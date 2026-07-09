"""
Periodic price check scheduler.
Enabled/interval controlled via AppSetting (settings page), not ENV.
APScheduler ticks every minute; actual check interval is read from DB each tick.
"""
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.filament import FilamentProduct
from app.models.price_snapshot import PriceSnapshot
from app.models.shop_rule import ShopRule
from app.models.shoplink import ShopLink
from app.price_check_service import check_price

log = logging.getLogger(__name__)

_INTER_REQUEST_DELAY = 0.75
_INTER_BATCH_DELAY = 3.0
_BATCH_SIZE = 10

_instance: "AsyncIOScheduler | None" = None
_last_run: "datetime | None" = None
_enabled: bool = False
_interval_minutes: int = 0


def get_status() -> dict:
    """Return scheduler status dict for the dashboard."""
    if _instance is None or not _instance.running:
        return {"enabled": False, "interval_minutes": _interval_minutes,
                "next_run": None, "last_run": _last_run}
    job = _instance.get_job("price_check")
    return {
        "enabled": _enabled,
        "interval_minutes": _interval_minutes,
        "next_run": job.next_run_time if job else None,
        "last_run": _last_run,
    }


async def run_price_checks() -> None:
    """Entry point for the scheduled job. Reads all config from AppSetting."""
    global _last_run, _enabled, _interval_minutes

    from app.settings_service import get_all
    async with get_db() as session:
        s = await get_all(session)

    _enabled = s.get("scheduler.enabled") == "1"
    _interval_minutes = int(s.get("scheduler.interval_minutes", "360"))

    if not _enabled:
        return

    if _last_run is not None:
        elapsed_min = (datetime.utcnow() - _last_run).total_seconds() / 60
        if elapsed_min < _interval_minutes:
            return

    min_interval = int(s.get("scheduler.min_interval_minutes", "60"))
    max_concurrent = max(1, int(s.get("playwright.max_concurrent", "1")))

    log.info("price-check run started")

    async with get_db() as session:
        links = (await session.execute(
            select(ShopLink)
            .options(selectinload(ShopLink.filament_product).selectinload(FilamentProduct.manufacturer))
            .where(ShopLink.is_active.is_(True))
        )).scalars().all()

        rules_list = (await session.execute(
            select(ShopRule).where(ShopRule.is_active.is_(True))
        )).scalars().all()

        link_ids = [lnk.id for lnk in links]
        last_checked: dict[int, datetime] = {}
        if link_ids and min_interval > 0:
            rows = (await session.execute(
                select(PriceSnapshot.shop_link_id, func.max(PriceSnapshot.captured_at))
                .where(PriceSnapshot.shop_link_id.in_(link_ids))
                .group_by(PriceSnapshot.shop_link_id)
            )).all()
            last_checked = {row[0]: row[1] for row in rows}

    from app.shop_adapters.registry import get_adapter
    rules_by_domain = {r.domain: r for r in rules_list}
    now = datetime.utcnow()

    work = []
    skipped = 0
    for link in links:
        host = urlparse(link.url).hostname or ""
        domain = host.removeprefix("www.")
        rule = rules_by_domain.get(domain)
        if not rule and not get_adapter(domain):
            continue
        if min_interval > 0 and link.id in last_checked:
            age_min = (now - last_checked[link.id]).total_seconds() / 60
            if age_min < min_interval:
                skipped += 1
                log.debug("skip [%d] age=%.0fmin < min=%dmin", link.id, age_min, min_interval)
                continue
        work.append({
            "link_id": link.id,
            "link_url": link.url,
            "link_currency": link.currency,
            "link_shipping": link.shipping_price,
            "rule": rule,
            "target_price": link.target_price,
            "target_price_per_kg": link.target_price_per_kg,
            "package_weight_g": link.package_weight_g,
            "shop_name": link.shop_name,
            "filament_name": link.filament_product.display_name if link.filament_product else "",
        })

    if skipped:
        log.info("price-check: %d link(s) skipped (min-interval %dmin)", skipped, min_interval)

    if not work:
        log.info("price-check: no eligible links (no matching rule or all within min-interval)")
        return

    log.info("price-check: %d link(s) to check", len(work))
    sem = asyncio.Semaphore(max_concurrent)

    async def bounded(kwargs: dict) -> None:
        async with sem:
            await check_price(**kwargs, settings=s)
            await asyncio.sleep(_INTER_REQUEST_DELAY)

    for i in range(0, len(work), _BATCH_SIZE):
        batch = work[i : i + _BATCH_SIZE]
        await asyncio.gather(*[bounded(kw) for kw in batch])
        if i + _BATCH_SIZE < len(work):
            await asyncio.sleep(_INTER_BATCH_DELAY)

    _last_run = datetime.utcnow()
    log.info("price-check run done (%d link(s))", len(work))


async def init_status() -> None:
    """Read initial enabled/interval from DB so dashboard is accurate from first request."""
    global _enabled, _interval_minutes
    try:
        from app.settings_service import get_all
        async with get_db() as session:
            s = await get_all(session)
        _enabled = s.get("scheduler.enabled") == "1"
        _interval_minutes = int(s.get("scheduler.interval_minutes", "360"))
        log.info("scheduler init: enabled=%s interval=%dmin", _enabled, _interval_minutes)
    except Exception as exc:
        log.warning("scheduler init_status failed (DB not ready?): %s", exc)


def apply_settings(settings: dict) -> None:
    """Update in-memory state immediately after settings are saved — no 1-min delay."""
    global _enabled, _interval_minutes
    _enabled = settings.get("scheduler.enabled") == "1"
    _interval_minutes = int(settings.get("scheduler.interval_minutes", "360"))


def create_scheduler() -> AsyncIOScheduler:
    global _instance
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_price_checks,
        trigger="interval",
        minutes=1,
        id="price_check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    _instance = scheduler
    log.info("scheduler configured: 1-min heartbeat, enabled/interval from AppSetting")
    return scheduler
