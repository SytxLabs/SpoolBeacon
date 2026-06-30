"""
Adapter registry — maps domain → adapter instance.

To add a new adapter:
  1. Create app/shop_adapters/_yourshop.py with a BaseAdapter subclass.
  2. Import and register it below.
"""
from .base import BaseAdapter
from ._3djake import ThreeDJakeAdapter
from ._prusa import PrusaAdapter
from ._anycubic import AnycubicAdapter
from ._bambulab import BambuLabAdapter
from ._esun import ESunAdapter
from ._elegoo import ElegooAdapter
from ._ebay import EbayAdapter

_REGISTRY: dict[str, BaseAdapter] = {}


def _reg(adapter: BaseAdapter) -> None:
    _REGISTRY[adapter.domain] = adapter


# ── Confirmed working adapters ────────────────────────────────────────────────
_reg(ThreeDJakeAdapter())     # 3djake.de             — SSR PHP
_reg(PrusaAdapter())          # prusa3d.com            — JSON-LD
_reg(AnycubicAdapter())       # anycubic.com           — Shopify USD
_reg(BambuLabAdapter())       # eu.store.bambulab.com  — JSON-LD EUR, cloudscraper
_reg(ESunAdapter())           # esun3dstore.com        — JSON-LD USD, cloudscraper
_reg(ElegooAdapter())         # elegoo.com             — Shopify og:price:amount USD, confirmed 2026-06-30
_reg(EbayAdapter())           # ebay.de                — .x-price-primary, Playwright required


def get_adapter(domain: str) -> BaseAdapter | None:
    return _REGISTRY.get(domain)


def registered_domains() -> list[str]:
    return sorted(_REGISTRY)


# ── Shops evaluated but not yet supported ─────────────────────────────────────
# Reason codes: blocked | needs_js | needs_api | selector_failed | http_error | wrong_price
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

    # filamentworld.de: adapter removed 2026-06. Use a ShopRule instead.
    # WooCommerce SSR — .price selector was working but has since broken.
    "filamentworld.de": (
        "adapter_removed — Adapter removed; use a ShopRule with the correct .price CSS selector "
        "on a direct product URL (not category page)."
    ),

    # Amazon: product pages accessible but price extraction incorrect.
    # Confirmed responding (not blocked), but extracted price does not match displayed price.
    # ShopRule with correct CSS selector may work on specific product pages.
    "amazon.de": (
        "wrong_price — Page responds but price selector extracts wrong value. "
        "Use a ShopRule and test with the exact product URL + CSS selector. "
        "Alternative: Amazon Product Advertising API (requires affiliate account)."
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

}
