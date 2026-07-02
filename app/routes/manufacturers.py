from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required
from sqlalchemy import select, func

from app.database import get_db
from app.models.filament import Manufacturer, FilamentProduct

manufacturers_bp = Blueprint("manufacturers", __name__, url_prefix="/manufacturers")


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


@manufacturers_bp.route("/new", methods=["GET", "POST"])
@login_required
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
async def edit(manufacturer_id: int):
    async with get_db() as session:
        manufacturer = await session.get(Manufacturer, manufacturer_id)
        if not manufacturer:
            abort(404)

        if request.method == "GET":
            return await render_template("manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=None)

        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template("manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=form)

        name = form.get("name", "").strip()
        dup = (await session.execute(
            select(Manufacturer).where(Manufacturer.name == name, Manufacturer.id != manufacturer_id)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Manufacturer "{name}" already exists.', "error")
            return await render_template("manufacturers/manufacturer_form.html", manufacturer=manufacturer, form_data=form)

        for k, v in _fields(form).items():
            setattr(manufacturer, k, v)

    return redirect(url_for("manufacturers.index"))


@manufacturers_bp.post("/<int:manufacturer_id>/delete")
@login_required
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
