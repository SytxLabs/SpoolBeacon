"""
eBay adapter — all major eBay marketplaces, plain httpx (mirrors the Amazon
adapter approach: a normal browser User-Agent + Accept-Language sails through
without triggering the interstitial/CAPTCHA that Playwright and cloudscraper's
TLS fingerprint tend to trip on eBay).
"""
import re
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from app.routes.shop_rules import parse_price
from .base import BaseAdapter, AdapterResult

_ACCEPT_LANGUAGE = {
    "ebay.com": "en-US,en;q=0.9",
    "ebay.de": "de-DE,de;q=0.9,en;q=0.8",
    "ebay.co.uk": "en-GB,en;q=0.9",
    "ebay.fr": "fr-FR,fr;q=0.9,en;q=0.8",
    "ebay.it": "it-IT,it;q=0.9,en;q=0.8",
    "ebay.es": "es-ES,es;q=0.9,en;q=0.8",
    "ebay.at": "de-AT,de;q=0.9,en;q=0.8",
    "ebay.nl": "nl-NL,nl;q=0.9,en;q=0.8",
    "ebay.ie": "en-IE,en;q=0.9",
    "ebay.pl": "pl-PL,pl;q=0.9,en;q=0.8",
    "ebay.ch": "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7",
    "ebay.ca": "en-CA,en;q=0.9",
    "ebay.com.au": "en-AU,en;q=0.9",
    "ebay.com.hk": "en-HK,en;q=0.9",
    "ebay.com.sg": "en-SG,en;q=0.9",
    "ebay.com.my": "en-MY,en;q=0.9",
    "ebay.ph": "en-PH,en;q=0.9",
}

_PRICE_RE = re.compile(r"[\d.,]+")


class EbayAdapter(BaseAdapter):
    domains = tuple(_ACCEPT_LANGUAGE)
    fetch_engine = "httpx"

    def fetch_headers(self, url: str) -> dict | None:
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        lang = _ACCEPT_LANGUAGE.get(domain)
        return {"Accept-Language": lang} if lang else None

    def warmup_url(self, url: str) -> str | None:
        # A cold request straight to an item page gets eBay's generic error
        # page — GET the homepage first (same client) to pick up session cookies.
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        return f"https://www.{domain}/" if domain in _ACCEPT_LANGUAGE else None

    def extract(self, html: str, url: str) -> AdapterResult:
        tree = HTMLParser(html)

        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node else None

        page_title_node = tree.css_first("title")
        page_title = page_title_node.text(strip=True) if page_title_node else ""
        if tree.css_first("#captcha") or "error page" in page_title.lower() or "sorry" in page_title.lower():
            return AdapterResult(
                status="blocked",
                error_message="eBay returned an error/CAPTCHA page.",
                title=title,
            )

        price_node = tree.css_first(".x-price-primary")
        if price_node:
            price_text = price_node.text(strip=True)
        else:
            itemprop = tree.css_first('[itemprop="price"]')
            if itemprop:
                price_text = itemprop.attributes.get("content") or itemprop.text(strip=True)
            else:
                meta = tree.css_first('meta[property="og:price:amount"]')
                price_text = meta.attributes.get("content") if meta else None

        if not price_text:
            return AdapterResult(
                status="error",
                error_message="Price element not found — page structure may have changed or item is unavailable.",
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
        except ValueError as e:
            return AdapterResult(
                status="error",
                price_raw=price_text,
                error_message=f"Price parse failed: {price_text!r} → {e}",
                title=title,
            )

        avail_node = (
                tree.css_first(".d-quantity__availability")
                or tree.css_first("[data-testid='ux-seller-section__item--seller-info']")
        )
        availability = avail_node.text(strip=True) if avail_node else None

        return AdapterResult(
            status="success",
            price_raw=price_text,
            price_parsed=price_parsed,
            availability=availability,
            title=title,
        )
