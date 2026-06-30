from quart import Quart, redirect, url_for, request, abort
from quart_auth import QuartAuth, Unauthorized

from .config import Config
from .csrf import generate_csrf_token, validate_csrf
from .database import init_db

auth_manager = QuartAuth()


def create_app(config_class=Config) -> Quart:
    app = Quart(__name__)
    app.config.from_object(config_class)

    auth_manager.init_app(app)
    init_db(app.config["DATABASE_URL"])

    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.inventory import inventory_bp
    from .routes.health import health_bp
    from .routes.shop_rules import shop_rules_bp
    from .routes.settings import settings_bp
    from .routes.users import users_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(shop_rules_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(users_bp)

    @app.before_request
    async def validate_active_session():
        from quart_auth import current_user, logout_user
        from quart import flash
        if not await current_user.is_authenticated:
            return
        if request.endpoint in ("auth.login", "auth.setup", "auth.logout", "health.health", "static"):
            return
        from app.models.user import User
        from app.database import get_db
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or not user.is_active:
            logout_user()
            await flash("Your account has been deactivated or deleted. Contact an administrator.", "error")
            return redirect(url_for("auth.login"))

    @app.before_request
    async def csrf_protect():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        content_type = request.content_type or ""
        if "json" in content_type:
            return
        form = await request.form
        if not validate_csrf(form.get("_csrf_token")):
            abort(403)

    from .scheduler import create_scheduler
    _scheduler = create_scheduler()

    @app.before_serving
    async def start_scheduler():
        from .scheduler import init_status as _scheduler_init
        await _scheduler_init()
        _scheduler.start()

    @app.after_serving
    async def stop_scheduler():
        _scheduler.shutdown(wait=False)

    @app.errorhandler(Unauthorized)
    async def handle_unauthorized(_e):
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    async def handle_403(_e):
        from quart import render_template
        return await render_template("errors/403.html"), 403

    @app.errorhandler(404)
    async def handle_404(_e):
        from quart import render_template
        return await render_template("errors/404.html"), 404

    @app.errorhandler(500)
    async def handle_500(_e):
        from quart import render_template
        return await render_template("errors/500.html"), 500

    @app.context_processor
    async def inject_globals():
        from quart_auth import current_user
        nav_alert_count = 0
        is_admin = False
        if await current_user.is_authenticated:
            from sqlalchemy import select, func
            from app.models.price_alert_event import PriceAlertEvent
            from app.models.user import User, UserRole
            from app.database import get_db
            async with get_db() as session:
                nav_alert_count = await session.scalar(
                    select(func.count(PriceAlertEvent.id))
                    .where(PriceAlertEvent.resolved_at.is_(None))
                ) or 0
                db_user = await session.get(User, int(current_user.auth_id))
                is_admin = db_user is not None and db_user.role == UserRole.admin
        return {
            "current_user": current_user,
            "csrf_token": generate_csrf_token,
            "nav_alert_count": nav_alert_count,
            "is_admin": is_admin,
        }

    return app
