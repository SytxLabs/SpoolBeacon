"""
Adapter registry — maps domain → adapter instance.

To add a new adapter:
  1. Create app/shop_adapters/_yourshop.py with a BaseAdapter subclass.
  2. Import and register it below.

Tested 2026-06-30 with httpx + headless Playwright (Chromium).
"""
from .base import BaseAdapter
from ._3djake import ThreeDJakeAdapter
from ._prusa import PrusaAdapter
from ._anycubic import AnycubicAdapter
from ._filamentworld import FilamentworldAdapter
from ._bambulab import BambuLabAdapter
from ._esun import ESunAdapter

_REGISTRY: dict[str, BaseAdapter] = {}


def _reg(adapter: BaseAdapter) -> None:
    _REGISTRY[adapter.domain] = adapter


# ── Confirmed working adapters ────────────────────────────────────────────────
_reg(ThreeDJakeAdapter())     # 3djake.de             — SSR PHP, confirmed 2026-06-29
_reg(PrusaAdapter())          # prusa3d.com            — JSON-LD, confirmed 2026-06-29
_reg(AnycubicAdapter())       # anycubic.com           — Shopify USD, confirmed 2026-06-30
_reg(FilamentworldAdapter())  # filamentworld.de       — WooCommerce EUR, confirmed 2026-06-30
_reg(BambuLabAdapter())       # eu.store.bambulab.com  — JSON-LD EUR, cloudscraper, confirmed 2026-06-30
_reg(ESunAdapter())           # esun3dstore.com        — JSON-LD USD, cloudscraper, confirmed 2026-06-30


def get_adapter(domain: str) -> BaseAdapter | None:
    return _REGISTRY.get(domain)


def registered_domains() -> list[str]:
    return sorted(_REGISTRY)


# ── Shops evaluated but not yet supported ─────────────────────────────────────
# Reason codes: blocked | needs_js | needs_api | selector_failed | http_error
PLANNED: dict[str, str] = {
    # bambulab.com (main site) is blocked by Cloudflare even with cloudscraper.
    # Use eu.store.bambulab.com instead (supported — see BambuLabAdapter above).
    "bambulab.com": (
        "blocked — Main site Cloudflare WAF blocks httpx, Playwright, and cloudscraper. "
        "Use eu.store.bambulab.com product pages instead (adapter supported)."
    ),
    # polymaker.com: HTTP 200 with cloudscraper but page is a marketing site, no store prices.
    # Resellers like 3DJake carry Polymaker products.
    "polymaker.com": (
        "no_store — polymaker.com is a marketing/product-info site with no purchasable prices. "
        "Products sold via distributors (3DJake, Amazon, etc.)."
    ),

    # Amazon: product ASIN URLs return 404 in both httpx and Playwright (region/session issue).
    # Search page returns JS soup with no extractable product price.
    # Do NOT use cloudscraper.
    "amazon.de": (
        "blocked — ASIN product pages return 404 without authenticated session. "
        "Search page accessible but no product price extractable. "
        "Future: Amazon Product Advertising API (requires affiliate account). "
        "Do NOT use cloudscraper."
    ),

    # eBay: all URLs return Cloudflare 'Error Page' even with Playwright.
    "ebay.de": (
        "blocked — Cloudflare blocks all requests (httpx + Playwright). "
        "Future: eBay Finding API or Browse API (requires app registration)."
    ),

    # AliExpress: HTTP 200 but entirely JS-rendered — headless Playwright returns
    # empty body (anti-bot fingerprinting or heavy lazy loading).
    "aliexpress.com": (
        "needs_js — Page accessible (HTTP 200) but content is fully JS-rendered. "
        "Headless Playwright returns empty body due to anti-bot fingerprinting."
    ),

    # esun3d.com is the brand/marketing site — prices are JS-rendered, not scrapeable.
    # Use esun3dstore.com (Shopify) instead — supported via ESunAdapter above.
    "esun3d.com": (
        "needs_js — Brand/marketing site, prices are JS-rendered. "
        "Use esun3dstore.com Shopify store instead (adapter supported)."
    ),

    # Sunlu: HTTP 500 — server error on product/collection pages.
    "sunlu.com": (
        "http_error — HTTP 500 on collection pages. Server instability or geo-blocking. "
        "Re-test when site is stable."
    ),

    # Creality: Product page URLs unclear, tested URLs returned 404.
    # Site accessible but correct product URL structure needs investigation.
    "creality.com": (
        "selector_failed — Correct product URL structure unclear; tested paths return 404. "
        "Main site returns HTTP 200. Re-test with valid product URL from store."
    ),

    # Elegoo: HTTP 404 on all tested product URLs. Shop URL structure may have changed.
    "elegoo.com": (
        "http_error — All tested product URLs return HTTP 404. "
        "URL structure may have changed; re-test with current product links from elegoo.com."
    ),
}
