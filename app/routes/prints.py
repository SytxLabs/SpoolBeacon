import datetime
from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required, current_user
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.i18n import t
from app.models.filament import FilamentProduct, Manufacturer
from app.models.spool import Spool, SpoolStatus
from app.models.user import User, UserRole
from app.models.print_job import PrintJob, PrintJobLine

prints_bp = Blueprint("prints", __name__, url_prefix="/prints")

_PER_PAGE = 20


def write_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role == UserRole.viewer:
            abort(403)
        return await f(*args, **kwargs)
    return wrapper


@prints_bp.get("/")
@login_required
async def index():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    async with get_db() as session:
        total = await session.scalar(select(func.count(PrintJob.id)))
        jobs = (await session.execute(
            select(PrintJob)
            .options(selectinload(PrintJob.lines))
            .order_by(PrintJob.printed_at.desc())
            .limit(_PER_PAGE)
            .offset((page - 1) * _PER_PAGE)
        )).scalars().all()

    total_pages = max(1, -(-total // _PER_PAGE))

    return await render_template(
        "prints/index.html",
        jobs=jobs,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@prints_bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
async def new_print():
    async with get_db() as session:
        spools = (await session.execute(
            select(Spool)
            .options(
                selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer)
            )
            .where(Spool.status.notin_([SpoolStatus.archived, SpoolStatus.empty]))
            .order_by(Spool.filament_product_id, Spool.spool_code)
        )).scalars().all()

        if request.method == "GET":
            return await render_template("prints/print_form.html", spools=spools)

        form = await request.form

        spool_ids = form.getlist("spool_id[]")
        used_gs = form.getlist("used_g[]")

        if not spool_ids:
            await flash(t("prints.validation.no_lines"), "error")
            return await render_template("prints/print_form.html", spools=spools)

        spool_map = {s.id: s for s in spools}

        lines_data = []
        errors = []
        for i, (sid_raw, ug_raw) in enumerate(zip(spool_ids, used_gs)):
            try:
                sid = int(sid_raw)
            except (ValueError, TypeError):
                errors.append(t("prints.validation.line_invalid_spool", line=i + 1))
                continue
            try:
                used_g = float(ug_raw)
                if used_g <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(t("prints.validation.line_used_weight", line=i + 1))
                continue

            spool = spool_map.get(sid)
            if not spool:
                errors.append(t("prints.validation.line_spool_not_found", line=i + 1))
                continue
            if used_g > spool.remaining_weight_g:
                errors.append(
                    t(
                        "prints.validation.line_exceeds_remaining",
                        line=i + 1,
                        used=used_g,
                        remaining=spool.remaining_weight_g,
                        code=spool.spool_code,
                    )
                )
                continue

            lines_data.append((spool, used_g))

        if errors:
            for msg in errors:
                await flash(msg, "error")
            return await render_template("prints/print_form.html", spools=spools)

        print_name = form.get("print_name", "").strip() or None
        notes = form.get("notes", "").strip() or None

        job = PrintJob(
            print_name=print_name,
            notes=notes,
            printed_at=datetime.datetime.utcnow(),
            created_at=datetime.datetime.utcnow(),
        )
        session.add(job)
        await session.flush()

        for spool, used_g in lines_data:
            product = spool.filament_product
            line = PrintJobLine(
                print_job_id=job.id,
                spool_id=spool.id,
                spool_code=spool.spool_code,
                product_name=f"{product.manufacturer.name} {product.name} – {product.color_name}",
                used_g=used_g,
            )
            session.add(line)

            spool.remaining_weight_g = max(0.0, spool.remaining_weight_g - used_g)
            spool.last_weight_update_at = datetime.datetime.utcnow()
            spool.last_weight_update_source = "print-log"

            if spool.remaining_weight_g <= 0:
                spool.status = SpoolStatus.empty
            elif spool.fill_percent < 20:
                spool.status = SpoolStatus.almost_empty
            elif spool.status == SpoolStatus.new:
                spool.status = SpoolStatus.opened

    await flash(t("prints.flash.logged"), "success")
    return redirect(url_for("prints.index"))


@prints_bp.post("/<int:job_id>/delete")
@login_required
@write_required
async def delete_print(job_id: int):
    async with get_db() as session:
        job = await session.get(PrintJob, job_id)
        if not job:
            abort(404)
        await session.delete(job)

    await flash(t("prints.flash.deleted"), "success")
    return redirect(url_for("prints.index"))
