"""
eBay adapter — ebay.de item pages.
Price from .x-price-primary (main listing price text).
eBay may block httpx/cloudscraper — use Playwright engine.
If eBay returns an error page, status is set to 'blocked'.
"""
import re
from selectolax.parser import HTMLParser
from app.routes.shop_rules import parse_price
from .base import BaseAdapter, AdapterResult

_PRICE_RE = re.compile(r'[\d.,]+')


class EbayAdapter(BaseAdapter):
    domains = ("ebay.de",)

    def extract(self, html: str, url: str) -> AdapterResult:
        tree = HTMLParser(html)

        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node else None

        # Detect eBay error/blocked page
        page_title_node = tree.css_first("title")
        page_title = page_title_node.text(strip=True) if page_title_node else ""
        if "error page" in page_title.lower() or "sorry" in page_title.lower():
            return AdapterResult(
                status="blocked",
                error_message="eBay returned an error page — request was blocked or item unavailable",
                title=title,
            )

        # Primary price: .x-price-primary contains the BIN / current bid price
        price_node = tree.css_first(".x-price-primary")
        if price_node:
            # Strip currency symbol and whitespace, keep only digits/separators
            price_text = price_node.text(strip=True)
        else:
            # Fallback: [itemprop="price"] or og:price
            itemprop = tree.css_first('[itemprop="price"]')
            if itemprop:
                price_text = itemprop.attributes.get("content") or itemprop.text(strip=True)
            else:
                meta = tree.css_first('meta[property="og:price:amount"]')
                price_text = meta.attributes.get("content") if meta else None

        if not price_text:
            return AdapterResult(
                status="error",
                error_message=".x-price-primary not found — eBay DOM may have changed or item is unavailable",
                title=title,
            )

        m = _PRICE_RE.search(price_text.replace("\xa0", ""))
        if not m:
            return AdapterResult(
                status="error",
                price_raw=price_text,
                error_message=f"No numeric price found in: {price_text!r}",
                title=title,
            )

        try:
            price_parsed = parse_price(m.group(0))
        except (ValueError, AttributeError) as e:
            return AdapterResult(
                status="error",
                price_raw=price_text,
                error_message=f"parse_price failed: {price_text!r} → {e}",
                title=title,
            )

        # Availability
        avail_node = tree.css_first(".d-quantity__availability") or tree.css_first("[data-testid='ux-seller-section__item--seller-info']")
        availability = avail_node.text(strip=True) if avail_node else None

        return AdapterResult(
            status="success",
            price_raw=price_text,
            price_parsed=price_parsed,
            availability=availability,
            title=title,
        )
