"""
eSUN store adapter — confirmed working 2026-06-30 with cloudscraper.
Domain: esun3dstore.com (Shopify — USD store).

Note: esun3d.com is the brand/marketing site — prices are JS-rendered there.
      esun3dstore.com is the actual shop and has Schema.org pricing in JSON-LD.

Price: Schema.org `"price" : "31.99"` — USD.
Availability: `"http://schema.org/InStock"` in JSON-LD.
Example: https://esun3dstore.com/products/pla-pro-2-rolls
"""
import re

from .base import BaseAdapter, AdapterResult
from app.routes.shop_rules import parse_price

_PRICE_RE = re.compile(r'"price"\s*:\s*"?([\d.]+)"?')
_AVAIL_RE = re.compile(r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"')
_AVAIL_MAP = {"InStock": "In Stock", "OutOfStock": "Out of Stock"}


class ESunAdapter(BaseAdapter):
    domain = "esun3dstore.com"
    fetch_engine = "cloudscraper"

    def extract(self, html: str, url: str) -> AdapterResult:
        m_price = _PRICE_RE.search(html)
        m_avail = _AVAIL_RE.search(html)

        avail_raw = None
        if m_avail:
            avail_raw = _AVAIL_MAP.get(m_avail.group(1), m_avail.group(1))

        if not m_price:
            return AdapterResult(
                status="error",
                error_message="No Schema.org price found — JSON-LD structure may have changed",
                availability=avail_raw,
            )

        try:
            price_parsed = parse_price(m_price.group(1))
        except (ValueError, AttributeError) as e:
            return AdapterResult(
                status="error",
                price_raw=m_price.group(1),
                error_message=f"parse_price failed: {m_price.group(1)!r} → {e}",
            )

        return AdapterResult(
            status="success",
            price_raw=m_price.group(1),
            price_parsed=price_parsed,
            availability=avail_raw,
        )
