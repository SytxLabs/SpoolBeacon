import datetime
import hashlib
from functools import wraps
from pathlib import Path

import yaml
from quart import Blueprint, request, jsonify, Response
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.filament import FilamentProduct, Manufacturer
from app.models.print_job import PrintJob, PrintJobLine
from app.models.spool import Spool, SpoolStatus

api_bp = Blueprint("api", __name__, url_prefix="/api")

async def _json_error(status: int, message: str):
    return jsonify({"error": message}), status


def api_key_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return await _json_error(401, "Missing bearer token")
        token = header[7:]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with get_db() as session:
            key = await session.scalar(
                select(ApiKey).where(ApiKey.token_hash == token_hash)
            )
            if not key:
                return await _json_error(401, "Invalid or revoked token")
            key.last_used_at = datetime.datetime.utcnow()
        return await f(*args, **kwargs)

    return wrapper

@api_bp.get("/v1/health")
async def health():
    return jsonify({"status": "ok"})

@api_bp.get("/v1/manufacturers")
@api_key_required
async def list_manufacturers():
    async with get_db() as session:
        rows = (await session.execute(
            select(Manufacturer).order_by(Manufacturer.name)
        )).scalars().all()
    return jsonify([
        {
            "id": m.id,
            "name": m.name,
            "website": m.website,
        }
        for m in rows
    ])

@api_bp.get("/v1/products")
@api_key_required
async def list_products():
    async with get_db() as session:
        rows = (await session.execute(
            select(FilamentProduct)
            .options(selectinload(FilamentProduct.manufacturer))
            .order_by(FilamentProduct.id)
        )).scalars().all()
    return jsonify([_product_summary(p) for p in rows])


@api_bp.get("/v1/products/<int:product_id>")
@api_key_required
async def get_product(product_id: int):
    async with get_db() as session:
        product = (await session.execute(
            select(FilamentProduct)
            .options(
                selectinload(FilamentProduct.manufacturer),
                selectinload(FilamentProduct.spools),
            )
            .where(FilamentProduct.id == product_id)
        )).scalar_one_or_none()
    if not product:
        return await _json_error(404, "Product not found")
    data = _product_summary(product)
    data["spools"] = [_spool_dict(s) for s in product.spools]
    return jsonify(data)


def _product_summary(p: FilamentProduct) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "manufacturer": p.manufacturer.name,
        "manufacturer_id": p.manufacturer_id,
        "material": p.material,
        "color_name": p.color_name,
        "color_hex": p.color_hex,
        "color_name_2": p.color_name_2,
        "color_hex_2": p.color_hex_2,
        "diameter_mm": p.diameter_mm,
        "nominal_weight_g": p.nominal_weight_g,
    }

@api_bp.get("/v1/spools")
@api_key_required
async def list_spools():
    status_filter = request.args.get("status")
    async with get_db() as session:
        q = select(Spool).options(
            selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer)
        ).order_by(Spool.id)
        if status_filter:
            try:
                q = q.where(Spool.status == SpoolStatus[status_filter])
            except KeyError:
                valid = [s.value for s in SpoolStatus]
                return await _json_error(400, f"Invalid status. Valid values: {valid}")
        rows = (await session.execute(q)).scalars().all()
    return jsonify([_spool_dict(s) for s in rows])


@api_bp.get("/v1/spools/<int:spool_id>")
@api_key_required
async def get_spool(spool_id: int):
    async with get_db() as session:
        spool = (await session.execute(
            select(Spool)
            .options(selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer))
            .where(Spool.id == spool_id)
        )).scalar_one_or_none()
    if not spool:
        return await _json_error(404, "Spool not found")
    return jsonify(_spool_dict(spool))


@api_bp.patch("/v1/spools/<int:spool_id>")
@api_key_required
async def patch_spool(spool_id: int):
    data = await request.get_json(silent=True) or {}
    if "remaining_g" not in data:
        return await _json_error(400, "Body must include 'remaining_g'")
    try:
        remaining = float(data["remaining_g"])
        if remaining < 0:
            raise ValueError
    except (TypeError, ValueError):
        return await _json_error(400, "'remaining_g' must be a non-negative number")

    async with get_db() as session:
        spool = (await session.execute(
            select(Spool).where(Spool.id == spool_id).with_for_update()
        )).scalar_one_or_none()
        if not spool:
            return await _json_error(404, "Spool not found")
        spool.remaining_weight_g = remaining
        spool.last_weight_update_at = datetime.datetime.utcnow()
        spool.last_weight_update_source = "api"
        if remaining <= 0:
            spool.status = SpoolStatus.empty
        elif spool.fill_percent < 20:
            spool.status = SpoolStatus.almost_empty
        elif spool.status in (SpoolStatus.new, SpoolStatus.empty):
            spool.status = SpoolStatus.opened
        await session.flush()
        await session.refresh(spool)
        return jsonify(_spool_dict(spool))


def _spool_dict(s: Spool) -> dict:
    d = {
        "id": s.id,
        "spool_code": s.spool_code,
        "product_id": s.filament_product_id,
        "status": s.status.value,
        "initial_weight_g": s.initial_weight_g,
        "remaining_weight_g": s.remaining_weight_g,
        "fill_percent": s.fill_percent,
        "storage_location": s.storage_location,
        "storage_status": s.storage_status.value,
        "opened_at": s.opened_at.isoformat() if s.opened_at else None,
        "last_dried_at": s.last_dried_at.isoformat() if s.last_dried_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    if "filament_product" in s.__dict__ and s.__dict__["filament_product"] is not None:
        p = s.__dict__["filament_product"]
        mfr = p.__dict__.get("manufacturer")
        d["product_name"] = p.name
        d["manufacturer"] = mfr.name if mfr else None
        d["material"] = p.material
        d["color_name"] = p.color_name
    return d


_MAX_PER_PAGE = 100
_DEFAULT_PER_PAGE = 20


@api_bp.get("/v1/prints")
@api_key_required
async def list_prints():
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(_MAX_PER_PAGE, max(1, int(request.args.get("per_page", _DEFAULT_PER_PAGE))))
    except (ValueError, TypeError):
        page, per_page = 1, _DEFAULT_PER_PAGE

    async with get_db() as session:
        total = await session.scalar(select(func.count(PrintJob.id)))
        jobs = (await session.execute(
            select(PrintJob)
            .options(selectinload(PrintJob.lines))
            .order_by(PrintJob.printed_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )).scalars().all()

    return jsonify({
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, -(-total // per_page)),
        "results": [_job_dict(j) for j in jobs],
    })


@api_bp.post("/v1/prints")
@api_key_required
async def create_print():
    data = await request.get_json(silent=True) or {}
    lines_input = data.get("lines", [])
    if not lines_input or not isinstance(lines_input, list):
        return await _json_error(400, "'lines' must be a non-empty array")

    async with get_db() as session:
        spools_q = (await session.execute(
            select(Spool)
            .options(selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer))
            .where(Spool.status.notin_([SpoolStatus.archived, SpoolStatus.empty]))
            .with_for_update()
        )).scalars().all()
        spool_map = {s.id: s for s in spools_q}

        lines_data = []
        errors = []
        for i, entry in enumerate(lines_input):
            try:
                sid = int(entry["spool_id"])
                used_g = float(entry["used_g"])
                if used_g <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors.append(f"Line {i + 1}: 'spool_id' (int) and 'used_g' (> 0) required")
                continue
            spool = spool_map.get(sid)
            if not spool:
                errors.append(f"Line {i + 1}: spool {sid} not found or not active")
                continue
            if used_g > spool.remaining_weight_g:
                errors.append(
                    f"Line {i + 1}: {used_g} g exceeds remaining {spool.remaining_weight_g} g on {spool.spool_code}"
                )
                continue
            lines_data.append((spool, used_g))

        if errors:
            return await _json_error(422, "; ".join(errors))

        now = datetime.datetime.utcnow()
        job = PrintJob(
            print_name=data.get("print_name") or None,
            notes=data.get("notes") or None,
            printed_at=now,
            created_at=now,
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
            spool.last_weight_update_at = now
            spool.last_weight_update_source = "api"
            if spool.remaining_weight_g <= 0:
                spool.status = SpoolStatus.empty
            elif spool.fill_percent < 20:
                spool.status = SpoolStatus.almost_empty
            elif spool.status == SpoolStatus.new:
                spool.status = SpoolStatus.opened

        await session.flush()

        refreshed = (await session.execute(
            select(PrintJob)
            .options(selectinload(PrintJob.lines))
            .where(PrintJob.id == job.id)
        )).scalar_one()
        result = _job_dict(refreshed)

    return jsonify(result), 201


def _job_dict(j: PrintJob) -> dict:
    return {
        "id": j.id,
        "print_name": j.print_name,
        "notes": j.notes,
        "printed_at": j.printed_at.isoformat() if j.printed_at else None,
        "total_used_g": j.total_used_g,
        "lines": [
            {
                "id": ln.id,
                "spool_id": ln.spool_id,
                "spool_code": ln.spool_code,
                "product_name": ln.product_name,
                "used_g": ln.used_g,
            }
            for ln in j.lines
        ],
    }


_SPEC_PATH = Path(__file__).parent.parent / "openapi.yaml"
_openapi_cache: dict | None = None


def _load_spec() -> dict | None:
    global _openapi_cache
    if _openapi_cache is None:
        with open(_SPEC_PATH, encoding="utf-8") as f:
            _openapi_cache = yaml.safe_load(f)
    return _openapi_cache


@api_bp.get("/openapi.json")
async def openapi_spec():
    return jsonify(_load_spec())


@api_bp.get("/docs")
async def swagger_ui():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SpoolBeacon API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
  SwaggerUIBundle({
    url: "/api/openapi.json",
    dom_id: "#swagger-ui",
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    deepLinking: true,
    persistAuthorization: true,
  });
</script>
</body>
</html>"""
    return Response(html, content_type="text/html")
