from quart import Blueprint, render_template, request, redirect, url_for, flash
from quart_auth import AuthUser, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import select, func

from app.database import get_db
from app.models.user import User, UserRole

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/")
async def index():
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/login", methods=["GET", "POST"])
async def login():
    if await current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")

        async with get_db() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()

        if user and user.is_active and check_password_hash(user.password_hash, password):
            login_user(AuthUser(str(user.id)))
            return redirect(url_for("dashboard.index"))

        await flash("Invalid credentials.", "error")

    return await render_template("auth/login.html")


@auth_bp.get("/logout")
@login_required
async def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup", methods=["GET", "POST"])
async def setup():
    async with get_db() as session:
        count = await session.scalar(select(func.count(User.id)))

    if count and count > 0:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        email = form.get("email", "").strip()
        password = form.get("password", "")

        if not (username and email and password):
            await flash("All fields are required.", "error")
            return await render_template("auth/setup.html")

        async with get_db() as session:
            admin = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role=UserRole.admin,
                is_active=True,
            )
            session.add(admin)

        return redirect(url_for("auth.login"))

    return await render_template("auth/setup.html")
