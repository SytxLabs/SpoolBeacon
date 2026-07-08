import json
from datetime import datetime
from functools import wraps

from quart import Blueprint, render_template, request, redirect, url_for, flash, abort, Response
from quart_auth import login_required, current_user
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, UserRole
from app.settings_service import get_all, set_many
from app.notification_service import send_discord, send_email, build_test_discord_embed, build_test_email_html
from app.models.spool import Spool
from app.spool_code import generate_spool_code, DEFAULT_TEMPLATE, AVAILABLE_VARS
from app.import_export_service import export_bundle, import_bundle
from app.i18n import t, SUPPORTED_LOCALES, DEFAULT_LOCALE

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
    return await render_template(
        "settings/index.html", s=s, available_vars=AVAILABLE_VARS, available_locales=SUPPORTED_LOCALES
    )


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

        language = form.get("app_language", "").strip()
        updates: dict[str, str] = {
            # localization
            "app.language": language if language in SUPPORTED_LOCALES else DEFAULT_LOCALE,
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

    await flash(t("settings.flash.saved"), "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/test/discord")
@login_required
@admin_required
async def test_discord():
    async with get_db() as session:
        s = await get_all(session)

    webhook_url = s["discord.webhook_url"]
    error = await send_discord(webhook_url, embeds=[build_test_discord_embed()])
    if error:
        await flash(t("settings.flash.discord_test_failed", error=error), "error")
    else:
        await flash(t("settings.flash.discord_test_success"), "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/spool-codes/regenerate")
@login_required
@admin_required
async def regenerate_spool_codes():
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

    await flash(t("settings.flash.spool_codes_regenerated", updated=updated, total=len(spools)), "success")
    return redirect(url_for("settings.index"))


@settings_bp.get("/export")
@login_required
@admin_required
async def export_data():
    async with get_db() as session:
        data = await export_bundle(session)

    payload = json.dumps(data, indent=2)
    filename = f"spoolbeacon-export-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@settings_bp.post("/import")
@login_required
@admin_required
async def import_data():
    files = await request.files
    upload = files.get("import_file")
    if not upload or not upload.filename:
        await flash(t("settings.flash.import_no_file"), "error")
        return redirect(url_for("settings.index"))

    try:
        data = json.loads(upload.read())
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        await flash(t("settings.flash.import_invalid_json"), "error")
        return redirect(url_for("settings.index"))

    async with get_db() as session:
        counts = await import_bundle(session, data)

    labels = {
        "manufacturers": t("settings.import_export.labels.manufacturers"),
        "products": t("settings.import_export.labels.products"),
        "shop_links": t("settings.import_export.labels.shop_links"),
        "purchases": t("settings.import_export.labels.purchases"),
        "purchase_lines": t("settings.import_export.labels.purchase_lines"),
        "spools": t("settings.import_export.labels.spools"),
        "shop_rules": t("settings.import_export.labels.shop_rules"),
    }
    summary = "; ".join(
        t("settings.import_export.summary_entry", label=labels[k], added=added, skipped=skipped)
        for k, (added, skipped) in counts.items() if added or skipped
    )
    await flash(
        t("settings.flash.import_complete", summary=summary) if summary else t("settings.flash.import_complete_empty"),
        "success",
    )
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
        subject="SpoolBeacon Test Email",
        body="SpoolBeacon test message: email integration is working.",
        html_body=build_test_email_html("emails via your SMTP server"),
        use_tls=s["smtp.tls"] == "1",
    )
    if error:
        await flash(t("settings.flash.email_test_failed", error=error), "error")
    else:
        await flash(t("settings.flash.email_test_success"), "success")
    return redirect(url_for("settings.index"))
