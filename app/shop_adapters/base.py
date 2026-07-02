from dataclasses import dataclass


@dataclass
class AdapterResult:
    # "success" | "blocked" | "needs_js" | "unsupported" | "error"
    status: str
    price_raw: str | None = None
    price_parsed: float | None = None
    availability: str | None = None
    title: str | None = None
    error_message: str | None = None


class BaseAdapter:
    """One instance per shop, registered under one or more domains (e.g. regional
    storefronts on the same platform). Receives already-fetched HTML, returns AdapterResult."""
    domains: tuple[str, ...] = ()
    # Override to "cloudscraper" when the shop blocks httpx and Playwright.
    # price_check_service reads this before fetching and uses the right client.
    fetch_engine: str = ""

    def extract(self, html: str, url: str) -> AdapterResult:
        raise NotImplementedError
