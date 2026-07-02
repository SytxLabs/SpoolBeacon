"""
Shop seed — ShopRules only, no filament/inventory data.
Safe to run standalone or alongside seed.py. Idempotent (skips existing domains).

Domains with a registered adapter (see app.shop_adapters.registry.registered_domains())
are intentionally NOT seeded here — the adapter always takes priority over a ShopRule
for the same domain, so a rule for it would be dead weight and confusing in the UI.
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import _build_database_url
from app.models.shop_rule import ShopRule  # noqa: F401 — import ensures model is registered

RULES = [
    # ── Rule-only shops (no adapter, httpx/Playwright fetch) ─────────────────
    dict(
        domain="amazon.de",
        price_selector=".a-price .a-offscreen",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector=None,
        availability_selector="#availability span",
        availability_regex=None,
        currency="EUR",
        test_url="",
        is_active=True,
        notes="BLOCKED — ASIN pages return 404 without session. Future: Amazon Product Advertising API.",
    ),
    dict(
        domain="ebay.de",
        price_selector=".x-price-primary .ux-textspans",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector=None,
        availability_selector=None,
        availability_regex=None,
        currency="EUR",
        test_url="",
        is_active=True,
        notes="BLOCKED — Cloudflare (httpx + Playwright + cloudscraper). Future: eBay Browse API.",
    ),
    dict(
        domain="sunlu.com",
        price_selector="[class*='price']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector=None,
        availability_selector=None,
        availability_regex=None,
        currency="USD",
        test_url="https://store.sunlu.com/products/over-6kg-of-pla-pla-meta-3d-filaments-1kg-2-2lbs-fit-most-of-fdm-printer",
        is_active=True,
        notes="HTTP 500 on collection pages. Server instability or geo-blocking.",
    ),
    dict(
        domain="shop.polymaker.com",
        price_selector=".price-item--regular",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector=None,
        availability_selector=None,
        availability_regex=None,
        currency="USD",
        test_url="https://shop.polymaker.com/en-eu/products/panchroma-matte?variant=43631458549817",
        is_active=True,
        notes="Marketing/product-info site only — no purchasable prices. Sold via distributors.",
    ),
]


async def run() -> None:
    engine = create_async_engine(_build_database_url(), echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        added = 0
        skipped = 0
        for rule_data in RULES:
            existing = (await session.execute(
                select(ShopRule).where(ShopRule.domain == rule_data["domain"])
            )).scalar_one_or_none()
            if existing:
                skipped += 1
                continue
            session.add(ShopRule(**rule_data))
            added += 1

        await session.commit()

    await engine.dispose()
    print(f"seed_shops: {added} rules added, {skipped} already existed.")


if __name__ == "__main__":
    asyncio.run(run())
