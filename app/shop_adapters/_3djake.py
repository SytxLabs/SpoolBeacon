"""
3DJake adapter — confirmed working 2026-06-29.
Plain SSR PHP page, no JS required for price.
"""
from app.routes.shop_rules import _extract, parse_price
from .base import BaseAdapter, AdapterResult


class ThreeDJakeAdapter(BaseAdapter):
    domains = ("3djake.de",)

    _PRICE_SEL = ".price"
    _PRICE_RE  = r"\d+[,\.]\d{2}"
    _TITLE_SEL = "h1"
    _AVAIL_SEL = "[class*='availab']"

    def extract(self, html: str, url: str) -> AdapterResult:
        title        = _extract(html, self._TITLE_SEL, None)
        price_raw    = _extract(html, self._PRICE_SEL, self._PRICE_RE)
        availability = _extract(html, self._AVAIL_SEL, None)

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="Price selector '.price' returned no match — layout may have changed",
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
