"""
Central price-check service used by the manual route and the scheduler.
Engine is controlled by the 'check.engine' AppSetting: 'playwright' (default) or 'httpx'.

Extraction flow per link:
  1. Fetch HTML via configured engine (playwright / httpx).
  2. If a shop adapter is registered for the link's domain → use it.
  3. Otherwise fall back to the generic ShopRule selector/regex extraction.
  4. If neither adapter nor rule is available → save error snapshot.
"""
import logging
from urllib.parse import urlparse

from app.database import get_db
from app.models.price_snapshot import PriceSnapshot
from app.models.shop_rule import ShopRule
from app.routes.shop_rules import _extract, parse_price
from app.alert_service import maybe_create_alerts, dispatch_alert_notifications
from app.shop_adapters.registry import get_adapter

log = logging.getLogger(__name__)

_HTTPX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
_HTTPX_TIMEOUT = 12.0


async def _fetch_httpx(
    url: str, extra_headers: dict | None = None, warmup_url: str | None = None
) -> tuple[str | None, str | None]:
    import httpx
    headers = {**_HTTPX_HEADERS, **extra_headers} if extra_headers else _HTTPX_HEADERS
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_HTTPX_TIMEOUT, headers=headers
        ) as client:
            if warmup_url:
                await client.get(warmup_url)
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text, None
    except httpx.TimeoutException:
        return None, f"Timeout after {_HTTPX_TIMEOUT:.0f}s"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)[:256]


async def _fetch_cloudscraper(url: str) -> tuple[str | None, str | None]:
    """Sync cloudscraper wrapped in executor — bypasses Cloudflare JS challenges."""
    import asyncio
    import functools

    def _sync():
        import cloudscraper
        sc = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        r = sc.get(url, timeout=20)
        r.raise_for_status()
        return r.text

    try:
        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(None, _sync)
        return html, None
    except Exception as e:
        return None, str(e)[:256]


async def _fetch_playwright(url: str, settings: dict) -> tuple[str | None, str | None]:
    headless = settings.get("playwright.headless", "1") == "1"
    timeout_ms = int(settings.get("playwright.timeout_ms", "30000"))
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                html = await page.content()
                return html, None
            finally:
                await browser.close()
    except Exception as e:
        return None, str(e)[:256]


async def _save_error(link_id: int, currency: str, msg: str) -> None:
    async with get_db() as session:
        session.add(PriceSnapshot(
            shop_link_id=link_id, price=0.0, currency=currency,
            source="error", error_message=msg[:256],
        ))


async def check_price(
    link_id: int,
    link_url: str,
    link_currency: str,
    link_shipping: float | None,
    rule: ShopRule | None,
    target_price: float | None,
    target_price_per_kg: float | None,
    package_weight_g: int,
    shop_name: str,
    filament_name: str,
    settings: dict,
) -> dict:
    """
    Fetch URL, extract price via adapter or ShopRule, save PriceSnapshot, create/resolve alerts.
    Returns dict: {ok, price, currency, availability, alert_types, error}
    """
    _err = {"alert_types": [], "currency": link_currency, "price": None, "availability": None}

    # Resolve adapter first — it may override the fetch engine.
    domain = (urlparse(link_url).hostname or "").removeprefix("www.")
    adapter = get_adapter(domain)

    engine = settings.get("check.engine", "playwright")
    effective_engine = (adapter.fetch_engine if adapter and adapter.fetch_engine else engine)

    if effective_engine == "cloudscraper":
        html, fetch_error = await _fetch_cloudscraper(link_url)
    elif effective_engine == "playwright":
        html, fetch_error = await _fetch_playwright(link_url, settings)
    else:
        html, fetch_error = await _fetch_httpx(
            link_url,
            adapter.fetch_headers(link_url) if adapter else None,
            adapter.warmup_url(link_url) if adapter else None,
        )

    if fetch_error:
        await _save_error(link_id, link_currency, fetch_error)
        log.warning("check [%d] fetch failed (%s): %s", link_id, effective_engine, fetch_error)
        return {"ok": False, "error": fetch_error, **_err}

    if adapter:
        result = adapter.extract(html, link_url)
        if result.status != "success" or result.price_parsed is None:
            msg = result.error_message or f"Adapter '{domain}': {result.status}"
            await _save_error(link_id, link_currency, msg)
            log.warning("check [%d] adapter error (%s): %s", link_id, domain, msg)
            return {"ok": False, "error": msg, **_err}
        price_float = result.price_parsed
        avail_raw   = result.availability
        source      = effective_engine

    elif rule:
        price_raw = _extract(html, rule.price_selector, rule.price_regex)
        avail_raw = _extract(html, rule.availability_selector, rule.availability_regex)

        if not price_raw:
            block = ""
            if "validateCaptcha" in html or "Robot Check" in html:
                block = " — CAPTCHA detected, session required"
            elif "Sign in" in html and "password" in html.lower() and len(html) < 50_000:
                block = " — login wall detected"
            msg = f"Price not found (sel={rule.price_selector!r} re={rule.price_regex!r}){block}"
            await _save_error(link_id, link_currency, msg)
            log.warning("check [%d] rule error: %s", link_id, msg)
            return {"ok": False, "error": msg, **_err}

        try:
            price_float = parse_price(price_raw)
        except (ValueError, AttributeError) as e:
            msg = f"parse_price: {price_raw!r} → {e}"
            await _save_error(link_id, link_currency, msg)
            log.warning("check [%d] parse error: %s", link_id, msg)
            return {"ok": False, "error": msg, **_err}

        source = effective_engine

    else:
        msg = f"No adapter and no active ShopRule for domain '{domain}'"
        await _save_error(link_id, link_currency, msg)
        log.warning("check [%d] no handler: %s", link_id, msg)
        return {"ok": False, "error": msg, **_err}

    # ── persist + alerts ──────────────────────────────────────────────────
    async with get_db() as session:
        snap = PriceSnapshot(
            shop_link_id=link_id,
            price=price_float,
            shipping_price=link_shipping,
            currency=link_currency,
            availability=avail_raw,
            source=source,
        )
        session.add(snap)
        await session.flush()

        alert_types = await maybe_create_alerts(
            session, link_id, snap.id, price_float, link_shipping,
            target_price, target_price_per_kg, package_weight_g, link_currency,
        )

    if alert_types:
        await dispatch_alert_notifications(
            alert_types, filament_name, shop_name, link_url,
            price_float + (link_shipping or 0.0),
            target_price, target_price_per_kg, link_currency,
        )

    log.info("check [%d] %s → %.2f %s%s [%s%s]",
             link_id, link_url, price_float, link_currency,
             f" | {avail_raw}" if avail_raw else "",
             effective_engine, "+adapter" if adapter else "")

    return {
        "ok": True,
        "price": price_float,
        "currency": link_currency,
        "availability": avail_raw,
        "alert_types": alert_types,
        "error": None,
    }
