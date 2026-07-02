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
from ._3djake import ThreeDJakeAdapter
from ._prusa import PrusaAdapter
from ._anycubic import AnycubicAdapter
from ._bambulab import BambuLabAdapter
from ._esun import ESunAdapter
from ._elegoo import ElegooAdapter

_REGISTRY: dict[str, BaseAdapter] = {}


def _reg(adapter: BaseAdapter) -> None:
    for domain in adapter.domains:
        _REGISTRY[domain] = adapter


# ── Confirmed working adapters ────────────────────────────────────────────────
_reg(ThreeDJakeAdapter())     # 3djake.de             — SSR PHP
_reg(PrusaAdapter())          # prusa3d.com            — JSON-LD
_reg(AnycubicAdapter())       # anycubic.com           — Shopify USD
_reg(BambuLabAdapter())       # eu.store.bambulab.com  — JSON-LD EUR, cloudscraper
_reg(ESunAdapter())           # esun3dstore.com, esun3dstoreeu.com — JSON-LD, cloudscraper
_reg(ElegooAdapter())         # elegoo.com             — Shopify og:price:amount USD, confirmed 2026-06-30


def get_adapter(domain: str) -> BaseAdapter | None:
    return _REGISTRY.get(domain)


def registered_domains() -> list[str]:
    return sorted(_REGISTRY)
