import asyncio
import logging
import smtplib
import ssl
from email.mime.text import MIMEText

import httpx

log = logging.getLogger(__name__)


async def send_discord(webhook_url: str, message: str) -> str | None:
    """POST message to Discord webhook. Returns error string or None on success."""
    if not webhook_url:
        return "Keine Webhook-URL konfiguriert."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json={"content": message})
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
) -> str | None:
    """Send email via SMTP. Returns error string or None on success."""
    if not host or not from_addr or not to_addr:
        return "SMTP-Konfiguration unvollständig (Host, From, To erforderlich)."

    def _send() -> None:
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
