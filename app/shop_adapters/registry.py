"""
Adapter registry — maps domain → adapter instance.

To add a new adapter:
  1. Create app/shop_adapters/_yourshop.py with a BaseAdapter subclass.
     Set `domains = (...)` to a tuple of every confirmed domain/subdomain
     the same extraction logic works on (e.g. regional storefronts on one
     platform) — one adapter instance can serve multiple domains.
  2. Import and register it below.
"""
from .base import BaseAdapter
from ._amazon import AmazonAdapter
from ._3djake import ThreeDJakeAdapter
from ._prusa import PrusaAdapter
from ._anycubic import AnycubicAdapter
from ._bambulab import BambuLabAdapter
from ._esun import ESunAdapter
from ._elegoo import ElegooAdapter
from ._ebay import EbayAdapter

_REGISTRY: dict[str, BaseAdapter] = {}


def _reg(adapter: BaseAdapter) -> None:
    for domain in adapter.domains:
        _REGISTRY[domain] = adapter


# ── Confirmed working adapters ────────────────────────────────────────────────
_reg(AmazonAdapter())         # amazon.com/.de/.co.uk/.fr/.it/.es/.nl/.se/.pl/.co.jp/.ca/.com.au/.in
                              # — httpx (cloudscraper gets CAPTCHA'd), confirmed 2026-07-06
_reg(ThreeDJakeAdapter())     # 3djake.de             — SSR PHP
_reg(PrusaAdapter())          # prusa3d.com            — JSON-LD
_reg(AnycubicAdapter())       # anycubic.com           — Shopify USD
_reg(BambuLabAdapter())       # eu.store.bambulab.com  — JSON-LD EUR, cloudscraper
_reg(ESunAdapter())           # esun3dstore.com, esun3dstoreeu.com — JSON-LD, cloudscraper
_reg(ElegooAdapter())         # elegoo.com             — Shopify og:price:amount USD, confirmed 2026-06-30
_reg(EbayAdapter())           # ebay.com/.de/.co.uk/.fr/.it/.es/.at/.nl/.ie/.pl/.ch/.ca/.com.au
                              # /.com.hk/.com.sg/.com.my/.ph — httpx


def get_adapter(domain: str) -> BaseAdapter | None:
    if domain in _REGISTRY:
        return _REGISTRY[domain]
    # Fallback: match regional/store subdomains of a registered domain
    # (e.g. "store.anycubic.com" / "de.elegoo.com" -> "anycubic.com" / "elegoo.com").
    # Longest registered domain wins in case of overlapping suffixes.
    best_adapter, best_len = None, -1
    for reg_domain, adapter in _REGISTRY.items():
        if domain.endswith("." + reg_domain) and len(reg_domain) > best_len:
            best_adapter, best_len = adapter, len(reg_domain)
    return best_adapter


def registered_domains() -> list[str]:
    return sorted(_REGISTRY)
