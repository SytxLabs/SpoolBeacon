"""
Seed demo data: manufacturers, filaments, purchases, spools, shop links, snapshots, alerts.

Usage:
  python seed.py            # idempotent — skip existing records
  python seed.py --reset    # truncate all demo tables first, then seed fresh
"""
import asyncio
import sys
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import _build_database_url
from app.models.filament import Manufacturer, FilamentProduct
from app.models.purchase import Purchase, PurchaseLine
from app.models.shoplink import ShopLink
from app.models.price_snapshot import PriceSnapshot
from app.models.price_alert_event import PriceAlertEvent
from app.models.shop_rule import ShopRule
from app.models.spool import Spool, SpoolStatus, StorageStatus


# ── helpers ────────────────────────────────────────────────────────────────────

async def clear_tables(session: AsyncSession) -> None:
    """Delete all demo data in FK-safe order. Does NOT touch users or app_settings."""
    for model in (
        PriceAlertEvent, PriceSnapshot, ShopLink,
        Spool, PurchaseLine, Purchase,
        FilamentProduct, Manufacturer,
        ShopRule,
    ):
        await session.execute(delete(model))
    await session.commit()
    print("clear_tables: all demo tables truncated.")


async def upsert_manufacturer(session, name: str, website: str) -> Manufacturer:
    m = (await session.execute(
        select(Manufacturer).where(Manufacturer.name == name)
    )).scalar_one_or_none()
    if m:
        return m
    m = Manufacturer(name=name, website=website)
    session.add(m)
    await session.flush()
    return m


async def upsert_product(session, mfr_id: int, data: dict) -> tuple[FilamentProduct, bool]:
    p = (await session.execute(
        select(FilamentProduct).where(
            FilamentProduct.manufacturer_id == mfr_id,
            FilamentProduct.name == data["name"],
            FilamentProduct.material == data["material"],
            FilamentProduct.color_name == data["color_name"],
        )
    )).scalar_one_or_none()
    if p:
        return p, False
    p = FilamentProduct(
        manufacturer_id=mfr_id,
        name=data["name"],
        material=data["material"],
        color_name=data["color_name"],
        color_hex=data["color_hex"],
        diameter_mm=data.get("diameter_mm", 1.75),
        nominal_weight_g=data.get("nominal_weight_g", 1000),
        notes=data.get("notes"),
    )
    session.add(p)
    await session.flush()
    return p, True


async def upsert_shoplink(session, pid: int, data: dict) -> tuple[ShopLink, bool]:
    sl = (await session.execute(
        select(ShopLink).where(ShopLink.filament_product_id == pid, ShopLink.url == data["url"])
    )).scalar_one_or_none()
    if sl:
        return sl, False
    sl = ShopLink(
        filament_product_id=pid,
        shop_name=data["shop_name"],
        url=data["url"],
        currency=data.get("currency", "EUR"),
        package_weight_g=data.get("package_weight_g", 1000),
        manual_price=data["manual_price"],
        shipping_price=data.get("shipping_price"),
        target_price=data.get("target_price"),
        target_price_per_kg=data.get("target_price_per_kg"),
        is_active=data.get("is_active", True),
        notes=data.get("notes"),
    )
    session.add(sl)
    await session.flush()
    return sl, True


# ── seed ───────────────────────────────────────────────────────────────────────

async def seed(reset: bool = False) -> None:
    engine = create_async_engine(_build_database_url(), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.utcnow()

    async with factory() as session:

        if reset:
            await clear_tables(session)

        # ── Manufacturers ──────────────────────────────────────────────────────
        mfrs = {
            k: await upsert_manufacturer(session, k, v) for k, v in {
                "Bambu Lab":  "https://bambulab.com",
                "Elegoo":     "https://elegoo.com",
                "Polymaker":  "https://polymaker.com",
                "eSUN":       "https://esun3d.com",
                "Prusament":  "https://prusament.com",
                "Fiberlogy":  "https://fiberlogy.com",
                "Anycubic":   "https://www.anycubic.com",
            }.items()
        }

        # ── FilamentProducts ───────────────────────────────────────────────────
        # Indices (pi) used in raw_links / raw_purchases below:
        #  0 Elegoo Rapid PLA+ Black
        #  1 Bambu Lab PLA Basic White
        #  2 Polymaker PolyTerra Army Green
        #  3 eSUN ePETG Black
        #  4 Prusament PETG Prusa Orange
        #  5 Fiberlogy Easy PLA Gray
        #  6 Bambu Lab PLA Basic Black
        #  7 Prusament PLA Galaxy Black
        #  8 Anycubic PLA Basic White   (NEW)
        #  9 eSUN ePLA Pro White        (NEW)
        raw_products = [
            dict(m="Elegoo",    name="Rapid PLA+ Black",          material="PLA+", color_name="Black",        color_hex="#1A1A1A"),
            dict(m="Bambu Lab", name="PLA Basic White",           material="PLA",  color_name="White",        color_hex="#F5F5F0"),
            dict(m="Polymaker", name="PolyTerra PLA Army Green",  material="PLA",  color_name="Army Green",   color_hex="#4A5240"),
            dict(m="eSUN",      name="ePETG Black",               material="PETG", color_name="Black",        color_hex="#111111"),
            dict(m="Prusament", name="PETG Prusa Orange",         material="PETG", color_name="Prusa Orange", color_hex="#FA6831"),
            dict(m="Fiberlogy", name="Easy PLA Gray",             material="PLA",  color_name="Gray",         color_hex="#9E9E9E"),
            dict(m="Bambu Lab", name="PLA Basic Black",           material="PLA",  color_name="Black",        color_hex="#1A1A1A"),
            dict(m="Prusament", name="PLA Galaxy Black",          material="PLA",  color_name="Galaxy Black", color_hex="#1C1C2E"),
            dict(m="Anycubic",  name="PLA Basic White",           material="PLA",  color_name="White",        color_hex="#F0F0EE"),
            dict(m="eSUN",      name="ePLA Pro White",            material="PLA",  color_name="White",        color_hex="#FAFAFA",
                 notes="2-roll bundle available on esun3dstore.com."),
        ]
        products = []
        for rp in raw_products:
            p, _ = await upsert_product(session, mfrs[rp["m"]].id, rp)
            products.append(p)

        # ── Purchases + PurchaseLines + Spools ────────────────────────────────
        raw_purchases = [
            dict(
                shop_name="3DJake",
                order_number="3DJ-2025-11-0082",
                purchase_date=date(2025, 11, 4),
                shipping_price=4.90,
                currency="EUR",
                lines=[
                    dict(pi=1, qty=3, unit_price=12.49, spool_weight_g=1000, lot="BL-2025-W44",
                         spools=[
                             dict(status=SpoolStatus.geoeffnet, remaining=650, storage="Regal A",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2025, 11, 10)),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal A",
                                  storage_status=StorageStatus.verschlossen),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal A",
                                  storage_status=StorageStatus.verschlossen),
                         ]),
                    dict(pi=2, qty=2, unit_price=17.99, spool_weight_g=1000, lot="PM-AG-2025-38",
                         spools=[
                             dict(status=SpoolStatus.fast_leer, remaining=120, storage="Werkstatt",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2025, 11, 20)),
                             dict(status=SpoolStatus.geoeffnet, remaining=780, storage="Drybox 1",
                                  storage_status=StorageStatus.drybox, opened_at=datetime(2026, 1, 5)),
                         ]),
                ],
            ),
            dict(
                shop_name="Prusa Shop",
                order_number="PRS-EU-2025-34821",
                purchase_date=date(2025, 12, 18),
                shipping_price=6.00,
                currency="EUR",
                lines=[
                    dict(pi=4, qty=2, unit_price=29.99, spool_weight_g=1000, lot="PRS-PETG-OR-W50",
                         spools=[
                             dict(status=SpoolStatus.geoeffnet, remaining=410, storage="Regal B",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2025, 12, 28)),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Vakuumbox",
                                  storage_status=StorageStatus.vakuumiert),
                         ]),
                    dict(pi=7, qty=1, unit_price=29.99, spool_weight_g=1000, lot="PRS-PLA-GB-W50",
                         spools=[
                             dict(status=SpoolStatus.leer, remaining=0, storage="Regal B",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2026, 1, 2)),
                         ]),
                ],
            ),
            dict(
                shop_name="Elegoo Official",
                order_number="ELG-2026-00441",
                purchase_date=date(2026, 2, 12),
                shipping_price=0.00,
                currency="EUR",
                lines=[
                    dict(pi=0, qty=4, unit_price=18.99, spool_weight_g=1000, lot="ELG-RPLA-BK-0226",
                         spools=[
                             dict(status=SpoolStatus.geoeffnet, remaining=850, storage="Drybox 1",
                                  storage_status=StorageStatus.drybox, opened_at=datetime(2026, 2, 20)),
                             dict(status=SpoolStatus.geoeffnet, remaining=920, storage="Drybox 1",
                                  storage_status=StorageStatus.drybox, opened_at=datetime(2026, 3, 1)),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal A",
                                  storage_status=StorageStatus.vakuumiert),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal A",
                                  storage_status=StorageStatus.vakuumiert),
                         ]),
                ],
            ),
            dict(
                shop_name="Fiberlogy EU Store",
                order_number="FBG-EU-2026-1188",
                purchase_date=date(2026, 4, 3),
                shipping_price=5.50,
                currency="EUR",
                lines=[
                    dict(pi=5, qty=2, unit_price=21.90, spool_weight_g=850, lot="FBG-EPLA-GR-Q1-26",
                         spools=[
                             dict(status=SpoolStatus.geoeffnet, remaining=600, storage="Regal B",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2026, 4, 10)),
                             dict(status=SpoolStatus.neu, remaining=850, storage="Regal B",
                                  storage_status=StorageStatus.verschlossen),
                         ]),
                    dict(pi=3, qty=1, unit_price=22.90, spool_weight_g=1000, lot="FBG-PETG-BK-Q1-26",
                         spools=[
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal B",
                                  storage_status=StorageStatus.verschlossen),
                         ]),
                ],
            ),
            dict(
                shop_name="Anycubic Store",
                order_number="ANC-2026-09914",
                purchase_date=date(2026, 5, 20),
                shipping_price=0.00,
                currency="USD",
                lines=[
                    dict(pi=8, qty=3, unit_price=17.99, spool_weight_g=1000, lot="ANC-PLA-WH-Q2-26",
                         spools=[
                             dict(status=SpoolStatus.geoeffnet, remaining=900, storage="Regal C",
                                  storage_status=StorageStatus.offen, opened_at=datetime(2026, 5, 28)),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal C",
                                  storage_status=StorageStatus.verschlossen),
                             dict(status=SpoolStatus.neu, remaining=1000, storage="Regal C",
                                  storage_status=StorageStatus.verschlossen),
                         ]),
                ],
            ),
        ]

        ts_base = int(now.timestamp())
        line_seq = 0

        for rp in raw_purchases:
            existing_purchase = (await session.execute(
                select(Purchase).where(Purchase.order_number == rp["order_number"])
            )).scalar_one_or_none()
            if existing_purchase:
                continue

            purchase = Purchase(
                purchase_date=rp["purchase_date"],
                shop_name=rp["shop_name"],
                order_number=rp["order_number"],
                shipping_price=rp["shipping_price"],
                currency=rp["currency"],
            )
            session.add(purchase)
            await session.flush()

            for ld in rp["lines"]:
                line_seq += 1
                product = products[ld["pi"]]
                line = PurchaseLine(
                    purchase_id=purchase.id,
                    filament_product_id=product.id,
                    quantity=ld["qty"],
                    unit_price=ld["unit_price"],
                    currency=rp["currency"],
                    spool_weight_g=ld["spool_weight_g"],
                    lot_number=ld.get("lot"),
                )
                session.add(line)
                await session.flush()

                for si, sd in enumerate(ld["spools"]):
                    code = f"SB-{product.id}-{line.id}-{ts_base + line_seq}-{si + 1:02d}"
                    spool = Spool(
                        filament_product_id=product.id,
                        purchase_line_id=line.id,
                        spool_code=code,
                        status=sd["status"],
                        initial_weight_g=float(ld["spool_weight_g"]),
                        remaining_weight_g=float(sd["remaining"]),
                        storage_location=sd.get("storage"),
                        storage_status=sd.get("storage_status", StorageStatus.unbekannt),
                        opened_at=sd.get("opened_at"),
                    )
                    session.add(spool)

        await session.flush()

        # ── ShopLinks + PriceSnapshots + Alerts ───────────────────────────────
        raw_links = [
            # ── Elegoo Rapid PLA+ Black (pi=0) ──────────────────────────────
            dict(pi=0, shop_name="Elegoo Official", currency="EUR",
                 url="https://www.elegoo.com/products/elegoo-rapid-series-pla-plus",
                 package_weight_g=1000, manual_price=18.99, shipping_price=0.00,
                 target_price=17.00, is_active=False,
                 notes="Free shipping above 25 EUR.",
                 history=[
                     (90, 21.99, 0.0, "In Stock"), (60, 20.49, 0.0, "In Stock"),
                     (30, 19.49, 0.0, "In Stock"), (7,  18.99, 0.0, "In Stock"),
                     (1,  18.99, 0.0, "In Stock"),
                 ]),
            dict(pi=0, shop_name="Amazon DE", currency="EUR",
                 url="https://www.amazon.de/dp/B0CF35BLP5",
                 package_weight_g=1000, manual_price=19.89, shipping_price=0.00,
                 is_active=False,
                 notes="Blocked — returns empty page without authenticated browser session.",
                 history=[
                     (14, 21.99, 0.0, "In Stock"), (3, 19.89, 0.0, "In Stock"),
                 ]),

            # ── Bambu Lab PLA Basic White (pi=1) ─────────────────────────────
            dict(pi=1, shop_name="Bambu Lab EU Store", currency="EUR",
                 url="https://eu.store.bambulab.com/en/products/pla-basic-filament",
                 package_weight_g=1000, manual_price=22.99, shipping_price=None,
                 target_price=20.00, target_price_per_kg=20.00,
                 is_active=True,
                 notes="Cloudscraper adapter — eu.store.bambulab.com (NOT bambulab.com which is blocked).",
                 history=[
                     (75, 24.99, None, "In Stock"), (40, 23.99, None, "In Stock"),
                     (10, 22.99, None, "In Stock"), (2,  22.99, None, "In Stock"),
                 ]),
            dict(pi=1, shop_name="3DJake", currency="EUR",
                 url="https://www.3djake.de/bambu-lab/pla-basic-white",
                 package_weight_g=1000, manual_price=12.49, shipping_price=4.90,
                 target_price=18.00, is_active=True,
                 history=[
                     (60, 14.99, 4.90, "In Stock"), (30, 13.99, 4.90, "In Stock"),
                     (14, 13.49, 4.90, "In Stock"), (3,  12.49, 4.90, "In Stock"),
                 ],
                 alert_resolved=True),
            dict(pi=1, shop_name="Filamentworld", currency="EUR",
                 url="https://filamentworld.de/shop/filament-3d-drucker/bambu-lab-pla-basic-weiss-1-75mm/?switch_shop=b2c",
                 package_weight_g=1000, manual_price=19.90, shipping_price=4.90,
                 target_price=25.00, is_active=True,
                 notes="WooCommerce EUR. Use direct product URL — category pages may return 0,00€.",
                 history=[
                     (30, 22.90, 4.90, "In Stock"), (14, 21.90, 4.90, "In Stock"),
                     (5,  19.90, 4.90, "In Stock"),
                 ]),

            # ── Polymaker PolyTerra Army Green (pi=2) ────────────────────────
            dict(pi=2, shop_name="3DJake", currency="EUR",
                 url="https://www.3djake.de/polymaker/polyterra-pla-army-green",
                 package_weight_g=1000, manual_price=17.99, shipping_price=3.90,
                 target_price=22.00, is_active=True,
                 history=[
                     (45, 22.99, 3.90, "In Stock"), (20, 20.49, 3.90, "In Stock"),
                     (5,  17.99, 3.90, "In Stock"), (0,  None,  None, None),
                 ],
                 alert_active=True),
            dict(pi=2, shop_name="Polymaker Shop", currency="USD",
                 url="https://polymaker.com/product/polyterra-pla/",
                 package_weight_g=1000, manual_price=22.99, shipping_price=None,
                 is_active=False,
                 notes="Marketing/product-info site — no prices. Products sold via distributors.",
                 history=[
                     (30, 24.99, None, "In Stock"), (5, 22.99, None, "In Stock"),
                 ]),

            # ── Prusament PETG Prusa Orange (pi=4) ───────────────────────────
            dict(pi=4, shop_name="Prusa Shop", currency="EUR",
                 url="https://www.prusa3d.com/de/produkt/prusament-petg-prusa-orange-1kg/",
                 package_weight_g=1000, manual_price=29.99, shipping_price=6.00,
                 target_price=32.00, is_active=True,
                 history=[
                     (90, 34.99, 6.00, "In Stock"), (45, 32.99, 6.00, "In Stock"),
                     (14, 30.99, 6.00, "In Stock"), (3,  29.99, 6.00, "In Stock"),
                 ]),

            # ── Fiberlogy Easy PLA Gray (pi=5) ───────────────────────────────
            dict(pi=5, shop_name="eBay DE", currency="EUR",
                 url="https://www.ebay.de/itm/fiberlogy-easy-pla-gray",
                 package_weight_g=850, manual_price=19.50, shipping_price=3.90,
                 is_active=False,
                 notes="Blocked — eBay Cloudflare protection. Consider eBay Browse API.",
                 history=[
                     (20, 21.90, 3.90, "In Stock"), (8, 19.50, 3.90, "In Stock"),
                 ]),

            # ── Anycubic PLA Basic White (pi=8) ──────────────────────────────
            dict(pi=8, shop_name="Anycubic", currency="USD",
                 url="https://www.anycubic.com/products/pla-filament",
                 package_weight_g=1000, manual_price=17.99, shipping_price=None,
                 target_price=20.00, is_active=True,
                 notes="Shopify USD store. SSR — no JS required. Adapter available.",
                 history=[
                     (30, 22.99, None, "In Stock"), (14, 19.99, None, "In Stock"),
                     (5,  17.99, None, "In Stock"),
                 ]),

            # ── eSUN ePLA Pro White (pi=9) ────────────────────────────────────
            dict(pi=9, shop_name="eSUN Store", currency="USD",
                 url="https://esun3dstore.com/products/pla-pro-2-rolls",
                 package_weight_g=2000, manual_price=31.99, shipping_price=None,
                 target_price=38.00, is_active=True,
                 notes="2-roll bundle (2 kg). Cloudscraper adapter — esun3dstore.com. USD store.",
                 history=[
                     (20, 37.99, None, "In Stock"), (10, 34.99, None, "In Stock"),
                     (3,  31.99, None, "In Stock"),
                 ]),
        ]

        alert_count = 0
        snap_count = 0

        for rl in raw_links:
            product = products[rl["pi"]]
            sl, _ = await upsert_shoplink(session, product.id, rl)

            snap_exists = (await session.execute(
                select(PriceSnapshot).where(PriceSnapshot.shop_link_id == sl.id).limit(1)
            )).scalar_one_or_none()
            if snap_exists:
                continue

            last_snap = None
            for days_ago, price, ship, avail in sorted(
                rl.get("history", []), key=lambda x: x[0], reverse=True
            ):
                if price is None:
                    snap = PriceSnapshot(
                        shop_link_id=sl.id, price=0.0, currency=rl.get("currency", "EUR"),
                        captured_at=now - timedelta(days=days_ago, hours=2),
                        source="error", error_message="Connection timeout — selector returned no match.",
                    )
                else:
                    snap = PriceSnapshot(
                        shop_link_id=sl.id, price=price, shipping_price=ship,
                        currency=rl.get("currency", "EUR"), availability=avail,
                        captured_at=now - timedelta(days=days_ago), source="manual",
                    )
                    last_snap = snap
                session.add(snap)
                snap_count += 1

            await session.flush()

            if rl.get("alert_resolved") and last_snap:
                session.add(PriceAlertEvent(
                    shop_link_id=sl.id, price_snapshot_id=last_snap.id,
                    alert_type="target_price",
                    message=(
                        f"Target price hit: {sl.target_price:.2f} EUR — "
                        f"current total {last_snap.price + (last_snap.shipping_price or 0):.2f} EUR"
                    ),
                    created_at=now - timedelta(days=3),
                    resolved_at=now - timedelta(days=1),
                ))
                alert_count += 1

            if rl.get("alert_active") and last_snap:
                session.add(PriceAlertEvent(
                    shop_link_id=sl.id, price_snapshot_id=last_snap.id,
                    alert_type="target_price",
                    message=(
                        f"Target price hit: {sl.target_price:.2f} EUR — "
                        f"current total {last_snap.price + (last_snap.shipping_price or 0):.2f} EUR"
                    ),
                    created_at=now - timedelta(days=5),
                    resolved_at=None,
                ))
                alert_count += 1

        # ── ShopRules ─────────────────────────────────────────────────────────
        # Domains with a registered adapter (app/shop_adapters/registry.py) are
        # intentionally NOT seeded here — the adapter always takes priority over
        # a ShopRule for the same domain, so a rule for it would be dead weight
        # and confusing in the UI. See app.shop_adapters.registry.registered_domains().
        raw_rules = [
            dict(
                domain="filamentworld.de",
                price_selector=".price",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h1",
                availability_selector=".stock",
                currency="EUR",
                test_url="https://filamentworld.de/shop/filament-3d-drucker/pla-filament-1-75-mm-braun/?switch_shop=b2c",
                is_active=True,
                notes="WooCommerce EUR. Confirmed 2026-06-30.",
            ),
            # ── Blocked / inactive (reference only) ───────────────────────────
            dict(
                domain="bambulab.com",
                price_selector="[class*='price']",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h1",
                currency="EUR",
                test_url="https://bambulab.com/de-de/filament/pla-basic",
                is_active=False,
                notes="BLOCKED — Cloudflare WAF (httpx + Playwright + cloudscraper). Use eu.store.bambulab.com instead.",
            ),
            dict(
                domain="amazon.de",
                price_selector=".a-price .a-offscreen",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="#productTitle",
                availability_selector="#availability span",
                currency="EUR",
                test_url="",
                is_active=False,
                notes="BLOCKED — ASIN pages return 404 without session. Future: Amazon Product Advertising API.",
            ),
            dict(
                domain="ebay.de",
                price_selector=".x-price-primary .ux-textspans",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h1.x-item-title__mainTitle",
                currency="EUR",
                test_url="",
                is_active=False,
                notes="BLOCKED — Cloudflare (httpx + Playwright + cloudscraper). Future: eBay Browse API.",
            ),
            dict(
                domain="aliexpress.com",
                price_selector="[class*='price--current']",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h1",
                currency="EUR",
                test_url="",
                is_active=False,
                notes="JS-rendered — headless Playwright returns empty body (anti-bot fingerprinting).",
            ),
            dict(
                domain="polymaker.com",
                price_selector=".price-item--regular",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h2",
                currency="USD",
                test_url="https://polymaker.com/product/polyterra-pla/",
                is_active=False,
                notes="Marketing/product-info site only — no purchasable prices. Sold via distributors.",
            ),
            dict(
                domain="sunlu.com",
                price_selector="[class*='price']",
                price_regex=r"\d+[,\.]\d{2}",
                title_selector="h1",
                currency="USD",
                test_url="",
                is_active=False,
                notes="HTTP 500 on collection pages. Server instability or geo-blocking.",
            ),
        ]

        rule_count = 0
        for rr in raw_rules:
            existing = (await session.execute(
                select(ShopRule).where(ShopRule.domain == rr["domain"])
            )).scalar_one_or_none()
            if existing:
                continue
            session.add(ShopRule(**rr))
            rule_count += 1

        await session.commit()
        print(
            f"Seed complete — {len(mfrs)} manufacturers, {len(products)} products, "
            f"{len(raw_purchases)} purchases, {snap_count} snapshots, "
            f"{alert_count} alerts, {rule_count} shop rules seeded."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed(reset="--reset" in sys.argv))
