from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, flash, abort
from quart_auth import login_required, current_user
from sqlalchemy import select, func
from werkzeug.security import generate_password_hash

from app.database import get_db
from app.i18n import t
from app.models.user import User, UserRole

users_bp = Blueprint("users", __name__, url_prefix="/users")


def admin_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role != UserRole.admin:
            abort(403)
        return await f(*args, **kwargs)
    return wrapper


async def _active_admin_count(session, exclude_id: int | None = None) -> int:
    q = select(func.count(User.id)).where(
        User.role == UserRole.admin,
        User.is_active.is_(True),
    )
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return await session.scalar(q) or 0


@users_bp.get("/")
@login_required
@admin_required
async def index():
    async with get_db() as session:
        users = (await session.execute(
            select(User).order_by(User.role, User.username)
        )).scalars().all()
    return await render_template("users/index.html", users=users)


@users_bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
async def create():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        email = form.get("email", "").strip()
        password = form.get("password", "")
        role_str = form.get("role", "viewer")
        is_active = "1" in form.getlist("is_active")

        errors = []
        if not username:
            errors.append(t("users.validation.username_required"))
        if not email:
            errors.append(t("users.validation.email_required"))
        if not password:
            errors.append(t("users.validation.password_required"))
        if role_str not in ("admin", "member", "viewer"):
            errors.append(t("users.validation.invalid_role"))

        if not errors:
            async with get_db() as session:
                clash = await session.scalar(
                    select(User).where(
                        (User.username == username) | (User.email == email)
                    )
                )
                if clash:
                    errors.append(t("users.validation.username_email_in_use"))
                else:
                    session.add(User(
                        username=username,
                        email=email,
                        password_hash=generate_password_hash(password),
                        role=UserRole[role_str],
                        is_active=is_active,
                    ))

        if errors:
            for e in errors:
                await flash(e, "error")
            return await render_template("users/user_form.html",
                                         editing=False, form_data=form)

        await flash(t("users.flash.created", username=username), "success")
        return redirect(url_for("users.index"))

    return await render_template("users/user_form.html", editing=False, form_data=None)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
async def edit(user_id: int):
    async with get_db() as session:
        target = await session.get(User, user_id)
    if not target:
        abort(404)

    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        email = form.get("email", "").strip()
        new_password = form.get("password", "").strip()
        role_str = form.get("role", "viewer")
        is_active = "1" in form.getlist("is_active")

        errors = []
        if not username:
            errors.append(t("users.validation.username_required"))
        if not email:
            errors.append(t("users.validation.email_required"))
        if role_str not in ("admin", "member", "viewer"):
            errors.append(t("users.validation.invalid_role"))

        me = int(current_user.auth_id)

        # Cannot deactivate own account
        if user_id == me and not is_active:
            errors.append(t("users.validation.cannot_deactivate_self"))

        new_role = UserRole[role_str] if role_str in ("admin", "member", "viewer") else target.role

        # Last-active-admin protection
        if target.role == UserRole.admin and not errors:
            async with get_db() as session:
                other_admins = await _active_admin_count(session, exclude_id=user_id)
            if other_admins == 0:
                if not is_active:
                    errors.append(t("users.validation.cannot_deactivate_last_admin"))
                if new_role != UserRole.admin:
                    errors.append(t("users.validation.cannot_demote_last_admin"))

        if not errors:
            async with get_db() as session:
                clash = await session.scalar(
                    select(User).where(
                        (User.username == username) | (User.email == email),
                        User.id != user_id,
                    )
                )
                if clash:
                    errors.append(t("users.validation.username_email_in_use_other"))
                else:
                    user = await session.get(User, user_id)
                    user.username = username
                    user.email = email
                    user.role = new_role
                    user.is_active = is_active
                    if new_password:
                        user.password_hash = generate_password_hash(new_password)

        if errors:
            for e in errors:
                await flash(e, "error")
            async with get_db() as session:
                target = await session.get(User, user_id)
            return await render_template("users/user_form.html",
                                         editing=True, user=target, form_data=form)

        await flash(t("users.flash.updated", username=username), "success")
        return redirect(url_for("users.index"))

    return await render_template("users/user_form.html",
                                 editing=True, user=target, form_data=None)


@users_bp.post("/<int:user_id>/delete")
@login_required
@admin_required
async def delete(user_id: int):
    me = int(current_user.auth_id)
    if user_id == me:
        await flash(t("users.flash.cannot_delete_self"), "error")
        return redirect(url_for("users.index"))

    async with get_db() as session:
        target = await session.get(User, user_id)
        if not target:
            abort(404)
        if target.role == UserRole.admin:
            other_admins = await _active_admin_count(session, exclude_id=user_id)
            if other_admins == 0:
                await flash(t("users.flash.cannot_delete_last_admin"), "error")
                return redirect(url_for("users.index"))
        await session.delete(target)

    await flash(t("users.flash.deleted"), "success")
    return redirect(url_for("users.index"))
