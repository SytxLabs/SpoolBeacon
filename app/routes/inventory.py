import datetime
import re

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, contains_eager

from app.database import get_db
from app.models.filament import FilamentProduct, Manufacturer
from app.models.price_snapshot import PriceSnapshot
from app.models.purchase import Purchase, PurchaseLine
from app.models.shoplink import ShopLink
from app.models.spool import Spool, SpoolStatus, StorageStatus

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


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
                & (Spool.status != SpoolStatus.archiviert),
            )
            .group_by(FilamentProduct.id, Manufacturer.name)
            .order_by(Manufacturer.name, FilamentProduct.material, FilamentProduct.name)
        )

        if material_filter:
            q = q.where(FilamentProduct.material == material_filter)

        products = (await session.execute(q)).all()

    return await render_template(
        "inventory/index.html",
        products=products,
        materials=materials,
        current_material=material_filter,
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

    return await render_template(
        "inventory/detail.html",
        product=product,
        spools=spools,
        purchase_lines=purchase_lines,
        shop_links=shop_links,
        latest_snapshots=latest_snapshots,
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
                f'Dieses Filament existiert bereits: "{dup.name}" ({dup.material}, {dup.color_name}, {dup.diameter_mm} mm)',
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
            await flash("Hersteller fehlt oder Name leer.", "error")
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
                f'Ein anderes Filament mit diesen Daten existiert bereits: "{dup.name}" ({dup.material}, {dup.color_name}, {dup.diameter_mm} mm)',
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
            await flash("Hersteller fehlt oder Name leer.", "error")
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
                f"Nicht löschbar: {spool_count} Spule(n) und {line_count} Kaufposition(en) verknüpft.",
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
            select(func.count(Spool.id)).where(Spool.purchase_line_id == line_id)
        ) or 0

        ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")

        for i in range(existing_count, existing_count + line.quantity):
            code = f"SB-{line.filament_product_id}-{line_id}-{ts}-{i + 1:02d}"
            exists = await session.scalar(
                select(func.count(Spool.id)).where(Spool.spool_code == code)
            )
            if exists:
                continue
            session.add(Spool(
                filament_product_id=line.filament_product_id,
                purchase_line_id=line_id,
                spool_code=code,
                status=SpoolStatus.neu,
                initial_weight_g=float(line.spool_weight_g),
                remaining_weight_g=float(line.spool_weight_g),
                storage_status=StorageStatus.unbekannt,
            ))

    return redirect(url_for("inventory.detail", product_id=line.filament_product_id))


# ── form helpers ───────────────────────────────────────────────────────────

def _validate_filament_form(form) -> str | None:
    if not form.get("name", "").strip():
        return "Name ist ein Pflichtfeld."
    if not form.get("material", "").strip():
        return "Material ist ein Pflichtfeld."
    if not form.get("color_name", "").strip():
        return "Farbbezeichnung ist ein Pflichtfeld."
    hex_val = form.get("color_hex", "").strip()
    if hex_val and not _HEX_RE.match(hex_val):
        return "Farbwert muss im Format #RRGGBB angegeben werden."
    try:
        float(form.get("diameter_mm", "1.75"))
        int(form.get("nominal_weight_g", "1000"))
    except ValueError:
        return "Ungültige Zahl bei Durchmesser oder Gewicht."
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
    return {
        "name": form.get("name", "").strip(),
        "material": form.get("material", "").strip(),
        "color_name": form.get("color_name", "").strip(),
        "color_hex": color_hex,
        "diameter_mm": float(form.get("diameter_mm", "1.75")),
        "nominal_weight_g": int(form.get("nominal_weight_g", "1000")),
        "notes": form.get("notes", "").strip() or None,
    }


# ── shop link helpers ──────────────────────────────────────────────────────

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _validate_shoplink_form(form) -> str | None:
    if not form.get("shop_name", "").strip():
        return "Shop-Name ist ein Pflichtfeld."
    url = form.get("url", "").strip()
    if not url:
        return "URL ist ein Pflichtfeld."
    if not _URL_RE.match(url):
        return "URL muss mit http:// oder https:// beginnen."
    try:
        w = int(form.get("package_weight_g", "0"))
        if w <= 0:
            return "Paketgewicht muss groesser als 0 sein."
        p = float(form.get("manual_price", "0"))
        if p < 0:
            return "Preis darf nicht negativ sein."
        s = form.get("shipping_price", "").strip()
        if s and float(s) < 0:
            return "Versandkosten duerfen nicht negativ sein."
    except ValueError:
        return "Ungueltige Zahl bei Preis oder Gewicht."
    return None


def _shoplink_fields(form) -> dict:
    shipping_raw = form.get("shipping_price", "").strip()
    return {
        "shop_name": form.get("shop_name", "").strip(),
        "url": form.get("url", "").strip(),
        "currency": form.get("currency", "EUR").strip() or "EUR",
        "package_weight_g": int(form.get("package_weight_g", "1000")),
        "manual_price": float(form.get("manual_price", "0")),
        "shipping_price": float(shipping_raw) if shipping_raw else None,
        "is_active": form.get("is_active") == "1",
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
                f'Dieser Shop-Link existiert bereits: "{dup.shop_name}" mit gleicher URL.',
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
                f'Dieser Shop-Link existiert bereits: "{dup.shop_name}" mit gleicher URL.',
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
            return "Preis darf nicht negativ sein."
    except (ValueError, TypeError):
        return "Preis ist ein Pflichtfeld."
    s = form.get("shipping_price", "").strip()
    if s:
        try:
            if float(s) < 0:
                return "Versandkosten duerfen nicht negativ sein."
        except ValueError:
            return "Ungueltige Versandkosten."
    return None


@inventory_bp.route("/<int:product_id>/shop-link/<int:link_id>/snapshot/new", methods=["GET", "POST"])
@login_required
async def snapshot_new(product_id: int, link_id: int):
    async with get_db() as session:
        link = await session.get(ShopLink, link_id)
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

        ship_raw = form.get("shipping_price", "").strip()
        session.add(PriceSnapshot(
            shop_link_id=link_id,
            price=float(form.get("price")),
            shipping_price=float(ship_raw) if ship_raw else None,
            currency=link.currency,
            availability=form.get("availability", "").strip() or None,
            source="manual",
            error_message=None,
        ))

    return redirect(url_for("inventory.detail", product_id=product_id))


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
