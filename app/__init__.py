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

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(shop_rules_bp)

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

    @app.errorhandler(Unauthorized)
    async def handle_unauthorized(_e):
        return redirect(url_for("auth.login"))

    @app.context_processor
    async def inject_globals():
        from quart_auth import current_user
        return {"current_user": current_user, "csrf_token": generate_csrf_token}

    return app
