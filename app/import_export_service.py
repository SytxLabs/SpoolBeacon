"""Full inventory JSON export/import — additive-only upsert on import.

Excludes PriceSnapshot/PriceAlertEvent (derivable history) and User/AppSetting
(instance-specific/security-sensitive). Natural keys mirror seed.py's upsert
pattern so re-importing the same file is a no-op.
"""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.filament import Manufacturer, FilamentProduct
from app.models.purchase import Purchase, PurchaseLine
from app.models.shoplink import ShopLink
from app.models.shop_rule import ShopRule
from app.models.spool import Spool, SpoolStatus, StorageStatus


def _product_key(p: FilamentProduct) -> dict:
    return {
        "manufacturer": p.manufacturer.name,
        "name": p.name,
        "material": p.material,
        "color_name": p.color_name,
    }


def _spool_dict(s: Spool) -> dict:
    return {
        "spool_code": s.spool_code,
        "status": s.status.value,
        "initial_weight_g": s.initial_weight_g,
        "remaining_weight_g": s.remaining_weight_g,
        "storage_location": s.storage_location,
        "storage_status": s.storage_status.value,
        "opened_at": s.opened_at.isoformat() if s.opened_at else None,
        "last_dried_at": s.last_dried_at.isoformat() if s.last_dried_at else None,
        "last_weight_update_source": s.last_weight_update_source,
        "last_weight_update_at": s.last_weight_update_at.isoformat() if s.last_weight_update_at else None,
        "notes": s.notes,
    }


async def export_bundle(session) -> dict:
    manufacturers = (await session.execute(
        select(Manufacturer)
        .options(selectinload(Manufacturer.products).selectinload(FilamentProduct.shop_links))
        .order_by(Manufacturer.name)
    )).scalars().all()

    purchases = (await session.execute(
        select(Purchase)
        .options(
            selectinload(Purchase.lines).selectinload(PurchaseLine.filament_product)
                .selectinload(FilamentProduct.manufacturer),
            selectinload(Purchase.lines).selectinload(PurchaseLine.spools),
        )
        .order_by(Purchase.purchase_date, Purchase.id)
    )).scalars().all()

    orphan_spools = (await session.execute(
        select(Spool)
        .options(selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer))
        .where(Spool.purchase_line_id.is_(None))
        .order_by(Spool.spool_code)
    )).scalars().all()

    shop_rules = (await session.execute(select(ShopRule).order_by(ShopRule.domain))).scalars().all()

    return {
        "schema_version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "manufacturers": [
            {
                "name": m.name, "website": m.website, "notes": m.notes,
                "products": [
                    {
                        "name": p.name, "material": p.material, "color_name": p.color_name,
                        "color_hex": p.color_hex, "color_name_2": p.color_name_2, "color_hex_2": p.color_hex_2,
                        "diameter_mm": p.diameter_mm, "nominal_weight_g": p.nominal_weight_g, "notes": p.notes,
                        "shop_links": [
                            {
                                "shop_name": sl.shop_name, "url": sl.url, "currency": sl.currency,
                                "package_weight_g": sl.package_weight_g, "manual_price": sl.manual_price,
                                "shipping_price": sl.shipping_price, "is_active": sl.is_active,
                                "target_price": sl.target_price, "target_price_per_kg": sl.target_price_per_kg,
                                "notes": sl.notes,
                            }
                            for sl in p.shop_links
                        ],
                    }
                    for p in m.products
                ],
            }
            for m in manufacturers
        ],
        "purchases": [
            {
                "purchase_date": pu.purchase_date.isoformat(), "shop_name": pu.shop_name,
                "order_number": pu.order_number, "shipping_price": pu.shipping_price,
                "total_price": pu.total_price, "currency": pu.currency, "notes": pu.notes,
                "lines": [
                    {
                        "product_key": _product_key(ln.filament_product),
                        "quantity": ln.quantity, "unit_price": ln.unit_price, "currency": ln.currency,
                        "spool_weight_g": ln.spool_weight_g, "lot_number": ln.lot_number, "notes": ln.notes,
                        "spools": [_spool_dict(s) for s in ln.spools],
                    }
                    for ln in pu.lines
                ],
            }
            for pu in purchases
        ],
        "spools": [
            {**_spool_dict(s), "product_key": _product_key(s.filament_product)}
            for s in orphan_spools
        ],
        "shop_rules": [
            {
                "domain": r.domain, "price_selector": r.price_selector, "title_selector": r.title_selector,
                "availability_selector": r.availability_selector, "price_regex": r.price_regex,
                "availability_regex": r.availability_regex, "currency": r.currency, "test_url": r.test_url,
                "is_active": r.is_active, "notes": r.notes,
            }
            for r in shop_rules
        ],
    }


# ── import (additive-only upserts) ──────────────────────────────────────────

async def _upsert_manufacturer(session, data: dict) -> tuple[Manufacturer, bool]:
    m = (await session.execute(
        select(Manufacturer).where(Manufacturer.name == data["name"])
    )).scalar_one_or_none()
    if m:
        return m, False
    m = Manufacturer(name=data["name"], website=data.get("website"), notes=data.get("notes"))
    session.add(m)
    await session.flush()
    return m, True


async def _upsert_product(session, mfr_id: int, data: dict) -> tuple[FilamentProduct, bool]:
    p = (await session.execute(select(FilamentProduct).where(
        FilamentProduct.manufacturer_id == mfr_id,
        FilamentProduct.name == data["name"],
        FilamentProduct.material == data["material"],
        FilamentProduct.color_name == data["color_name"],
    ))).scalar_one_or_none()
    if p:
        return p, False
    p = FilamentProduct(
        manufacturer_id=mfr_id, name=data["name"], material=data["material"], color_name=data["color_name"],
        color_hex=data.get("color_hex"), color_name_2=data.get("color_name_2"), color_hex_2=data.get("color_hex_2"),
        diameter_mm=data.get("diameter_mm", 1.75), nominal_weight_g=data.get("nominal_weight_g", 1000),
        notes=data.get("notes"),
    )
    session.add(p)
    await session.flush()
    return p, True


async def _upsert_shop_link(session, product_id: int, data: dict) -> tuple[ShopLink, bool]:
    sl = (await session.execute(select(ShopLink).where(
        ShopLink.filament_product_id == product_id, ShopLink.url == data["url"],
    ))).scalar_one_or_none()
    if sl:
        return sl, False
    sl = ShopLink(
        filament_product_id=product_id, shop_name=data["shop_name"], url=data["url"],
        currency=data.get("currency", "EUR"), package_weight_g=data["package_weight_g"],
        manual_price=data["manual_price"], shipping_price=data.get("shipping_price"),
        is_active=data.get("is_active", True), target_price=data.get("target_price"),
        target_price_per_kg=data.get("target_price_per_kg"), notes=data.get("notes"),
    )
    session.add(sl)
    await session.flush()
    return sl, True


async def _upsert_purchase(session, data: dict) -> tuple[Purchase, bool]:
    pu_date = date.fromisoformat(data["purchase_date"])
    pu = (await session.execute(select(Purchase).where(
        Purchase.purchase_date == pu_date, Purchase.shop_name == data["shop_name"],
        Purchase.order_number == data.get("order_number"),
    ))).scalar_one_or_none()
    if pu:
        return pu, False
    pu = Purchase(
        purchase_date=pu_date, shop_name=data["shop_name"], order_number=data.get("order_number"),
        shipping_price=data.get("shipping_price"), total_price=data.get("total_price"),
        currency=data.get("currency", "EUR"), notes=data.get("notes"),
    )
    session.add(pu)
    await session.flush()
    return pu, True


async def _upsert_purchase_line(session, purchase_id: int, product_id: int, data: dict) -> tuple[PurchaseLine, bool]:
    ln = (await session.execute(select(PurchaseLine).where(
        PurchaseLine.purchase_id == purchase_id, PurchaseLine.filament_product_id == product_id,
        PurchaseLine.lot_number == data.get("lot_number"),
    ))).scalar_one_or_none()
    if ln:
        return ln, False
    ln = PurchaseLine(
        purchase_id=purchase_id, filament_product_id=product_id, quantity=data.get("quantity", 1),
        unit_price=data["unit_price"], currency=data.get("currency", "EUR"),
        spool_weight_g=data.get("spool_weight_g", 1000), lot_number=data.get("lot_number"),
        notes=data.get("notes"),
    )
    session.add(ln)
    await session.flush()
    return ln, True


async def _upsert_spool(session, product_id: int, purchase_line_id: int | None, data: dict) -> tuple[Spool, bool]:
    sp = (await session.execute(
        select(Spool).where(Spool.spool_code == data["spool_code"])
    )).scalar_one_or_none()
    if sp:
        return sp, False
    sp = Spool(
        filament_product_id=product_id, purchase_line_id=purchase_line_id, spool_code=data["spool_code"],
        status=SpoolStatus(data.get("status", "new")), initial_weight_g=data["initial_weight_g"],
        remaining_weight_g=data["remaining_weight_g"], storage_location=data.get("storage_location"),
        storage_status=StorageStatus(data.get("storage_status", "unknown")),
        opened_at=datetime.fromisoformat(data["opened_at"]) if data.get("opened_at") else None,
        last_dried_at=datetime.fromisoformat(data["last_dried_at"]) if data.get("last_dried_at") else None,
        last_weight_update_source=data.get("last_weight_update_source"),
        last_weight_update_at=datetime.fromisoformat(data["last_weight_update_at"]) if data.get("last_weight_update_at") else None,
        notes=data.get("notes"),
    )
    session.add(sp)
    await session.flush()
    return sp, True


async def _upsert_shop_rule(session, data: dict) -> tuple[ShopRule, bool]:
    r = (await session.execute(
        select(ShopRule).where(ShopRule.domain == data["domain"])
    )).scalar_one_or_none()
    if r:
        return r, False
    r = ShopRule(
        domain=data["domain"], price_selector=data.get("price_selector"), title_selector=data.get("title_selector"),
        availability_selector=data.get("availability_selector"), price_regex=data.get("price_regex"),
        availability_regex=data.get("availability_regex"), currency=data.get("currency", "EUR"),
        test_url=data.get("test_url"), is_active=data.get("is_active", True), notes=data.get("notes"),
    )
    session.add(r)
    await session.flush()
    return r, True


async def _resolve_product(session, key: dict) -> FilamentProduct | None:
    mfr = (await session.execute(
        select(Manufacturer).where(Manufacturer.name == key["manufacturer"])
    )).scalar_one_or_none()
    if not mfr:
        return None
    return (await session.execute(select(FilamentProduct).where(
        FilamentProduct.manufacturer_id == mfr.id, FilamentProduct.name == key["name"],
        FilamentProduct.material == key["material"], FilamentProduct.color_name == key["color_name"],
    ))).scalar_one_or_none()


async def import_bundle(session, data: dict) -> dict[str, tuple[int, int]]:
    """Walk the export schema and additively upsert everything. Returns {entity: (added, skipped)}."""
    counts = {k: [0, 0] for k in
              ("manufacturers", "products", "shop_links", "purchases", "purchase_lines", "spools", "shop_rules")}

    for m_data in data.get("manufacturers", []):
        mfr, created = await _upsert_manufacturer(session, m_data)
        counts["manufacturers"][0 if created else 1] += 1
        for p_data in m_data.get("products", []):
            product, created = await _upsert_product(session, mfr.id, p_data)
            counts["products"][0 if created else 1] += 1
            for sl_data in p_data.get("shop_links", []):
                _, created = await _upsert_shop_link(session, product.id, sl_data)
                counts["shop_links"][0 if created else 1] += 1

    for pu_data in data.get("purchases", []):
        purchase, created = await _upsert_purchase(session, pu_data)
        counts["purchases"][0 if created else 1] += 1
        for ln_data in pu_data.get("lines", []):
            product = await _resolve_product(session, ln_data["product_key"])
            if not product:
                continue
            line, created = await _upsert_purchase_line(session, purchase.id, product.id, ln_data)
            counts["purchase_lines"][0 if created else 1] += 1
            for sp_data in ln_data.get("spools", []):
                _, created = await _upsert_spool(session, product.id, line.id, sp_data)
                counts["spools"][0 if created else 1] += 1

    for sp_data in data.get("spools", []):
        product = await _resolve_product(session, sp_data["product_key"])
        if not product:
            continue
        _, created = await _upsert_spool(session, product.id, None, sp_data)
        counts["spools"][0 if created else 1] += 1

    for r_data in data.get("shop_rules", []):
        _, created = await _upsert_shop_rule(session, r_data)
        counts["shop_rules"][0 if created else 1] += 1

    return {k: tuple(v) for k, v in counts.items()}
