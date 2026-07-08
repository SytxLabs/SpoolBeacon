import functools
from pathlib import Path

import yaml
from quart import g

DEFAULT_LOCALE = "en"

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"


@functools.lru_cache(maxsize=None)
def _load(locale: str) -> dict:
    path = _TRANSLATIONS_DIR / f"{locale}.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _discover_locales() -> dict[str, str]:
    """Scan app/translations/*.yaml — each file's `language:` key is its display name."""
    locales = {}
    for path in sorted(_TRANSLATIONS_DIR.glob("*.yaml")):
        code = path.stem
        data = _load(code)
        locales[code] = data.get("language", code)
    return locales


SUPPORTED_LOCALES = _discover_locales()


def _lookup(data: dict, key: str):
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def get_locale() -> str:
    try:
        return g.locale
    except (RuntimeError, AttributeError):
        return DEFAULT_LOCALE


def t(key: str, **kwargs) -> str:
    """Translate `key` (dot-notation path into the yaml files) for the current request locale."""
    locale = get_locale()
    value = _lookup(_load(locale), key)
    if value is None and locale != DEFAULT_LOCALE:
        value = _lookup(_load(DEFAULT_LOCALE), key)
    if value is None:
        return key
    return value.format(**kwargs) if kwargs else value
