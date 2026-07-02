"""
Elegoo Shop adapter — Shopify store, confirmed working 2026-06-30.
Price from <meta property="og:price:amount"> (already decimal, USD).
Availability from JSON-LD schema.org/InStock|OutOfStock.
"""
import re
from selectolax.parser import HTMLParser
from .base import BaseAdapter, AdapterResult

_AVAIL_MAP = {
    "instock":    "In Stock",
    "outofstock": "Out of Stock",
    "preorder":   "Pre-order",
}
_AVAIL_RE = re.compile(r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"', re.IGNORECASE)


class ElegooAdapter(BaseAdapter):
    domains = ("elegoo.com",)

    def extract(self, html: str, url: str) -> AdapterResult:
        tree = HTMLParser(html)

        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node else None

        # Detect soft-404: Elegoo returns HTTP 200 with error-page content on invalid URLs
        if title and ("404" in title or "page not found" in title.lower()):
            return AdapterResult(
                status="error",
                error_message="Page returned a 404 error page — check the product URL",
                title=title,
            )

        price_node = tree.css_first('meta[property="og:price:amount"]')
        currency_node = tree.css_first('meta[property="og:price:currency"]')

        price_raw = price_node.attributes.get("content") if price_node else None
        currency = currency_node.attributes.get("content") if currency_node else None

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="og:price:amount meta tag not found — page structure may have changed",
                title=title,
            )

        try:
            price_parsed = round(float(price_raw.replace(',', '.')), 2)
        except (ValueError, TypeError) as e:
            return AdapterResult(
                status="error",
                price_raw=price_raw,
                error_message=f"Price parse failed: {price_raw!r} → {e}",
                title=title,
            )

        avail_raw = None
        m = _AVAIL_RE.search(html)
        if m:
            token = m.group(1).lower()
            avail_raw = _AVAIL_MAP.get(token, m.group(1))

        return AdapterResult(
            status="success",
            price_raw=price_raw,
            price_parsed=price_parsed,
            availability=avail_raw,
            title=title,
        )
