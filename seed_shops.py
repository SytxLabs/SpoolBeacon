"""
Shop seed — ShopRules only, no filament/inventory data.
Safe to run standalone or alongside seed.py. Idempotent (skips existing domains).

Shops with a registered adapter (3djake.de, prusa3d.com, anycubic.com,
filamentworld.de, eu.store.bambulab.com, esun3dstore.com) don't need a ShopRule
for price extraction — the adapter takes priority. The rules below are included
as fallbacks and for reference only.
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import _build_database_url
from app.models.shop_rule import ShopRule  # noqa: F401 — import ensures model is registered

RULES = [
    # ── Adapter-backed (adapter takes priority; rule is fallback/reference) ──
    dict(
        domain="3djake.de",
        price_selector=".price",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        availability_selector="[class*='availab']",
        currency="EUR",
        test_url="https://www.3djake.de/bambu-lab/pla-basic-white",
        is_active=True,
        notes=(
            "SSR PHP page. Price incl. VAT. "
            "Adapter available (3djake.de) — adapter takes priority over this rule. "
            "Confirmed working 2026-06-29."
        ),
    ),
    dict(
        domain="prusa3d.com",
        price_selector="script[type='application/ld+json']",
        price_regex=r'"price"\s*:\s*"?([\d.]+)"?',
        title_selector="h1",
        availability_selector="script[type='application/ld+json']",
        availability_regex=r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"',
        currency="EUR",
        test_url="https://www.prusa3d.com/de/produkt/prusament-petg-prusa-orange-1kg/",
        is_active=True,
        notes=(
            "WooCommerce + Schema.org JSON-LD. Price as float (29.990000). "
            "Adapter available (prusa3d.com) — adapter takes priority. "
            "Confirmed working 2026-06-29."
        ),
    ),
    dict(
        domain="anycubic.com",
        price_selector=".price-item--regular.bold",
        price_regex=r"\d+\.\d{2}",
        title_selector="h1",
        currency="USD",
        test_url="https://www.anycubic.com/products/pla-filament",
        is_active=True,
        notes=(
            "Shopify USD store. SSR page — no JS required. "
            "Adapter available (anycubic.com) — adapter takes priority. "
            "Confirmed working 2026-06-30."
        ),
    ),
    dict(
        domain="filamentworld.de",
        price_selector=".price",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        availability_selector=".stock",
        currency="EUR",
        test_url="https://filamentworld.de/shop/filament-3d-drucker/pla-filament-1-75-mm-braun/?switch_shop=b2c",
        is_active=True,
        notes=(
            "WooCommerce EUR. Use direct product URLs (not category pages). "
            "Adapter available (filamentworld.de) — adapter skips 0,00€ placeholder. "
            "Confirmed working 2026-06-30."
        ),
    ),
    dict(
        domain="eu.store.bambulab.com",
        price_selector="script[type='application/ld+json']",
        price_regex=r'"price"\s*:\s*"?([\d.]+)"?',
        title_selector="h1",
        availability_selector="script[type='application/ld+json']",
        availability_regex=r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"',
        currency="EUR",
        test_url="https://eu.store.bambulab.com/en/products/pla-basic-filament",
        is_active=True,
        notes=(
            "Bambu Lab EU Shopify store. Requires cloudscraper (Cloudflare). "
            "Adapter available (eu.store.bambulab.com) handles cloudscraper fetch automatically. "
            "Do NOT use bambulab.com — blocked by Cloudflare. "
            "Confirmed working 2026-06-30."
        ),
    ),
    dict(
        domain="esun3dstore.com",
        price_selector="script[type='application/ld+json']",
        price_regex=r'"price"\s*:\s*"?([\d.]+)"?',
        title_selector="h1",
        availability_selector="script[type='application/ld+json']",
        availability_regex=r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"',
        currency="USD",
        test_url="https://esun3dstore.com/products/pla-pro-2-rolls",
        is_active=True,
        notes=(
            "eSUN Shopify store (USD). Requires cloudscraper. "
            "Adapter available (esun3dstore.com) handles cloudscraper fetch automatically. "
            "Note: esun3d.com is the marketing site — prices are JS-rendered there. "
            "Confirmed working 2026-06-30."
        ),
    ),

    # ── Rule-only shops (no adapter, httpx/Playwright fetch) ─────────────────
    dict(
        domain="amazon.de",
        price_selector=".a-price .a-offscreen",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="#productTitle",
        availability_selector="#availability span",
        currency="EUR",
        test_url="",
        is_active=False,
        notes=(
            "BLOCKED — ASIN product pages return empty/404 without authenticated session. "
            "Selectors are correct for a real browser session but not for automated fetching. "
            "Future: Amazon Product Advertising API (requires affiliate account)."
        ),
    ),
    dict(
        domain="ebay.de",
        price_selector=".x-price-primary .ux-textspans",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1.x-item-title__mainTitle",
        currency="EUR",
        test_url="",
        is_active=False,
        notes=(
            "BLOCKED — Cloudflare blocks httpx, Playwright, and cloudscraper. "
            "eBay HTML structure changes frequently. "
            "Future: eBay Browse API or Finding API (requires app registration)."
        ),
    ),
    dict(
        domain="aliexpress.com",
        price_selector="[class*='price--current']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        currency="EUR",
        test_url="",
        is_active=False,
        notes=(
            "JS-rendered — headless Playwright returns empty body due to anti-bot fingerprinting. "
            "httpx gets HTTP 200 but no product content."
        ),
    ),
    dict(
        domain="bambulab.com",
        price_selector="[class*='price']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        currency="EUR",
        test_url="https://bambulab.com/de-de/filament/pla-basic",
        is_active=False,
        notes=(
            "BLOCKED — Main site Cloudflare WAF blocks httpx, Playwright, and cloudscraper. "
            "Use eu.store.bambulab.com instead (adapter + rule both available for that domain)."
        ),
    ),
    dict(
        domain="sunlu.com",
        price_selector="[class*='price']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        currency="USD",
        test_url="",
        is_active=False,
        notes="HTTP 500 on collection pages. Server instability or geo-blocking. Re-test when stable.",
    ),
    dict(
        domain="creality.com",
        price_selector="[class*='price']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        currency="USD",
        test_url="",
        is_active=False,
        notes=(
            "Correct product URL structure unclear — tested paths return 404. "
            "Re-test with a valid product URL from the store."
        ),
    ),
    dict(
        domain="elegoo.com",
        price_selector="[class*='price']",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h1",
        currency="USD",
        test_url="",
        is_active=False,
        notes=(
            "All tested product URLs return HTTP 404. "
            "URL structure may have changed — re-test with current product links."
        ),
    ),
    dict(
        domain="polymaker.com",
        price_selector=".price-item--regular",
        price_regex=r"\d+[,\.]\d{2}",
        title_selector="h2",
        currency="USD",
        test_url="https://polymaker.com/product/polyterra-pla/",
        is_active=False,
        notes=(
            "Marketing/product-info site only — no purchasable prices on polymaker.com. "
            "Products sold via distributors (3DJake, Amazon, etc.)."
        ),
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
