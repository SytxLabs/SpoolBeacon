import datetime
import re
from urllib.parse import urlparse

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required
from sqlalchemy import or_, select, func
from sqlalchemy.orm import selectinload, contains_eager

from app.database import get_db
from app.models.filament import FilamentProduct, Manufacturer
from app.models.price_snapshot import PriceSnapshot
from app.models.purchase import Purchase, PurchaseLine
from app.models.shop_rule import ShopRule
from app.models.shoplink import ShopLink
from app.models.spool import Spool, SpoolStatus, StorageStatus
from app.alert_service import maybe_create_alerts, dispatch_alert_notifications
from app.models.price_alert_event import PriceAlertEvent
from app.price_check_service import check_price
from app.settings_service import get_all as get_settings
from app.spool_code import generate_spool_code, DEFAULT_TEMPLATE

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── target price hits helper ───────────────────────────────────────────────

async def load_target_hits(session) -> list[dict]:
    """Return ShopLinks whose latest non-error snapshot hits target_price or target_price_per_kg."""
    links = (await session.execute(
        select(ShopLink)
        .options(selectinload(ShopLink.filament_product))
        .where(
            ShopLink.is_active == True,
            or_(ShopLink.target_price.isnot(None), ShopLink.target_price_per_kg.isnot(None)),
        )
    )).scalars().all()

    if not links:
        return []

    link_ids = [lnk.id for lnk in links]
    sub = (
        select(PriceSnapshot.shop_link_id, func.max(PriceSnapshot.captured_at).label("max_at"))
        .where(PriceSnapshot.shop_link_id.in_(link_ids), PriceSnapshot.source != "error")
        .group_by(PriceSnapshot.shop_link_id)
        .subquery()
    )
    snaps = (await session.execute(
        select(PriceSnapshot).join(
            sub,
            (PriceSnapshot.shop_link_id == sub.c.shop_link_id)
            & (PriceSnapshot.captured_at == sub.c.max_at),
        )
    )).scalars().all()
    snap_by_link = {s.shop_link_id: s for s in snaps}

    hits = []
    for lnk in links:
        snap = snap_by_link.get(lnk.id)
        if not snap:
            continue
        snap_total = snap.price + (snap.shipping_price or 0.0)
        snap_per_kg = round(snap_total / lnk.package_weight_g * 1000, 2) if lnk.package_weight_g else None

        hit_price = lnk.target_price is not None and snap_total <= lnk.target_price
        hit_kg = (lnk.target_price_per_kg is not None and snap_per_kg is not None
                  and snap_per_kg <= lnk.target_price_per_kg)

        if hit_price or hit_kg:
            hits.append({
                "link": lnk,
                "snap": snap,
                "snap_total": snap_total,
                "snap_per_kg": snap_per_kg,
                "hit_price": hit_price,
                "hit_kg": hit_kg,
                "product_id": lnk.filament_product_id,
                "product_name": lnk.filament_product.name if lnk.filament_product else "–",
            })
    return hits


# ── helpers ────────────────────────────────────────────────────────────────

async def _load_manufacturers(session):
    return (await session.execute(
        select(Manufacturer).order_by(Manufacturer.name)
    )).scalars().all()


async def _manufacturer_id_for_check(session, form) -> int | None:
    """Resolve manufacturer_id for duplicate check WITHOUT creating anything."""
    mfr_id = form.get("manufacturer_id", "").strip()
    if mfr_id == "new":
        name = form.get("new_manufacturer_name", "").strip()
        if not name:
            return None
        existing = (await session.execute(
            select(Manufacturer).where(Manufacturer.name == name)
        )).scalar_one_or_none()
        return existing.id if existing else None  # new mfr → no possible dup
    return int(mfr_id) if mfr_id.isdigit() else None


async def _find_duplicate(session, form, mfr_id: int | None, exclude_id: int | None = None):
    if mfr_id is None:
        return None
    try:
        diameter = float(form.get("diameter_mm", "1.75"))
    except ValueError:
        return None
    q = select(FilamentProduct).where(
        FilamentProduct.manufacturer_id == mfr_id,
        FilamentProduct.name == form.get("name", "").strip(),
        FilamentProduct.material == form.get("material", "").strip(),
        FilamentProduct.color_name == form.get("color_name", "").strip(),
        FilamentProduct.diameter_mm == diameter,
    )
    if exclude_id is not None:
        q = q.where(FilamentProduct.id != exclude_id)
    return (await session.execute(q)).scalar_one_or_none()


# ── list ───────────────────────────────────────────────────────────────────

@inventory_bp.get("/")
@login_required
async def index():
    material_filter = request.args.get("material", "")

    async with get_db() as session:
        materials_result = await session.execute(
            select(FilamentProduct.material).distinct().order_by(FilamentProduct.material)
        )
        materials = [r[0] for r in materials_result.all()]

        q = (
            select(
                FilamentProduct,
                Manufacturer.name.label("manufacturer_name"),
                func.count(Spool.id).label("spool_count"),
                func.coalesce(func.sum(Spool.remaining_weight_g), 0).label("total_remaining_g"),
            )
            .join(Manufacturer, FilamentProduct.manufacturer_id == Manufacturer.id)
            .outerjoin(
                Spool,
                (Spool.filament_product_id == FilamentProduct.id)
                & (Spool.status != SpoolStatus.archived),
            )
            .group_by(FilamentProduct.id, Manufacturer.name)
            .order_by(Manufacturer.name, FilamentProduct.material, FilamentProduct.name)
        )

        if material_filter:
            q = q.where(FilamentProduct.material == material_filter)

        products = (await session.execute(q)).all()
        target_hits = await load_target_hits(session)

        alert_rows = (await session.execute(
            select(ShopLink.filament_product_id, func.count(PriceAlertEvent.id).label("n"))
            .join(PriceAlertEvent, PriceAlertEvent.shop_link_id == ShopLink.id)
            .where(PriceAlertEvent.resolved_at.is_(None))
            .group_by(ShopLink.filament_product_id)
        )).all()
        alert_counts_by_product: dict[int, int] = {row.filament_product_id: row.n for row in alert_rows}

    target_hit_product_ids = {h["product_id"] for h in target_hits}

    return await render_template(
        "inventory/index.html",
        products=products,
        materials=materials,
        current_material=material_filter,
        target_hit_product_ids=target_hit_product_ids,
        alert_counts_by_product=alert_counts_by_product,
    )


# ── detail ─────────────────────────────────────────────────────────────────

@inventory_bp.get("/<int:product_id>")
@login_required
async def detail(product_id: int):
    async with get_db() as session:
        product = (await session.execute(
            select(FilamentProduct)
            .options(selectinload(FilamentProduct.manufacturer))
            .where(FilamentProduct.id == product_id)
        )).scalar_one_or_none()

        if not product:
            abort(404)

        spools = (await session.execute(
            select(Spool)
            .options(selectinload(Spool.purchase_line))
            .where(Spool.filament_product_id == product_id)
            .order_by(Spool.status, Spool.spool_code)
        )).scalars().all()

        purchase_lines = (await session.execute(
            select(PurchaseLine)
            .join(Purchase, PurchaseLine.purchase_id == Purchase.id)
            .options(contains_eager(PurchaseLine.purchase))
            .where(PurchaseLine.filament_product_id == product_id)
            .order_by(Purchase.purchase_date.desc())
        )).scalars().all()

        _price_per_kg_expr = (
            (ShopLink.manual_price + func.coalesce(ShopLink.shipping_price, 0.0))
            / ShopLink.package_weight_g
        )
        shop_links = (await session.execute(
            select(ShopLink)
            .where(ShopLink.filament_product_id == product_id)
            .order_by(ShopLink.is_active.desc(), _price_per_kg_expr, ShopLink.shop_name)
        )).scalars().all()

        # latest PriceSnapshot per shop_link
        latest_snapshots: dict[int, PriceSnapshot] = {}
        sl_ids = [sl.id for sl in shop_links]
        if sl_ids:
            sub = (
                select(
                    PriceSnapshot.shop_link_id,
                    func.max(PriceSnapshot.captured_at).label("max_at"),
                )
                .where(PriceSnapshot.shop_link_id.in_(sl_ids))
                .group_by(PriceSnapshot.shop_link_id)
                .subquery()
            )
            snaps = (await session.execute(
                select(PriceSnapshot).join(
                    sub,
                    (PriceSnapshot.shop_link_id == sub.c.shop_link_id)
                    & (PriceSnapshot.captured_at == sub.c.max_at),
                )
            )).scalars().all()
            latest_snapshots = {s.shop_link_id: s for s in snaps}

        # which shop_link IDs have an active ShopRule or registered adapter → drives Check button
        checkable_link_ids: set[int] = set()
        adapter_link_ids: set[int] = set()   # subset with registered adapter
        rule_link_ids: set[int] = set()      # subset with active rule
        if shop_links:
            from app.shop_adapters.registry import get_adapter as _get_adapter
            domain_to_ids: dict[str, list[int]] = {}
            for sl in shop_links:
                host = urlparse(sl.url).hostname or ""
                domain = host.removeprefix("www.")
                domain_to_ids.setdefault(domain, []).append(sl.id)
                if _get_adapter(domain):
                    adapter_link_ids.update([sl.id])

            active_domains = (await session.execute(
                select(ShopRule.domain).where(
                    ShopRule.domain.in_(domain_to_ids.keys()),
                    ShopRule.is_active == True,
                )
            )).scalars().all()

            for d in active_domains:
                rule_link_ids.update(domain_to_ids[d])

            checkable_link_ids = adapter_link_ids | rule_link_ids

    active_spools = [s for s in spools if s.status != SpoolStatus.archived]
    total_remaining_g = sum(s.remaining_weight_g for s in active_spools)
    inventory_value = sum(
        s.remaining_weight_g / s.purchase_line.spool_weight_g * s.purchase_line.unit_price
        for s in active_spools
        if s.purchase_line and s.purchase_line.spool_weight_g
    )
    avg_unit_price = (
        sum(ln.unit_price for ln in purchase_lines) / len(purchase_lines)
        if purchase_lines else None
    )
    currency = purchase_lines[0].currency if purchase_lines else "EUR"

    spool_counts_by_line: dict[int, int] = {}
    for s in spools:
        if s.purchase_line_id:
            spool_counts_by_line[s.purchase_line_id] = spool_counts_by_line.get(s.purchase_line_id, 0) + 1

    return await render_template(
        "inventory/detail.html",
        product=product,
        spools=spools,
        purchase_lines=purchase_lines,
        shop_links=shop_links,
        latest_snapshots=latest_snapshots,
        checkable_link_ids=checkable_link_ids,
        adapter_link_ids=adapter_link_ids,
        rule_link_ids=rule_link_ids,
        active_spool_count=len(active_spools),
        total_remaining_g=total_remaining_g,
        inventory_value=inventory_value,
        avg_unit_price=avg_unit_price,
        currency=currency,
        spool_counts_by_line=spool_counts_by_line,
    )


# ── filament create ────────────────────────────────────────────────────────

@inventory_bp.route("/filament/new", methods=["GET", "POST"])
@login_required
async def filament_new():
    async with get_db() as session:
        manufacturers = await _load_manufacturers(session)

        if request.method == "GET":
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=None,
                form_data=None,
            )

        form = await request.form
        error = _validate_filament_form(form)

        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=None,
                form_data=form,
            )

        check_mfr_id = await _manufacturer_id_for_check(session, form)
        dup = await _find_duplicate(session, form, check_mfr_id)
        if dup:
            await flash(
                f'This filament already exists: "{dup.name}" ({dup.material}, {dup.color_name}, {dup.diameter_mm} mm)',
                "error",
            )
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=None,
                form_data=form,
            )

        manufacturer_id = await _resolve_manufacturer(session, form)
        if manufacturer_id is None:
            await flash("Manufacturer missing or name empty.", "error")
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=None,
                form_data=form,
            )

        product = FilamentProduct(
            manufacturer_id=manufacturer_id,
            **_filament_fields(form),
        )
        session.add(product)
        await session.flush()
        product_id = product.id

    return redirect(url_for("inventory.detail", product_id=product_id))


# ── filament edit ──────────────────────────────────────────────────────────

@inventory_bp.route("/filament/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
async def filament_edit(product_id: int):
    async with get_db() as session:
        product = (await session.execute(
            select(FilamentProduct)
            .options(selectinload(FilamentProduct.manufacturer))
            .where(FilamentProduct.id == product_id)
        )).scalar_one_or_none()

        if not product:
            abort(404)

        manufacturers = await _load_manufacturers(session)

        if request.method == "GET":
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=product,
                form_data=None,
            )

        form = await request.form
        error = _validate_filament_form(form)

        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=product,
                form_data=form,
            )

        check_mfr_id = await _manufacturer_id_for_check(session, form)
        dup = await _find_duplicate(session, form, check_mfr_id, exclude_id=product_id)
        if dup:
            await flash(
                f'Another filament with this data already exists: "{dup.name}" ({dup.material}, {dup.color_name}, {dup.diameter_mm} mm)',
                "error",
            )
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=product,
                form_data=form,
            )

        manufacturer_id = await _resolve_manufacturer(session, form)
        if manufacturer_id is None:
            await flash("Manufacturer missing or name empty.", "error")
            return await render_template(
                "inventory/filament_form.html",
                manufacturers=manufacturers,
                product=product,
                form_data=form,
            )

        product.manufacturer_id = manufacturer_id
        for k, v in _filament_fields(form).items():
            setattr(product, k, v)

    return redirect(url_for("inventory.detail", product_id=product_id))


# ── filament delete ────────────────────────────────────────────────────────

@inventory_bp.post("/filament/<int:product_id>/delete")
@login_required
async def filament_delete(product_id: int):
    async with get_db() as session:
        product = await session.get(FilamentProduct, product_id)
        if not product:
            abort(404)

        spool_count = await session.scalar(
            select(func.count(Spool.id)).where(Spool.filament_product_id == product_id)
        ) or 0
        line_count = await session.scalar(
            select(func.count(PurchaseLine.id))
            .where(PurchaseLine.filament_product_id == product_id)
        ) or 0

        if spool_count or line_count:
            await flash(
                f"Cannot delete: {spool_count} spool(s) and {line_count} purchase line(s) linked.",
                "error",
            )
            return redirect(url_for("inventory.detail", product_id=product_id))

        await session.delete(product)

    return redirect(url_for("inventory.index"))


# ── spool creation from purchase line ─────────────────────────────────────

@inventory_bp.post("/purchase-line/<int:line_id>/create-spools")
@login_required
async def create_spools_from_line(line_id: int):
    async with get_db() as session:
        line = (await session.execute(
            select(PurchaseLine)
            .options(selectinload(PurchaseLine.filament_product))
            .where(PurchaseLine.id == line_id)
        )).scalar_one_or_none()

        if not line:
            abort(404)

        existing_count = await session.scalar(
            select(func.count(Spool.id)).where(Spool.filament_product_id == line.filament_product_id)
        ) or 0

        to_create = line.quantity - existing_count
        product_id_out = line.filament_product_id

        if to_create <= 0:
            await flash("All spools for this purchase line already exist.", "info")
        else:
            settings = await get_settings(session)
            template = settings.get("spool.code_template", DEFAULT_TEMPLATE)
            now = datetime.datetime.utcnow()
            for i in range(to_create):
                code = generate_spool_code(
                    template,
                    product_id=line.filament_product_id,
                    line_id=line_id,
                    seq=existing_count + i + 1,
                    now=now,
                )
                session.add(Spool(
                    filament_product_id=line.filament_product_id,
                    purchase_line_id=line_id,
                    spool_code=code,
                    status=SpoolStatus.new,
                    initial_weight_g=float(line.spool_weight_g),
                    remaining_weight_g=float(line.spool_weight_g),
                    storage_status=StorageStatus.unknown,
                ))
            await flash(f"{to_create} spool(s) created.", "success")

    return redirect(url_for("inventory.detail", product_id=product_id_out))


# ── purchase new ──────────────────────────────────────────────────────────

def _validate_purchase_form(form) -> str | None:
    if not form.get("purchase_date", "").strip():
        return "Purchase date is required."
    if not form.get("shop_name", "").strip():
        return "Shop name is required."
    try:
        q = int(form.get("quantity", "0"))
        if q <= 0:
            return "Quantity must be at least 1."
        float(form.get("unit_price", ""))
        w = int(form.get("spool_weight_g", "0"))
        if w <= 0:
            return "Spool weight must be greater than 0."
        s = form.get("shipping_price", "").strip()
        if s:
            float(s)
    except (ValueError, TypeError):
        return "Invalid number."
    return None


@inventory_bp.route("/<int:product_id>/purchase/new", methods=["GET", "POST"])
@login_required
async def purchase_new(product_id: int):
    async with get_db() as session:
        product = await session.get(FilamentProduct, product_id)
        if not product:
            abort(404)

        if request.method == "GET":
            return await render_template(
                "inventory/purchase_form.html",
                product=product,
                form_data=None,
                default_weight=product.nominal_weight_g,
            )

        form = await request.form
        error = _validate_purchase_form(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/purchase_form.html",
                product=product,
                form_data=form,
                default_weight=product.nominal_weight_g,
            )

        currency = form.get("currency", "EUR").strip() or "EUR"
        ship_raw = form.get("shipping_price", "").strip()
        purchase = Purchase(
            purchase_date=datetime.date.fromisoformat(form.get("purchase_date")),
            shop_name=form.get("shop_name", "").strip(),
            order_number=form.get("order_number", "").strip() or None,
            shipping_price=float(ship_raw) if ship_raw else None,
            currency=currency,
            notes=form.get("purchase_notes", "").strip() or None,
        )
        session.add(purchase)
        await session.flush()

        quantity = int(form.get("quantity", "1"))
        spool_weight = int(form.get("spool_weight_g", "1000"))
        line = PurchaseLine(
            purchase_id=purchase.id,
            filament_product_id=product_id,
            quantity=quantity,
            unit_price=float(form.get("unit_price")),
            currency=currency,
            spool_weight_g=spool_weight,
            lot_number=form.get("lot_number", "").strip() or None,
            notes=form.get("line_notes", "").strip() or None,
        )
        session.add(line)
        await session.flush()

        settings = await get_settings(session)
        template = settings.get("spool.code_template", DEFAULT_TEMPLATE)
        now = datetime.datetime.utcnow()
        existing_for_seq = await session.scalar(
            select(func.count(Spool.id)).where(Spool.filament_product_id == product_id)
        ) or 0
        for i in range(quantity):
            code = generate_spool_code(
                template,
                product_id=product_id,
                line_id=line.id,
                seq=existing_for_seq + i + 1,
                now=now,
            )
            session.add(Spool(
                filament_product_id=product_id,
                purchase_line_id=line.id,
                spool_code=code,
                status=SpoolStatus.new,
                initial_weight_g=float(spool_weight),
                remaining_weight_g=float(spool_weight),
                storage_status=StorageStatus.unknown,
            ))

    await flash(f"Purchase saved. {quantity} spool(s) created.", "success")
    return redirect(url_for("inventory.detail", product_id=product_id))


# ── purchase edit ─────────────────────────────────────────────────────────

def _validate_purchase_edit_form(
    form, can_edit_quantity: bool, can_edit_weight: bool
) -> str | None:
    if not form.get("purchase_date", "").strip():
        return "Purchase date is required."
    if not form.get("shop_name", "").strip():
        return "Shop name is required."
    try:
        float(form.get("unit_price", ""))
    except (ValueError, TypeError):
        return "Invalid unit price."
    if can_edit_quantity:
        try:
            q = int(form.get("quantity", "0"))
            if q <= 0:
                return "Quantity must be at least 1."
        except (ValueError, TypeError):
            return "Invalid quantity."
    if can_edit_weight:
        try:
            w = int(form.get("spool_weight_g", "0"))
            if w <= 0:
                return "Spool weight must be greater than 0."
        except (ValueError, TypeError):
            return "Invalid spool weight."
    s = form.get("shipping_price", "").strip()
    if s:
        try:
            float(s)
        except ValueError:
            return "Invalid shipping cost."
    return None


@inventory_bp.route("/<int:product_id>/purchase/<int:line_id>/edit", methods=["GET", "POST"])
@login_required
async def purchase_edit(product_id: int, line_id: int):
    async with get_db() as session:
        line = (await session.execute(
            select(PurchaseLine)
            .options(selectinload(PurchaseLine.purchase), selectinload(PurchaseLine.spools))
            .where(
                PurchaseLine.id == line_id,
                PurchaseLine.filament_product_id == product_id,
            )
        )).scalar_one_or_none()
        if not line:
            abort(404)

        product = await session.get(FilamentProduct, product_id)
        spools_of_line = line.spools  # already loaded

        spool_count = len(spools_of_line)
        all_unused = spool_count > 0 and all(
            s.status == SpoolStatus.new and s.remaining_weight_g == s.initial_weight_g
            for s in spools_of_line
        )
        can_edit_quantity = spool_count == 0
        can_edit_weight = spool_count == 0 or all_unused

        if request.method == "GET":
            prefill = {
                "purchase_date": line.purchase.purchase_date.isoformat(),
                "shop_name": line.purchase.shop_name,
                "order_number": line.purchase.order_number or "",
                "shipping_price": (
                    str(line.purchase.shipping_price)
                    if line.purchase.shipping_price is not None else ""
                ),
                "currency": line.purchase.currency,
                "purchase_notes": line.purchase.notes or "",
                "unit_price": str(line.unit_price),
                "lot_number": line.lot_number or "",
                "line_notes": line.notes or "",
                "quantity": str(line.quantity),
                "spool_weight_g": str(line.spool_weight_g),
            }
            return await render_template(
                "inventory/purchase_form.html",
                product=product,
                form_data=prefill,
                default_weight=product.nominal_weight_g,
                editing=True,
                line_id=line_id,
                can_edit_quantity=can_edit_quantity,
                can_edit_weight=can_edit_weight,
                spool_count=spool_count,
            )

        form = await request.form
        error = _validate_purchase_edit_form(form, can_edit_quantity, can_edit_weight)
        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/purchase_form.html",
                product=product,
                form_data=form,
                default_weight=product.nominal_weight_g,
                editing=True,
                line_id=line_id,
                can_edit_quantity=can_edit_quantity,
                can_edit_weight=can_edit_weight,
                spool_count=spool_count,
            )

        currency = form.get("currency", line.purchase.currency).strip() or line.purchase.currency
        ship_raw = form.get("shipping_price", "").strip()

        # update Purchase header
        line.purchase.purchase_date = datetime.date.fromisoformat(form.get("purchase_date"))
        line.purchase.shop_name = form.get("shop_name", "").strip()
        line.purchase.order_number = form.get("order_number", "").strip() or None
        line.purchase.shipping_price = float(ship_raw) if ship_raw else None
        line.purchase.currency = currency
        line.purchase.notes = form.get("purchase_notes", "").strip() or None

        # update PurchaseLine
        line.unit_price = float(form.get("unit_price"))
        line.currency = currency
        line.lot_number = form.get("lot_number", "").strip() or None
        line.notes = form.get("line_notes", "").strip() or None

        if can_edit_quantity:
            line.quantity = int(form.get("quantity", line.quantity))

        if can_edit_weight:
            new_weight = int(form.get("spool_weight_g", line.spool_weight_g))
            if new_weight != line.spool_weight_g:
                line.spool_weight_g = new_weight
                for s in spools_of_line:
                    if s.status == SpoolStatus.new and s.remaining_weight_g == s.initial_weight_g:
                        s.initial_weight_g = float(new_weight)
                        s.remaining_weight_g = float(new_weight)

    return redirect(url_for("inventory.detail", product_id=product_id))


# ── spool edit ─────────────────────────────────────────────────────────────

@inventory_bp.route("/<int:product_id>/spool/<int:spool_id>/edit", methods=["GET", "POST"])
@login_required
async def spool_edit(product_id: int, spool_id: int):
    async with get_db() as session:
        spool = await session.get(Spool, spool_id)
        if not spool or spool.filament_product_id != product_id:
            abort(404)
        product = await session.get(FilamentProduct, product_id)

        if request.method == "GET":
            return await render_template(
                "inventory/spool_form.html",
                product=product,
                spool=spool,
            )

        form = await request.form
        try:
            remaining = float(form.get("remaining_weight_g", ""))
            if remaining < 0:
                raise ValueError
        except (ValueError, TypeError):
            await flash("Invalid remaining weight.", "error")
            return await render_template(
                "inventory/spool_form.html",
                product=product,
                spool=spool,
            )

        spool.status = SpoolStatus(form.get("status", SpoolStatus.new.value))
        if remaining != spool.remaining_weight_g:
            spool.remaining_weight_g = remaining
            spool.last_weight_update_at = datetime.datetime.utcnow()
            spool.last_weight_update_source = "manual"
        spool.storage_location = form.get("storage_location", "").strip() or None
        spool.storage_status = StorageStatus(
            form.get("storage_status", StorageStatus.unknown.value)
        )
        spool.notes = form.get("notes", "").strip() or None

        opened_raw = form.get("opened_at", "").strip()
        spool.opened_at = datetime.datetime.fromisoformat(opened_raw) if opened_raw else None
        dried_raw = form.get("last_dried_at", "").strip()
        spool.last_dried_at = datetime.datetime.fromisoformat(dried_raw) if dried_raw else None

    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.post("/<int:product_id>/spool/<int:spool_id>/delete")
@login_required
async def spool_delete(product_id: int, spool_id: int):
    async with get_db() as session:
        spool = await session.get(Spool, spool_id)
        if not spool or spool.filament_product_id != product_id:
            abort(404)
        await session.delete(spool)

    return redirect(url_for("inventory.detail", product_id=product_id))


# ── form helpers ───────────────────────────────────────────────────────────

def _validate_filament_form(form) -> str | None:
    if not form.get("name", "").strip():
        return "Name is required."
    if not form.get("material", "").strip():
        return "Material is required."
    if not form.get("color_name", "").strip():
        return "Color name is required."
    hex_val = form.get("color_hex", "").strip()
    if hex_val and not _HEX_RE.match(hex_val):
        return "Color value must be in #RRGGBB format."
    hex_val_2 = form.get("color_hex_2", "").strip()
    if hex_val_2 and not _HEX_RE.match(hex_val_2):
        return "Second color value must be in #RRGGBB format."
    try:
        float(form.get("diameter_mm", "1.75"))
        int(form.get("nominal_weight_g", "1000"))
    except ValueError:
        return "Invalid number for diameter or weight."
    return None


async def _resolve_manufacturer(session, form) -> int | None:
    mfr_id = form.get("manufacturer_id", "").strip()
    if mfr_id == "new":
        name = form.get("new_manufacturer_name", "").strip()
        if not name:
            return None
        mfr = Manufacturer(name=name)
        session.add(mfr)
        await session.flush()
        return mfr.id
    if mfr_id.isdigit():
        return int(mfr_id)
    return None


def _filament_fields(form) -> dict:
    color_hex = form.get("color_hex", "").strip() or None
    dual_color = form.get("dual_color") == "1"
    return {
        "name": form.get("name", "").strip(),
        "material": form.get("material", "").strip(),
        "color_name": form.get("color_name", "").strip(),
        "color_hex": color_hex,
        "color_name_2": form.get("color_name_2", "").strip() or None if dual_color else None,
        "color_hex_2": form.get("color_hex_2", "").strip() or None if dual_color else None,
        "diameter_mm": float(form.get("diameter_mm", "1.75")),
        "nominal_weight_g": int(form.get("nominal_weight_g", "1000")),
        "notes": form.get("notes", "").strip() or None,
    }


# ── shop link helpers ──────────────────────────────────────────────────────

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _validate_shoplink_form(form) -> str | None:
    if not form.get("shop_name", "").strip():
        return "Shop name is required."
    url = form.get("url", "").strip()
    if not url:
        return "URL is required."
    if not _URL_RE.match(url):
        return "URL must start with http:// or https://."
    try:
        w = int(form.get("package_weight_g", "0"))
        if w <= 0:
            return "Package weight must be greater than 0."
        p = float(form.get("manual_price", "0"))
        if p < 0:
            return "Price must not be negative."
        s = form.get("shipping_price", "").strip()
        if s and float(s) < 0:
            return "Shipping cost must not be negative."
        t = form.get("target_price", "").strip()
        if t and float(t) < 0:
            return "Target price must not be negative."
        tk = form.get("target_price_per_kg", "").strip()
        if tk and float(tk) < 0:
            return "Target price/kg must not be negative."
    except ValueError:
        return "Invalid number for price or weight."
    return None


def _shoplink_fields(form) -> dict:
    shipping_raw = form.get("shipping_price", "").strip()
    target_raw = form.get("target_price", "").strip()
    target_kg_raw = form.get("target_price_per_kg", "").strip()
    return {
        "shop_name": form.get("shop_name", "").strip(),
        "url": form.get("url", "").strip(),
        "currency": form.get("currency", "EUR").strip() or "EUR",
        "package_weight_g": int(form.get("package_weight_g", "1000")),
        "manual_price": float(form.get("manual_price", "0")),
        "shipping_price": float(shipping_raw) if shipping_raw else None,
        "is_active": "1" in form.getlist("is_active"),
        "target_price": float(target_raw) if target_raw else None,
        "target_price_per_kg": float(target_kg_raw) if target_kg_raw else None,
        "notes": form.get("notes", "").strip() or None,
    }


async def _find_shoplink_duplicate(session, product_id: int, shop_name: str, url: str, exclude_id: int | None = None):
    q = select(ShopLink).where(
        ShopLink.filament_product_id == product_id,
        ShopLink.shop_name == shop_name,
        ShopLink.url == url,
    )
    if exclude_id is not None:
        q = q.where(ShopLink.id != exclude_id)
    return (await session.execute(q)).scalar_one_or_none()


# ── shop link routes ───────────────────────────────────────────────────────

@inventory_bp.route("/<int:product_id>/shop-link/new", methods=["GET", "POST"])
@login_required
async def shoplink_new(product_id: int):
    async with get_db() as session:
        product = await session.get(FilamentProduct, product_id)
        if not product:
            abort(404)

        if request.method == "GET":
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=None,
                form_data=None,
            )

        form = await request.form
        error = _validate_shoplink_form(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=None,
                form_data=form,
            )

        dup = await _find_shoplink_duplicate(
            session, product_id,
            form.get("shop_name", "").strip(),
            form.get("url", "").strip(),
        )
        if dup:
            await flash(
                f'This shop link already exists: "{dup.shop_name}" with the same URL.',
                "error",
            )
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=None,
                form_data=form,
            )

        session.add(ShopLink(filament_product_id=product_id, **_shoplink_fields(form)))

    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.route("/<int:product_id>/shop-link/<int:link_id>/edit", methods=["GET", "POST"])
@login_required
async def shoplink_edit(product_id: int, link_id: int):
    async with get_db() as session:
        link = await session.get(ShopLink, link_id)
        if not link or link.filament_product_id != product_id:
            abort(404)
        product = await session.get(FilamentProduct, product_id)

        if request.method == "GET":
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=link,
                form_data=None,
            )

        form = await request.form
        error = _validate_shoplink_form(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=link,
                form_data=form,
            )

        dup = await _find_shoplink_duplicate(
            session, product_id,
            form.get("shop_name", "").strip(),
            form.get("url", "").strip(),
            exclude_id=link_id,
        )
        if dup:
            await flash(
                f'This shop link already exists: "{dup.shop_name}" with the same URL.',
                "error",
            )
            return await render_template(
                "inventory/shoplink_form.html",
                product=product,
                link=link,
                form_data=form,
            )

        for k, v in _shoplink_fields(form).items():
            setattr(link, k, v)

    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.post("/<int:product_id>/shop-link/<int:link_id>/delete")
@login_required
async def shoplink_delete(product_id: int, link_id: int):
    async with get_db() as session:
        link = await session.get(ShopLink, link_id)
        if not link or link.filament_product_id != product_id:
            abort(404)
        await session.delete(link)
    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.post("/<int:product_id>/shop-link/<int:link_id>/toggle")
@login_required
async def shoplink_toggle(product_id: int, link_id: int):
    async with get_db() as session:
        link = await session.get(ShopLink, link_id)
        if not link or link.filament_product_id != product_id:
            abort(404)
        link.is_active = not link.is_active
    return redirect(url_for("inventory.detail", product_id=product_id))


# ── snapshot routes ────────────────────────────────────────────────────────

def _validate_snapshot_form(form) -> str | None:
    try:
        p = float(form.get("price", ""))
        if p < 0:
            return "Price must not be negative."
    except (ValueError, TypeError):
        return "Price is required."
    s = form.get("shipping_price", "").strip()
    if s:
        try:
            if float(s) < 0:
                return "Shipping cost must not be negative."
        except ValueError:
            return "Ungueltige Versandkosten."
    return None


@inventory_bp.route("/<int:product_id>/shop-link/<int:link_id>/snapshot/new", methods=["GET", "POST"])
@login_required
async def snapshot_new(product_id: int, link_id: int):
    async with get_db() as session:
        link = (await session.execute(
            select(ShopLink)
            .options(selectinload(ShopLink.filament_product))
            .where(ShopLink.id == link_id)
        )).scalar_one_or_none()
        if not link or link.filament_product_id != product_id:
            abort(404)
        product = await session.get(FilamentProduct, product_id)

        if request.method == "GET":
            return await render_template(
                "inventory/snapshot_form.html",
                product=product,
                link=link,
                form_data=None,
            )

        form = await request.form
        error = _validate_snapshot_form(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "inventory/snapshot_form.html",
                product=product,
                link=link,
                form_data=form,
            )

        price_float = float(form.get("price"))
        ship_raw = form.get("shipping_price", "").strip()
        ship_parsed: float | None = float(ship_raw) if ship_raw else None

        snap = PriceSnapshot(
            shop_link_id=link_id,
            price=price_float,
            shipping_price=ship_parsed,
            currency=link.currency,
            availability=form.get("availability", "").strip() or None,
            source="manual",
            error_message=None,
        )
        session.add(snap)
        await session.flush()

        alert_types = await maybe_create_alerts(
            session, link_id, snap.id, price_float, ship_parsed,
            link.target_price, link.target_price_per_kg, link.package_weight_g, link.currency,
        )

        link_url           = link.url
        link_currency      = link.currency
        link_target        = link.target_price
        link_target_kg     = link.target_price_per_kg
        link_shop_name     = link.shop_name
        link_filament_name = link.filament_product.name if link.filament_product else ""

    if alert_types:
        await dispatch_alert_notifications(
            alert_types, link_filament_name, link_shop_name, link_url,
            price_float + (ship_parsed or 0.0),
            link_target, link_target_kg, link_currency,
        )

    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.post("/<int:product_id>/shop-link/<int:link_id>/check")
@login_required
async def shoplink_check(product_id: int, link_id: int):
    detail_url = url_for("inventory.detail", product_id=product_id)

    async with get_db() as session:
        link = (await session.execute(
            select(ShopLink)
            .options(selectinload(ShopLink.filament_product))
            .where(ShopLink.id == link_id)
        )).scalar_one_or_none()
        if not link or link.filament_product_id != product_id:
            abort(404)

        host = urlparse(link.url).hostname or ""
        domain = host.removeprefix("www.")
        rule = (await session.execute(
            select(ShopRule).where(ShopRule.domain == domain, ShopRule.is_active == True)
        )).scalar_one_or_none()
        if not rule:
            from app.shop_adapters.registry import get_adapter
            if not get_adapter(domain):
                await flash(
                    f'No active ShopRule for domain "{domain}" and no built-in adapter. '
                    f'Add a rule or activate an existing one.',
                    "error",
                )
                return redirect(detail_url)

        settings = await get_settings(session)

        # capture plain values before session closes (rule has no lazy relationships)
        link_url       = link.url
        link_currency  = link.currency
        link_shipping  = link.shipping_price
        link_target    = link.target_price
        link_target_kg = link.target_price_per_kg
        link_package   = link.package_weight_g
        link_shop      = link.shop_name
        link_fname     = link.filament_product.name if link.filament_product else ""

    result = await check_price(
        link_id=link_id,
        link_url=link_url,
        link_currency=link_currency,
        link_shipping=link_shipping,
        rule=rule,
        target_price=link_target,
        target_price_per_kg=link_target_kg,
        package_weight_g=link_package,
        shop_name=link_shop,
        filament_name=link_fname,
        settings=settings,
    )

    if result["ok"]:
        msg = f"Price captured: {result['price']} {result['currency']}"
        if result["availability"]:
            msg += f" | {result['availability']}"
        if result["alert_types"]:
            msg += " — Target price reached!"
        await flash(msg, "success")
    else:
        await flash(f"Price check failed: {result['error']}", "error")

    return redirect(detail_url)


@inventory_bp.get("/<int:product_id>/shop-link/<int:link_id>/snapshots")
@login_required
async def snapshot_history(product_id: int, link_id: int):
    async with get_db() as session:
        link = await session.get(ShopLink, link_id)
        if not link or link.filament_product_id != product_id:
            abort(404)
        product = await session.get(FilamentProduct, product_id)

        snapshots = (await session.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.shop_link_id == link_id)
            .order_by(PriceSnapshot.captured_at.desc())
            .limit(100)
        )).scalars().all()

    return await render_template(
        "inventory/snapshot_history.html",
        product=product,
        link=link,
        snapshots=snapshots,
    )


# ── alert routes ───────────────────────────────────────────────────────────

@inventory_bp.post("/alert/<int:alert_id>/resolve")
@login_required
async def alert_resolve(alert_id: int):
    from datetime import datetime as dt
    async with get_db() as session:
        alert = await session.get(PriceAlertEvent, alert_id)
        if alert and alert.resolved_at is None:
            alert.resolved_at = dt.utcnow()
    return redirect(request.referrer or url_for("dashboard.index"))
