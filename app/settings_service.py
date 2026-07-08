from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

_DEFAULTS: dict[str, str] = {
    # localization
    "app.language": "en",
    # spool codes
    "spool.code_template": "SB-{product_id}-{line_id}-{timestamp}-{seq:02d}",
    # scheduler
    "scheduler.enabled": "0",
    "scheduler.interval_minutes": "360",
    "scheduler.min_interval_minutes": "60",
    # price-check engine
    "check.engine": "playwright",
    "playwright.headless": "1",
    "playwright.timeout_ms": "30000",
    "playwright.max_concurrent": "1",
    # discord
    "discord.enabled": "0",
    "discord.webhook_url": "",
    # smtp
    "smtp.enabled": "0",
    "smtp.host": "",
    "smtp.port": "587",
    "smtp.user": "",
    "smtp.password": "",
    "smtp.from_addr": "",
    "smtp.to_addr": "",
    "smtp.tls": "1",
}


async def get_all(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(AppSetting))).scalars().all()
    result = dict(_DEFAULTS)
    for row in rows:
        if row.key in result:
            result[row.key] = row.value if row.value is not None else ""
    return result


async def set_many(session: AsyncSession, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        row = await session.scalar(select(AppSetting).where(AppSetting.key == key))
        if row:
            row.value = value
        else:
            session.add(AppSetting(key=key, value=value))
