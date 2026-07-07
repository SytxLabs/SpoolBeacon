"""
Anycubic adapter — confirmed working 2026-06-30 (httpx + Playwright).
Shopify-based store (USD only). Plain HTTP works — SSR page ~2.9 MB.

Price selectors (Shopify theme):
  .price-item--sale.bold   → sale price when on sale (e.g. "$17.99")
  .price-item--regular.bold → regular price when not on sale
  Both return the effective current price in observed tests.

Note: anycubic.com is a USD-denominated store. Set ShopLink.currency = "USD".
"""
from app.routes.shop_rules import _extract, parse_price
from .base import BaseAdapter, AdapterResult

# Add $ to parsing — _CURRENCY_RE already strips it via the char class
_PRICE_RE = r"\d+\.\d{2}"


class AnycubicAdapter(BaseAdapter):
    domains = ("anycubic.com",)
    fetch_engine = "httpx"

    def extract(self, html: str, url: str) -> AdapterResult:
        title = _extract(html, "h1", None)

        # Sale price takes priority; fall back to regular price
        price_raw = _extract(html, ".price-item--sale.bold", _PRICE_RE)
        if not price_raw:
            price_raw = _extract(html, ".price-item--regular.bold", _PRICE_RE)

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="Shopify price selector returned no match — page layout may have changed",
                title=title,
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
            title=title,
        )
