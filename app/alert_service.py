"""
Shared helper to create and auto-resolve PriceAlertEvents after a successful price check.
- Creates alert if target hit and no active alert exists yet.
- Auto-resolves active alert if target is no longer met.
"""
import logging
from datetime import datetime
from sqlalchemy import select
from app.models.price_alert_event import PriceAlertEvent

log = logging.getLogger(__name__)


async def dispatch_alert_notifications(
    alert_types: list[str],
    filament_name: str,
    shop_name: str,
    shop_url: str,
    snap_total: float,
    target_price: float | None,
    target_price_per_kg: float | None,
    currency: str,
) -> None:
    """Send Discord/email notifications for newly created alerts. Errors only logged."""
    if not alert_types:
        return

    try:
        from app.database import get_db
        from app.settings_service import get_all
        from app.notification_service import (
            send_discord, send_email,
            build_price_alert_discord_embed, build_price_alert_email_html,
        )

        async with get_db() as session:
            s = await get_all(session)

        discord_on = s["discord.enabled"] == "1" and bool(s["discord.webhook_url"])
        smtp_on = (
            s["smtp.enabled"] == "1"
            and bool(s["smtp.host"])
            and bool(s["smtp.from_addr"])
            and bool(s["smtp.to_addr"])
        )

        if not discord_on and not smtp_on:
            return

        for alert_type in alert_types:
            if alert_type == "target_price" and target_price is not None:
                target_str = f"{target_price:.2f} {currency}"
                type_label = "Target price"
            elif alert_type == "target_price_per_kg" and target_price_per_kg is not None:
                target_str = f"{target_price_per_kg:.2f} {currency}/kg"
                type_label = "Target price/kg"
            else:
                continue

            message = (
                f"Target price reached: {filament_name}\n"
                f"Shop: {shop_name}\n"
                f"Price: {snap_total:.2f} {currency}  |  {type_label}: {target_str}\n"
                f"{shop_url}"
            )

            if discord_on:
                embed = build_price_alert_discord_embed(
                    filament_name, shop_name, shop_url, snap_total, currency, type_label, target_str,
                )
                err = await send_discord(s["discord.webhook_url"], embeds=[embed])
                if err:
                    log.error("discord notify failed (type=%s): %s", alert_type, err)

            if smtp_on:
                html_body = build_price_alert_email_html(
                    filament_name, shop_name, shop_url, snap_total, currency, type_label, target_str,
                )
                err = await send_email(
                    host=s["smtp.host"],
                    port=int(s["smtp.port"] or "587"),
                    user=s["smtp.user"],
                    password=s["smtp.password"],
                    from_addr=s["smtp.from_addr"],
                    to_addr=s["smtp.to_addr"],
                    subject=f"SpoolBeacon: {type_label} reached – {filament_name}",
                    body=message,
                    html_body=html_body,
                    use_tls=s["smtp.tls"] == "1",
                )
                if err:
                    log.error("email notify failed (type=%s): %s", alert_type, err)

    except Exception:
        log.exception("dispatch_alert_notifications unexpected error")


async def maybe_create_alerts(
    session,
    link_id: int,
    snap_id: int | None,
    snap_price: float,
    snap_shipping: float | None,
    target_price: float | None,
    target_price_per_kg: float | None,
    package_weight_g: int,
    currency: str,
) -> list[str]:
    """Create or auto-resolve PriceAlertEvents based on current snapshot.
    Returns list of alert_types that were newly created.
    """
    snap_total = snap_price + (snap_shipping or 0.0)
    snap_per_kg = round(snap_total / package_weight_g * 1000, 2) if package_weight_g else None
    now = datetime.utcnow()

    # ── auto-resolve: conditions no longer met ────────────────────────────
    for alert_type, still_hit in (
        ("target_price",        target_price is not None and snap_total <= target_price),
        ("target_price_per_kg", target_price_per_kg is not None and snap_per_kg is not None
                                and snap_per_kg <= target_price_per_kg),
    ):
        if not still_hit:
            stale = await session.scalar(
                select(PriceAlertEvent).where(
                    PriceAlertEvent.shop_link_id == link_id,
                    PriceAlertEvent.alert_type == alert_type,
                    PriceAlertEvent.resolved_at.is_(None),
                )
            )
            if stale:
                stale.resolved_at = now
                log.info("alert auto-resolved: link=%d type=%s", link_id, alert_type)

    # ── create: conditions newly met ─────────────────────────────────────
    candidates: list[tuple[str, str]] = []
    if target_price is not None and snap_total <= target_price:
        candidates.append((
            "target_price",
            f"Price {snap_total:.2f} {currency} <= target {target_price:.2f} {currency}",
        ))
    if target_price_per_kg is not None and snap_per_kg is not None and snap_per_kg <= target_price_per_kg:
        candidates.append((
            "target_price_per_kg",
            f"Price/kg {snap_per_kg:.2f} {currency} <= target {target_price_per_kg:.2f} {currency}/kg",
        ))

    created = []
    for alert_type, message in candidates:
        existing = await session.scalar(
            select(PriceAlertEvent).where(
                PriceAlertEvent.shop_link_id == link_id,
                PriceAlertEvent.alert_type == alert_type,
                PriceAlertEvent.resolved_at.is_(None),
            )
        )
        if not existing:
            session.add(PriceAlertEvent(
                shop_link_id=link_id,
                price_snapshot_id=snap_id,
                alert_type=alert_type,
                message=message,
            ))
            created.append(alert_type)
            log.info("alert created: link=%d type=%s", link_id, alert_type)

    return created
