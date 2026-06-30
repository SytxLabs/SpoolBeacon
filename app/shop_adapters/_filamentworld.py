"""
Filamentworld adapter — confirmed working 2026-06-30 (httpx + Playwright).
WooCommerce / German 3D-printing shop (EUR). Plain HTTP works — SSR page.

Price: `.price` selector → "24,90 €" — first non-zero match on product pages.
  Note: `.woocommerce-Price-amount` returns 0,00 € first (cart/hidden element);
  `.price` correctly returns the product price as first result.
Availability: `.stock` → "Vorrätig" / "Nicht vorrätig"

Use on direct product pages (not category listings) for reliable pricing.
"""
from app.routes.shop_rules import _extract, parse_price
from .base import BaseAdapter, AdapterResult
from selectolax.parser import HTMLParser
import re

_PRICE_RE = r"\d+[,\.]\d{2}"
_AVAIL_MAP = {
    "vorrätig":         "In Stock",
    "vorraetig":        "In Stock",
    "nicht vorrätig":   "Out of Stock",
    "nicht vorraetig":  "Out of Stock",
    "in stock":         "In Stock",
    "out of stock":     "Out of Stock",
}


class FilamentworldAdapter(BaseAdapter):
    domain = "filamentworld.de"

    def extract(self, html: str, url: str) -> AdapterResult:
        tree = HTMLParser(html)
        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node else None

        # Find first .price node with a non-zero price
        price_raw = None
        for node in tree.css(".price"):
            text = node.text(strip=True)
            m = re.search(_PRICE_RE, text)
            if m:
                try:
                    val = parse_price(m.group(0))
                    if val > 0:
                        price_raw = m.group(0)
                        break
                except (ValueError, AttributeError):
                    continue

        # Availability
        avail_node = tree.css_first(".stock")
        availability = None
        if avail_node:
            raw = avail_node.text(strip=True).lower().strip()
            availability = _AVAIL_MAP.get(raw, avail_node.text(strip=True))

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="No non-zero .price element found — use a direct product URL, not a category page",
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
