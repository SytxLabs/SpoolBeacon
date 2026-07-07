"""
Prusa Shop adapter — WooCommerce + JSON-LD, confirmed working 2026-06-29.
Price extracted from <script type="application/ld+json"> as float string (e.g. "29.990000").
Plain httpx works fine (~650KB SSR page), no need for Playwright.
"""
from app.routes.shop_rules import _extract, parse_price
from .base import BaseAdapter, AdapterResult

_AVAIL_MAP = {
    "instock":    "In Stock",
    "outofstock": "Out of Stock",
    "preorder":   "Pre-order",
}


class PrusaAdapter(BaseAdapter):
    domains = ("prusa3d.com",)
    fetch_engine = "httpx"

    _PRICE_SEL = "script[type='application/ld+json']"
    _PRICE_RE  = r'"price"\s*:\s*"?([\d.]+)"?'
    _TITLE_SEL = "h1"
    _AVAIL_SEL = "script[type='application/ld+json']"
    _AVAIL_RE  = r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"'

    def extract(self, html: str, url: str) -> AdapterResult:
        title     = _extract(html, self._TITLE_SEL, None)
        price_raw = _extract(html, self._PRICE_SEL, self._PRICE_RE)
        avail_raw = _extract(html, self._AVAIL_SEL, self._AVAIL_RE)

        # Normalise schema.org availability token → human label
        availability = None
        if avail_raw:
            token = avail_raw.split("/")[-1].lower().replace(" ", "")
            availability = _AVAIL_MAP.get(token, avail_raw)

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="JSON-LD 'price' field not found — page structure may have changed",
                title=title,
                availability=availability,
            )

        try:
            price_parsed = parse_price(price_raw)
        except (ValueError, AttributeError) as e:
            return AdapterResult(
                status="error",
                price_raw=price_raw,
                error_message=f"parse_price failed: {price_raw!r} → {e}",
                title=title,
            )

        return AdapterResult(
            status="success",
            price_raw=price_raw,
            price_parsed=price_parsed,
            availability=availability,
            title=title,
        )
