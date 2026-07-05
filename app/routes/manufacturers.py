from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required, current_user
from sqlalchemy import select, func

from app.database import get_db
from app.models.filament import Manufacturer, FilamentProduct
from app.models.spool import Spool, SpoolStatus
from app.models.user import User, UserRole

manufacturers_bp = Blueprint("manufacturers", __name__, url_prefix="/manufacturers")


def write_required(f):
    """Blocks viewer-role users from state-changing routes. Stack after @login_required."""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role == UserRole.viewer:
            abort(403)
        return await f(*args, **kwargs)
    return wrapper


def _validate(form) -> str | None:
    name = form.get("name", "").strip()
    if not name:
        return "Name is required."
    if len(name) > 128:
        return "Name must be 128 characters or fewer."
    website = form.get("website", "").strip()
    if website and not website.startswith(("http://", "https://")):
        return "Website must start with http:// or https://."
    return None


def _fields(form) -> dict:
    return {
        "name": form.get("name", "").strip(),
        "website": form.get("website", "").strip() or None,
        "notes": form.get("notes", "").strip() or None,
    }


@manufacturers_bp.get("/")
@login_required
async def index():
    async with get_db() as session:
        manufacturers = (await session.execute(
            select(Manufacturer).order_by(Manufacturer.name)
        )).scalars().all()
        rows = []
        for mfr in manufacturers:
            product_count = await session.scalar(
                select(func.count(FilamentProduct.id)).where(FilamentProduct.manufacturer_id == mfr.id)
            ) or 0
            rows.append({"manufacturer": mfr, "product_count": product_count})
    return await render_template("manufacturers/index.html", rows=rows)


@manufacturers_bp.get("/<int:manufacturer_id>")
@login_required
async def detail(manufacturer_id: int):
    async with get_db() as session:
        manufacturer = await session.get(Manufacturer, manufacturer_id)
        if not manufacturer:
            abort(404)

        q = (
            select(
                FilamentProduct,
                func.count(Spool.id).label("spool_count"),
                func.coalesce(func.sum(Spool.remaining_weight_g), 0).label("total_remaining_g"),
            )
            .outerjoin(
                Spool,
                (Spool.filament_product_id == FilamentProduct.id)
                & (Spool.status != SpoolStatus.archived),
            )
            .where(FilamentProduct.manufacturer_id == manufacturer_id)
            .group_by(FilamentProduct.id)
            .order_by(FilamentProduct.material, FilamentProduct.name)
        )
        products = (await session.execute(q)).all()

    return await render_template(
        "manufacturers/manufacturer_detail.html", manufacturer=manufacturer, products=products
    )


@manufacturers_bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
async def new():
    if request.method == "GET":
        return await render_template("manufacturers/manufacturer_form.html", manufacturer=None, form_data=None)

    async with get_db() as session:
        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template("manufacturers/manufacturer_form.html", manufacturer=None, form_data=form)

        name = form.get("name", "").strip()
        dup = (await session.execute(
            select(Manufacturer).where(Manufacturer.name == name)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Manufacturer "{name}" already exists.', "error")
            return await render_template("manufacturers/manufacturer_form.html", manufacturer=None, form_data=form)

        session.add(Manufacturer(**_fields(form)))

    return redirect(url_for("manufacturers.index"))


@manufacturers_bp.route("/<int:manufacturer_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
async def edit(manufacturer_id: int):
    async with get_db() as session:
        manufacturer = await session.get(Manufacturer, manufacturer_id)
        if not manufacturer:
            abort(404)

        if request.method == "GET":
            return await render_template(
                "manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=None
            )

        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=form
            )

        name = form.get("name", "").strip()
        dup = (await session.execute(
            select(Manufacturer).where(Manufacturer.name == name, Manufacturer.id != manufacturer_id)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Manufacturer "{name}" already exists.', "error")
            return await render_template(
                "manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=form
            )

        for k, v in _fields(form).items():
            setattr(manufacturer, k, v)

    return redirect(url_for("manufacturers.index"))


@manufacturers_bp.post("/<int:manufacturer_id>/delete")
@login_required
@write_required
async def delete(manufacturer_id: int):
    async with get_db() as session:
        manufacturer = await session.get(Manufacturer, manufacturer_id)
        if not manufacturer:
            abort(404)

        product_count = await session.scalar(
            select(func.count(FilamentProduct.id)).where(FilamentProduct.manufacturer_id == manufacturer_id)
        ) or 0
        if product_count:
            await flash(
                f'Cannot delete "{manufacturer.name}": {product_count} filament product(s) still reference it.',
                "error",
            )
            return redirect(url_for("manufacturers.index"))

        await session.delete(manufacturer)

    return redirect(url_for("manufacturers.index"))
