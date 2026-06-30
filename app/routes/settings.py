from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, flash, abort
from quart_auth import login_required, current_user
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, UserRole
from app.settings_service import get_all, set_many
from app.notification_service import send_discord, send_email
from app.spool_code import generate_spool_code, DEFAULT_TEMPLATE, AVAILABLE_VARS

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

_SENSITIVE_KEYS = {"discord.webhook_url", "smtp.password"}


def admin_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role != UserRole.admin:
            abort(403)
        return await f(*args, **kwargs)
    return wrapper


@settings_bp.get("/")
@login_required
@admin_required
async def index():
    async with get_db() as session:
        s = await get_all(session)
    return await render_template("settings/index.html", s=s, available_vars=AVAILABLE_VARS)


@settings_bp.post("/")
@login_required
@admin_required
async def save():
    form = await request.form
    async with get_db() as session:
        current = await get_all(session)

        def cb(name: str) -> str:
            # hidden input + checkbox → getlist() to detect checked state
            return "1" if "1" in form.getlist(name) else "0"

        updates: dict[str, str] = {
            # spool codes
            "spool.code_template": form.get("spool_code_template", "").strip() or DEFAULT_TEMPLATE,
            # scheduler
            "scheduler.enabled": cb("scheduler_enabled"),
            "scheduler.interval_minutes": form.get("scheduler_interval_minutes", "360").strip() or "360",
            "scheduler.min_interval_minutes": form.get("scheduler_min_interval_minutes", "60").strip() or "60",
            # price-check engine
            "check.engine": form.get("check_engine", "playwright").strip() or "playwright",
            "playwright.headless": cb("playwright_headless"),
            "playwright.timeout_ms": form.get("playwright_timeout_ms", "30000").strip() or "30000",
            "playwright.max_concurrent": form.get("playwright_max_concurrent", "1").strip() or "1",
            # discord
            "discord.enabled": cb("discord_enabled"),
            "discord.webhook_url": form.get("discord_webhook_url", "").strip()
                                   or current["discord.webhook_url"],
            # smtp
            "smtp.enabled": cb("smtp_enabled"),
            "smtp.host": form.get("smtp_host", "").strip(),
            "smtp.port": form.get("smtp_port", "587").strip() or "587",
            "smtp.user": form.get("smtp_user", "").strip(),
            "smtp.from_addr": form.get("smtp_from_addr", "").strip(),
            "smtp.to_addr": form.get("smtp_to_addr", "").strip(),
            "smtp.tls": cb("smtp_tls"),
        }
        new_pw = form.get("smtp_password", "").strip()
        updates["smtp.password"] = new_pw if new_pw else current["smtp.password"]

        await set_many(session, updates)

    from app.scheduler import apply_settings
    apply_settings(updates)

    await flash("Settings saved.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/test/discord")
@login_required
@admin_required
async def test_discord():
    async with get_db() as session:
        s = await get_all(session)

    webhook_url = s["discord.webhook_url"]
    error = await send_discord(webhook_url, "SpoolBeacon Test-Nachricht: Discord-Integration funktioniert.")
    if error:
        await flash(f"Discord-Test fehlgeschlagen: {error}", "error")
    else:
        await flash("Discord-Test erfolgreich.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/spool-codes/regenerate")
@login_required
@admin_required
async def regenerate_spool_codes():
    from datetime import datetime
    from sqlalchemy import select
    from app.models.spool import Spool

    async with get_db() as session:
        s = await get_all(session)
        template = s.get("spool.code_template", DEFAULT_TEMPLATE)

        spools = (await session.execute(
            select(Spool).order_by(Spool.purchase_line_id, Spool.created_at, Spool.id)
        )).scalars().all()

        now = datetime.utcnow()
        seq_counters: dict[tuple, int] = {}
        updated = 0
        for spool in spools:
            key = (spool.filament_product_id, spool.purchase_line_id)
            seq_counters[key] = seq_counters.get(key, 0) + 1
            new_code = generate_spool_code(
                template,
                product_id=spool.filament_product_id,
                line_id=spool.purchase_line_id,
                seq=seq_counters[key],
                now=now,
            )
            if new_code != spool.spool_code:
                spool.spool_code = new_code
                updated += 1

    await flash(f"Spool codes regenerated: {updated} of {len(spools)} updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/test/email")
@login_required
@admin_required
async def test_email():
    async with get_db() as session:
        s = await get_all(session)

    error = await send_email(
        host=s["smtp.host"],
        port=int(s["smtp.port"] or "587"),
        user=s["smtp.user"],
        password=s["smtp.password"],
        from_addr=s["smtp.from_addr"],
        to_addr=s["smtp.to_addr"],
        subject="SpoolBeacon Test-E-Mail",
        body="SpoolBeacon Test-Nachricht: E-Mail-Integration funktioniert.",
        use_tls=s["smtp.tls"] == "1",
    )
    if error:
        await flash(f"E-Mail-Test fehlgeschlagen: {error}", "error")
    else:
        await flash("E-Mail-Test erfolgreich.", "success")
    return redirect(url_for("settings.index"))
