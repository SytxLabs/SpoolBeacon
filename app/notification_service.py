import asyncio
import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

log = logging.getLogger(__name__)


async def send_discord(webhook_url: str, message: str = "", embeds: list[dict] | None = None) -> str | None:
    """POST message/embeds to Discord webhook. Returns error string or None on success."""
    if not webhook_url:
        return "No webhook URL configured."
    payload: dict = {}
    if message:
        payload["content"] = message
    if embeds:
        payload["embeds"] = embeds
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        if resp.status_code not in (200, 204):
            return f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return str(e)
    return None


async def send_email(
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    use_tls: bool,
    html_body: str | None = None,
) -> str | None:
    """Send email via SMTP. Sends multipart plain+HTML if html_body given. Returns error string or None on success."""
    if not host or not from_addr or not to_addr:
        return "SMTP configuration incomplete (host, from, and to are required)."

    def _send() -> None:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr

        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [to_addr], msg.as_bytes())
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if use_tls:
                    smtp.starttls(context=ctx)
                if user:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [to_addr], msg.as_bytes())

    try:
        await asyncio.to_thread(_send)
    except Exception as e:
        return str(e)
    return None


# ── Discord embed builders ──────────────────────────────────────────────────

def _discord_embed(*, title: str, description: str = "", color: int = 0x6366F1,
                    url: str | None = None, fields: list[tuple[str, str]] | None = None) -> dict:
    embed: dict = {"title": title, "color": color, "footer": {"text": "SpoolBeacon"},
                   "timestamp": datetime.utcnow().isoformat()}
    if description:
        embed["description"] = description
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = [{"name": name, "value": value, "inline": True} for name, value in fields]
    return embed


def build_price_alert_discord_embed(
    filament_name: str, shop_name: str, shop_url: str,
    snap_total: float, currency: str, type_label: str, target_str: str,
) -> dict:
    return _discord_embed(
        title=f"🎯 {type_label} reached",
        description=f"**{filament_name}**",
        color=0x10B981,
        url=shop_url,
        fields=[
            ("Shop", shop_name),
            ("Price", f"{snap_total:.2f} {currency}"),
            (type_label, target_str),
        ],
    )


def build_test_discord_embed() -> dict:
    return _discord_embed(
        title="✅ Connection successful",
        description="Discord integration is working.",
        color=0x5865F2,
    )


# ── HTML email builders ─────────────────────────────────────────────────────

def _email_card_html(*, badge: str, badge_color: str, title: str, intro: str,
                      rows: list[tuple[str, str]] | None = None,
                      cta_label: str | None = None, cta_url: str | None = None) -> str:
    rows_html = ""
    if rows:
        row_items = "".join(
            f'<tr>'
            f'<td style="padding:9px 0;color:#8b8b93;font-size:13px;border-top:1px solid #27272a;">{name}</td>'
            f'<td style="padding:9px 0;color:#f4f4f5;font-size:13px;font-weight:600;'
            f'text-align:right;border-top:1px solid #27272a;">{value}</td>'
            f'</tr>'
            for name, value in rows
        )
        rows_html = f'<table style="width:100%;border-collapse:collapse;margin-top:18px;">{row_items}</table>'

    cta_html = ""
    if cta_label and cta_url:
        cta_html = (
            f'<a href="{cta_url}" '
            f'style="display:inline-block;margin-top:22px;padding:11px 20px;background:{badge_color};'
            f'color:#0a0a0a;font-weight:700;font-size:13px;text-decoration:none;border-radius:10px;">'
            f'{cta_label}</a>'
        )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#0a0a0a;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:480px;margin:0 auto;background:#141416;border:1px solid #27272a;border-radius:16px;overflow:hidden;">
    <div style="padding:18px 24px;border-bottom:1px solid #27272a;">
      <span style="font-weight:700;color:#ffffff;font-size:14px;letter-spacing:-.01em;">SpoolBeacon</span>
    </div>
    <div style="padding:26px 24px;">
      <span style="display:inline-block;padding:4px 10px;border-radius:999px;background:{badge_color}22;
                   color:{badge_color};font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;">
        {badge}
      </span>
      <h1 style="margin:14px 0 6px;color:#ffffff;font-size:19px;font-weight:700;">{title}</h1>
      <p style="margin:0;color:#a1a1aa;font-size:13px;line-height:1.5;">{intro}</p>
      {rows_html}
      {cta_html}
    </div>
    <div style="padding:14px 24px;border-top:1px solid #27272a;color:#52525b;font-size:11px;">
      SpoolBeacon &middot; Filament Inventory &amp; Price Tracking
    </div>
  </div>
</body>
</html>"""


def build_price_alert_email_html(
    filament_name: str, shop_name: str, shop_url: str,
    snap_total: float, currency: str, type_label: str, target_str: str,
) -> str:
    return _email_card_html(
        badge="Price Alert",
        badge_color="#10b981",
        title=f"Target price reached: {filament_name}",
        intro=f"The price at {shop_name} has dropped below your {type_label.lower()}.",
        rows=[
            ("Shop", shop_name),
            ("Price", f"{snap_total:.2f} {currency}"),
            (type_label, target_str),
        ],
        cta_label="View offer",
        cta_url=shop_url,
    )


def build_test_email_html(channel_label: str) -> str:
    return _email_card_html(
        badge="Test",
        badge_color="#6366f1",
        title="Connection successful",
        intro=f"This test message confirms that SpoolBeacon can send {channel_label}.",
    )
