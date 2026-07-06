"""
Amazon adapter — all major Amazon marketplaces, confirmed working via plain httpx.

cloudscraper's Chrome-impersonation TLS fingerprint gets flagged by Amazon
(CAPTCHA wall on every request). Plain httpx with a normal browser
User-Agent + Accept-Language sails through instead — do not switch this
adapter's fetch_engine to "cloudscraper" or "playwright" without
re-confirming manually first.
"""
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from app.routes.shop_rules import parse_price
from .base import BaseAdapter, AdapterResult

# Locale per marketplace — keeps scraped titles/availability in the language
# a visitor of that storefront would actually see (this repo is English-only,
# so amazon.com must not come back German just because the global httpx
# default Accept-Language is de-DE). Confirmed no CAPTCHA on any of these
# domains 2026-07-06; 404s during testing were just ASIN-not-in-marketplace,
# not a block.
_ACCEPT_LANGUAGE = {
    "amazon.com": "en-US,en;q=0.9",
    "amazon.de": "de-DE,de;q=0.9,en;q=0.8",
    "amazon.co.uk": "en-GB,en;q=0.9",
    "amazon.fr": "fr-FR,fr;q=0.9,en;q=0.8",
    "amazon.it": "it-IT,it;q=0.9,en;q=0.8",
    "amazon.es": "es-ES,es;q=0.9,en;q=0.8",
    "amazon.nl": "nl-NL,nl;q=0.9,en;q=0.8",
    "amazon.se": "sv-SE,sv;q=0.9,en;q=0.8",
    "amazon.pl": "pl-PL,pl;q=0.9,en;q=0.8",
    "amazon.co.jp": "ja-JP,ja;q=0.9,en;q=0.8",
    "amazon.ca": "en-CA,en;q=0.9",
    "amazon.com.au": "en-AU,en;q=0.9",
    "amazon.in": "en-IN,en;q=0.9",
}


class AmazonAdapter(BaseAdapter):
    domains = tuple(_ACCEPT_LANGUAGE)
    fetch_engine = "httpx"

    def fetch_headers(self, url: str) -> dict | None:
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        lang = _ACCEPT_LANGUAGE.get(domain)
        return {"Accept-Language": lang} if lang else None

    def extract(self, html: str, url: str) -> AdapterResult:
        tree = HTMLParser(html)

        if tree.css_first("form[action*='validateCaptcha']"):
            return AdapterResult(
                status="blocked",
                error_message="Amazon returned a CAPTCHA challenge page.",
            )

        title_node = tree.css_first("#productTitle")
        title = title_node.text(strip=True) if title_node else None

        price_node = (
                tree.css_first(".a-price .a-offscreen")
                or tree.css_first("#priceblock_ourprice")
                or tree.css_first("#priceblock_dealprice")
        )
        price_raw = price_node.text(strip=True) if price_node else None

        if not price_raw:
            return AdapterResult(
                status="error",
                error_message="Price element not found — page structure may have changed or product is unavailable.",
                title=title,
            )

        try:
            price_parsed = parse_price(price_raw)
        except ValueError as e:
            return AdapterResult(
                status="error",
                price_raw=price_raw,
                error_message=f"Price parse failed: {price_raw!r} → {e}",
                title=title,
            )

        avail_node = tree.css_first("#availability .primary-availability-message") \
                     or tree.css_first("#availability span")
        availability = avail_node.text(strip=True) if avail_node else None

        return AdapterResult(
            status="success",
            price_raw=price_raw,
            price_parsed=price_parsed,
            availability=availability,
            title=title,
        )
