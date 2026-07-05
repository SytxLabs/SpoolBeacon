from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, abort, flash, session
from quart_auth import login_required, current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User, UserRole

api_keys_bp = Blueprint("api_keys", __name__, url_prefix="/api-keys")


def admin_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role != UserRole.admin:
            abort(403)
        return await f(*args, **kwargs)

    return wrapper


@api_keys_bp.get("/")
@login_required
@admin_required
async def index():
    new_token = session.pop("new_api_token", None)
    async with get_db() as db:
        keys = (await db.execute(
            select(ApiKey)
            .options(selectinload(ApiKey.user))
            .order_by(ApiKey.created_at.desc())
        )).scalars().all()
    return await render_template("api_keys/index.html", keys=keys, new_token=new_token)


@api_keys_bp.post("/create")
@login_required
@admin_required
async def create():
    form = await request.form
    name = form.get("name", "").strip()
    if not name:
        await flash("API key name is required.", "error")
        return redirect(url_for("api_keys.index"))

    token = ApiKey.generate_token()
    token_hash = ApiKey.hash_token(token)

    async with get_db() as db:
        key = ApiKey(
            name=name,
            token_hash=token_hash,
            user_id=int(current_user.auth_id),
        )
        db.add(key)

    session["new_api_token"] = token
    return redirect(url_for("api_keys.index"))


@api_keys_bp.post("/<int:key_id>/delete")
@login_required
@admin_required
async def delete(key_id: int):
    async with get_db() as session:
        key = await session.get(ApiKey, key_id)
        if not key:
            abort(404)
        await session.delete(key)

    await flash("API key revoked.", "success")
    return redirect(url_for("api_keys.index"))
