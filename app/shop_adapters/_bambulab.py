"""
Bambu Lab EU store adapter — confirmed working 2026-06-30 with cloudscraper.
Domain: eu.store.bambulab.com (Shopify store — NOT bambulab.com which is blocked).

Price: Schema.org JSON-LD `"price": 22.99` — EUR.
Availability: `"availability": "https://schema.org/InStock"` or OutOfStock.

bambulab.com (main site) remains blocked by Cloudflare even with cloudscraper.
Use ShopLinks pointing to eu.store.bambulab.com product pages.
Example: https://eu.store.bambulab.com/en/products/pla-basic-filament
"""
import re

from .base import BaseAdapter, AdapterResult
from app.routes.shop_rules import parse_price

_PRICE_RE = re.compile(r'"price"\s*:\s*"?([\d.]+)"?')
_AVAIL_RE = re.compile(r'"availability"\s*:\s*"[^"]*/([A-Za-z]+)"')
_AVAIL_MAP = {"InStock": "In Stock", "OutOfStock": "Out of Stock", "PreOrder": "Pre-Order"}


class BambuLabAdapter(BaseAdapter):
    # bambulab.com itself is blocked by Cloudflare — only regional storefronts work.
    # Add more confirmed regional domains here (e.g. us.store.bambulab.com) once tested.
    domains = ("eu.store.bambulab.com",)
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
                error_message="No Schema.org price found in page — JSON-LD structure may have changed",
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
