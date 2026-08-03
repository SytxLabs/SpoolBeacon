import datetime
import os
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash, send_from_directory
from quart_auth import login_required, current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Config
from app.database import get_db
from app.i18n import t
from app.models.filament import FilamentProduct, Manufacturer
from app.models.spool import Spool, SpoolStatus
from app.models.user import User, UserRole
from app.models.print_job import PrintJob, PrintJobLine, PrintJobFile, PrintFileKind, PrintJobStatus
from app.print_job_service import (
    create_print_job, deduct_filament, restore_filament, find_insufficient_lines,
)

prints_bp = Blueprint("prints", __name__, url_prefix="/prints")

_DONE_COLUMN_LIMIT = 50
_VIEWER_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "js" / "print-viewer.js"


async def _validate_lines(form, spool_map: dict[int, Spool]) -> tuple[list[tuple[Spool, float]], list[str]]:
    spool_ids = form.getlist("spool_id[]")
    used_gs = form.getlist("used_g[]")

    lines_data = []
    errors = []
    if len(spool_ids) != len(used_gs):
        return [], [t("prints.validation.no_lines")]
    for i, (sid_raw, ug_raw) in enumerate(zip(spool_ids, used_gs, strict=True)):
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

    return lines_data, errors


async def _validate_new_files(form, files_multidict) -> tuple[list[dict], list[str]]:
    file_kinds = form.getlist("file_kind[]")
    file_urls = form.getlist("file_url[]")
    file_uploads = files_multidict.getlist("file_upload[]")

    file_rows = []
    errors = []
    for i, kind_raw in enumerate(file_kinds):
        url_raw = file_urls[i] if i < len(file_urls) else ""
        upload = file_uploads[i] if i < len(file_uploads) else None

        if kind_raw == "link":
            url_raw = url_raw.strip()
            parsed = urlparse(url_raw)
            if not url_raw or not parsed.scheme or not parsed.netloc:
                errors.append(t("prints.validation.file_invalid_url", line=i + 1))
                continue
            file_rows.append({
                "kind": PrintFileKind.link,
                "url": url_raw,
                "provider": _detect_provider(url_raw),
            })
        elif kind_raw == "upload":
            if not upload or not upload.filename:
                errors.append(t("prints.validation.file_no_file", line=i + 1))
                continue
            ext = Path(upload.filename).suffix.lstrip(".").lower()
            if ext not in _ALLOWED_FILE_EXTENSIONS:
                errors.append(t("prints.validation.file_invalid_type", line=i + 1))
                continue
            upload.stream.seek(0, os.SEEK_END)
            size_bytes = upload.stream.tell()
            upload.stream.seek(0)
            if size_bytes > Config.MAX_UPLOAD_MB * 1024 * 1024:
                errors.append(t("prints.validation.file_too_large", line=i + 1, max_mb=Config.MAX_UPLOAD_MB))
                continue
            file_rows.append({
                "kind": PrintFileKind.upload,
                "upload": upload,
                "ext": ext,
                "original_filename": upload.filename,
                "size_bytes": size_bytes,
            })

    return file_rows, errors


async def _save_files(session, job_id: int, file_rows: list[dict]) -> None:
    if any(row["kind"] == PrintFileKind.upload for row in file_rows):
        os.makedirs(Config.UPLOAD_DIR, exist_ok=True)

    for row in file_rows:
        if row["kind"] == PrintFileKind.link:
            session.add(PrintJobFile(
                print_job_id=job_id,
                kind=PrintFileKind.link,
                provider=row["provider"],
                url=row["url"],
            ))
        else:
            stored_filename = f"{uuid4().hex}.{row['ext']}"
            dest_path = Path(Config.UPLOAD_DIR) / stored_filename
            await row["upload"].save(dest_path)
            session.add(PrintJobFile(
                print_job_id=job_id,
                kind=PrintFileKind.upload,
                stored_filename=stored_filename,
                original_filename=row["original_filename"],
                file_ext=row["ext"],
                file_size_bytes=row["size_bytes"],
            ))


def _viewer_js_version() -> int:
    try:
        return int(_VIEWER_JS_PATH.stat().st_mtime)
    except OSError:
        return 0
_ALLOWED_FILE_EXTENSIONS = {"stl", "3mf"}
_KNOWN_PROVIDERS = {
    "printables.com": "printables",
    "makerworld.com": "makerworld",
    "thingiverse.com": "thingiverse",
}
_FILE_MIMETYPES = {
    "stl": "model/stl",
    "3mf": "model/3mf",
}


def _detect_provider(url: str) -> str | None:
    hostname = urlparse(url).hostname
    if not hostname:
        return None
    hostname = hostname.removeprefix("www.")
    return _KNOWN_PROVIDERS.get(hostname, hostname[:50])


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
    async with get_db() as session:
        jobs = (await session.execute(
            select(PrintJob)
            .options(selectinload(PrintJob.lines), selectinload(PrintJob.files))
            .order_by(PrintJob.created_at.asc())
        )).scalars().all()

    planned_jobs = [j for j in jobs if j.status == PrintJobStatus.planned]
    printing_jobs = [j for j in jobs if j.status == PrintJobStatus.printing]
    done_jobs = sorted(
        (j for j in jobs if j.status == PrintJobStatus.done),
        key=lambda j: j.completed_at or j.created_at,
        reverse=True,
    )[:_DONE_COLUMN_LIMIT]

    return await render_template(
        "prints/index.html",
        viewer_js_version=_viewer_js_version(),
        planned_jobs=planned_jobs,
        printing_jobs=printing_jobs,
        done_jobs=done_jobs,
        total=len(jobs),
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

        if not form.getlist("spool_id[]"):
            await flash(t("prints.validation.no_lines"), "error")
            return await render_template("prints/print_form.html", spools=spools)

        spool_map = {s.id: s for s in spools}
        lines_data, line_errors = await _validate_lines(form, spool_map)
        file_rows, file_errors = await _validate_new_files(form, await request.files)
        errors = line_errors + file_errors

        if errors:
            for msg in errors:
                await flash(msg, "error")
            return await render_template("prints/print_form.html", spools=spools)

        print_name = form.get("print_name", "").strip() or None
        notes = form.get("notes", "").strip() or None

        job = await create_print_job(
            session,
            print_name=print_name,
            notes=notes,
            status=PrintJobStatus.planned,
            printed_at=datetime.datetime.utcnow(),
            created_at=datetime.datetime.utcnow(),
        )

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

        await _save_files(session, job.id, file_rows)

    await flash(t("prints.flash.logged"), "success")
    return redirect(url_for("prints.index"))


@prints_bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
async def edit_print(job_id: int):
    async with get_db() as session:
        job = await session.get(
            PrintJob,
            job_id,
            options=[
                selectinload(PrintJob.lines).selectinload(PrintJobLine.spool),
                selectinload(PrintJob.files),
            ],
        )
        if not job:
            abort(404)
        if job.status == PrintJobStatus.done:
            await flash(t("prints.validation.cannot_edit_done"), "error")
            return redirect(url_for("prints.index", _anchor=f"job-{job.id}"))

        active_spools = (await session.execute(
            select(Spool)
            .options(selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer))
            .where(Spool.status.notin_([SpoolStatus.archived, SpoolStatus.empty]))
            .order_by(Spool.filament_product_id, Spool.spool_code)
        )).scalars().all()
        spool_ids = {s.id for s in active_spools}
        spools = list(active_spools)
        for line in job.lines:
            if line.spool and line.spool_id not in spool_ids:
                spools.append(line.spool)
                spool_ids.add(line.spool_id)

        if request.method == "GET":
            return await render_template("prints/print_form.html", spools=spools, job=job, editing=True)

        form = await request.form

        if not form.getlist("spool_id[]"):
            await flash(t("prints.validation.no_lines"), "error")
            return await render_template("prints/print_form.html", spools=spools, job=job, editing=True)

        spool_map = {s.id: s for s in spools}
        lines_data, line_errors = await _validate_lines(form, spool_map)
        file_rows, file_errors = await _validate_new_files(form, await request.files)
        errors = line_errors + file_errors

        if errors:
            for msg in errors:
                await flash(msg, "error")
            return await render_template("prints/print_form.html", spools=spools, job=job, editing=True)

        job.print_name = form.get("print_name", "").strip() or None
        job.notes = form.get("notes", "").strip() or None

        for line in list(job.lines):
            await session.delete(line)
        await session.flush()

        for spool, used_g in lines_data:
            product = spool.filament_product
            session.add(PrintJobLine(
                print_job_id=job.id,
                spool_id=spool.id,
                spool_code=spool.spool_code,
                product_name=f"{product.manufacturer.name} {product.name} – {product.color_name}",
                used_g=used_g,
            ))

        await _save_files(session, job.id, file_rows)

    await flash(t("prints.flash.updated"), "success")
    return redirect(url_for("prints.index", _anchor=f"job-{job_id}"))


@prints_bp.post("/<int:job_id>/delete")
@login_required
@write_required
async def delete_print(job_id: int):
    async with get_db() as session:
        job = await session.get(
            PrintJob,
            job_id,
            options=[
                selectinload(PrintJob.files),
                selectinload(PrintJob.lines).selectinload(PrintJobLine.spool),
            ],
        )
        if not job:
            abort(404)
        if job.status == PrintJobStatus.done:
            restore_filament(job)
        for file in job.files:
            if file.kind == PrintFileKind.upload and file.stored_filename:
                (Path(Config.UPLOAD_DIR) / file.stored_filename).unlink(missing_ok=True)
        await session.delete(job)

    await flash(t("prints.flash.deleted"), "success")
    return redirect(url_for("prints.index"))


@prints_bp.post("/<int:job_id>/status")
@login_required
@write_required
async def update_status(job_id: int):
    form = await request.form
    new_status_raw = form.get("status", "")
    try:
        new_status = PrintJobStatus(new_status_raw)
    except ValueError:
        abort(400)

    async with get_db() as session:
        job = await session.get(
            PrintJob,
            job_id,
            options=[selectinload(PrintJob.lines).selectinload(PrintJobLine.spool)],
        )
        if not job:
            abort(404)

        old_status = job.status
        if old_status != PrintJobStatus.done and new_status == PrintJobStatus.done:
            insufficient = find_insufficient_lines(job)
            if insufficient:
                await flash(
                    t("prints.validation.insufficient_at_done", codes=", ".join(insufficient)),
                    "error",
                )
                return redirect(url_for("prints.index", _anchor=f"job-{job_id}"))
            deduct_filament(job)
            job.completed_at = datetime.datetime.utcnow()
        elif old_status == PrintJobStatus.done and new_status != PrintJobStatus.done:
            restore_filament(job)
            job.completed_at = None

        job.status = new_status

    await flash(t("prints.flash.status_updated"), "success")
    return redirect(url_for("prints.index", _anchor=f"job-{job_id}"))


@prints_bp.get("/files/<int:file_id>/download")
@login_required
async def download_file(file_id: int):
    async with get_db() as session:
        file = await session.get(PrintJobFile, file_id)
        if not file:
            abort(404)
        if file.kind == PrintFileKind.link:
            return redirect(file.url)
        if not file.stored_filename:
            abort(404)
        return await send_from_directory(
            Config.UPLOAD_DIR,
            file.stored_filename,
            mimetype=_FILE_MIMETYPES.get(file.file_ext, "application/octet-stream"),
            attachment_filename=file.original_filename,
        )


@prints_bp.post("/files/<int:file_id>/delete")
@login_required
@write_required
async def delete_file(file_id: int):
    form = await request.form
    redirect_to_edit = form.get("next") == "edit"

    async with get_db() as session:
        file = await session.get(PrintJobFile, file_id)
        if not file:
            abort(404)
        job_id = file.print_job_id
        if file.kind == PrintFileKind.upload and file.stored_filename:
            (Path(Config.UPLOAD_DIR) / file.stored_filename).unlink(missing_ok=True)
        await session.delete(file)

    await flash(t("prints.flash.file_deleted"), "success")
    if redirect_to_edit:
        return redirect(url_for("prints.edit_print", job_id=job_id))
    return redirect(url_for("prints.index", _anchor=f"job-{job_id}"))
