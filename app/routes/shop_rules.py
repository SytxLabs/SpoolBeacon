import re
from functools import wraps
from html import escape as html_escape
from urllib.parse import quote

import httpx
from quart import Blueprint, render_template, request, redirect, url_for, abort, flash, Response
from quart_auth import login_required, current_user
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.database import get_db
from app.models.shop_rule import ShopRule
from app.models.user import User, UserRole


def write_required(f):
    """Blocks viewer-role users from state-changing routes. Stack after @login_required."""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with get_db() as session:
            user = await session.get(User, int(current_user.auth_id))
        if not user or user.role == UserRole.viewer:
            abort(403)
        return await f(*args, **kwargs)
    return wrapper


_FETCH_TIMEOUT = 12.0
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


_CURRENCY_RE = re.compile(r'[€$£¥ \s]|CHF|EUR|USD|GBP|PLN|CZK', re.IGNORECASE)
# Strips JSON key prefixes: `"price":"`, `price: `, `"price": "`, etc.
_JSON_KEY_RE = re.compile(r'^"?[\w]+"?\s*:\s*"?', re.IGNORECASE)


def parse_price(raw: str) -> float:
    """
    Parse price strings in German or English format to float.
      "17,99 €"              → 17.99
      "€17.99"               → 17.99
      "1.299,00 €"           → 1299.00
      "1,299.00"             → 1299.00
      "32.990000"            → 32.99  (JSON-LD plain float)
      '"price":"32.990000"'  → 32.99  (JSON-LD key fragment from regex capture)
      '"price": "32.990000"' → 32.99
      'price:32.990000'      → 32.99
    Raises ValueError for unparseable input.
    """
    s = raw.strip().strip('"')
    # Strip JSON key prefix if present (only at start of string)
    s = _JSON_KEY_RE.sub("", s, count=1).strip().strip('"')
    s = _CURRENCY_RE.sub("", s).strip()

    dot_pos   = s.rfind(".")
    comma_pos = s.rfind(",")

    if comma_pos > dot_pos:
        # German: 1.299,00 — last separator is comma → decimal
        s = s.replace(".", "").replace(",", ".")
    elif dot_pos > comma_pos:
        # English or plain float: 1,299.00 or 32.990000 — last separator is dot
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")

    return round(float(s), 2)


def _extract(html: str, selector: str | None, pattern: str | None) -> str | None:
    """Apply CSS selector then optional regex to extracted text. Returns stripped text or None."""
    text = None
    if selector:
        tree = HTMLParser(html)
        node = tree.css_first(selector)
        if node:
            text = node.text(strip=True) or None
    if text and pattern:
        m = re.search(pattern, text, re.IGNORECASE)
        text = m.group(0).strip() if m else None
    elif not text and pattern:
        m = re.search(pattern, html, re.IGNORECASE)
        text = m.group(0).strip() if m else None
    return text


async def _run_test(rule: ShopRule, url: str) -> dict:
    """Fetch URL and apply rule selectors. Returns result dict."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_FETCH_TIMEOUT,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Timeout after {_FETCH_TIMEOUT:.0f}s."}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    price_raw = _extract(html, rule.price_selector, rule.price_regex)
    title_raw = _extract(html, rule.title_selector, None)
    avail_raw = _extract(html, rule.availability_selector, rule.availability_regex)

    price_parsed = None
    price_parse_error = None
    if price_raw:
        try:
            price_parsed = parse_price(price_raw)
        except (ValueError, AttributeError) as e:
            price_parse_error = str(e)

    return {
        "ok": True,
        "url": url,
        "status_code": resp.status_code,
        "price_raw": price_raw,
        "price_parsed": price_parsed,
        "price_parse_error": price_parse_error,
        "title_raw": title_raw,
        "availability_raw": avail_raw,
    }

shop_rules_bp = Blueprint("shop_rules", __name__, url_prefix="/shop-rules")

_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+$")


def _validate(form) -> str | None:
    domain = form.get("domain", "").strip().lower()
    if not domain:
        return "Domain is required."
    if not _DOMAIN_RE.match(domain):
        return "Invalid domain (example: shop.example.com)."
    return None


def _fields(form) -> dict:
    return {
        "domain": form.get("domain", "").strip().lower(),
        "price_selector": form.get("price_selector", "").strip() or None,
        "title_selector": form.get("title_selector", "").strip() or None,
        "availability_selector": form.get("availability_selector", "").strip() or None,
        "price_regex": form.get("price_regex", "").strip() or None,
        "availability_regex": form.get("availability_regex", "").strip() or None,
        "currency": form.get("currency", "EUR").strip() or "EUR",
        "test_url": form.get("test_url", "").strip() or None,
        "is_active": "1" in form.getlist("is_active"),
        "notes": form.get("notes", "").strip() or None,
    }


@shop_rules_bp.get("/")
@login_required
async def index():
    from app.shop_adapters.registry import registered_domains

    async with get_db() as session:
        rules = (await session.execute(
            select(ShopRule).order_by(ShopRule.domain)
        )).scalars().all()
    return await render_template(
        "shop_rules/index.html", rules=rules, adapter_domains=set(registered_domains())
    )


@shop_rules_bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
async def new():
    from app.shop_adapters.registry import registered_domains

    if request.method == "GET":
        return await render_template(
            "shop_rules/rule_form.html", rule=None, form_data=None,
            adapter_domains=registered_domains(),
        )

    async with get_db() as session:
        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=None, form_data=form,
                adapter_domains=registered_domains(),
            )

        domain = form.get("domain", "").strip().lower()
        dup = (await session.execute(
            select(ShopRule).where(ShopRule.domain == domain)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Rule for "{domain}" already exists.', "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=None, form_data=form,
                adapter_domains=registered_domains(),
            )

        session.add(ShopRule(**_fields(form)))

    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.route("/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
async def edit(rule_id: int):
    from app.shop_adapters.registry import registered_domains

    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)

        if request.method == "GET":
            return await render_template(
                "shop_rules/rule_form.html", rule=rule, form_data=None,
                adapter_domains=registered_domains(),
            )

        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=rule, form_data=form,
                adapter_domains=registered_domains(),
            )

        domain = form.get("domain", "").strip().lower()
        dup = (await session.execute(
            select(ShopRule).where(ShopRule.domain == domain, ShopRule.id != rule_id)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Rule for "{domain}" already exists.', "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=rule, form_data=form,
                adapter_domains=registered_domains(),
            )

        for k, v in _fields(form).items():
            setattr(rule, k, v)

    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.post("/<int:rule_id>/toggle")
@login_required
@write_required
async def toggle(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)
        rule.is_active = not rule.is_active
    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.post("/<int:rule_id>/delete")
@login_required
@write_required
async def delete(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)
        await session.delete(rule)
    return redirect(url_for("shop_rules.index"))


_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_HEAD_OPEN_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)


def _picker_response(body_html: str) -> Response:
    resp = Response(body_html, mimetype="text/html")
    resp.headers["Content-Security-Policy"] = "sandbox allow-same-origin"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


def _picker_message_page(url: str, message: str) -> Response:
    retry_href = f"/shop-rules/picker-frame?url={quote(url)}" if url else "#"
    return _picker_response(f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
             background:#141417;color:#a1a1aa;font-family:-apple-system,system-ui,sans-serif;">
  <div style="text-align:center;max-width:26rem;padding:1.5rem;">
    <p style="color:#f87171;font-size:.875rem;font-weight:600;margin-bottom:.5rem;">Could not load page</p>
    <p style="font-size:.8rem;word-break:break-word;">{html_escape(message)}</p>
    <p style="font-size:.75rem;margin-top:1rem;color:#52525b;">Check the Test URL, or the check engine in Settings.</p>
    {'<a href="' + html_escape(retry_href) + '" style="display:inline-block;margin-top:1rem;color:#818cf8;font-size:.8rem;">Retry</a>' if url else ''}
  </div>
</body></html>""")


@shop_rules_bp.get("/picker-frame")
@login_required
async def picker_frame():
    """Serve a sandboxed same-origin copy of a fetched page for the visual selector picker.
    CSP `sandbox` + X-Frame-Options ensure this stays script-inert even if loaded outside
    the intended sandboxed iframe (see plan: prevents third-party-script XSS on our origin).
    """
    from app.price_check_service import _fetch_httpx, _fetch_playwright, _fetch_cloudscraper
    from app.settings_service import get_all as get_settings

    url = request.args.get("url", "").strip()
    if not url or not _URL_SCHEME_RE.match(url):
        return _picker_message_page(url, "Enter a valid http(s) Test URL above first.")

    async with get_db() as session:
        settings = await get_settings(session)

    engine = settings.get("check.engine", "playwright")
    if engine == "cloudscraper":
        html, err = await _fetch_cloudscraper(url)
    elif engine == "playwright":
        html, err = await _fetch_playwright(url, settings)
    else:
        html, err = await _fetch_httpx(url)

    if err or not html:
        return _picker_message_page(url, err or "Empty response from server.")

    base_tag = f'<base href="{html_escape(url, quote=True)}">'
    if _HEAD_OPEN_RE.search(html):
        html = _HEAD_OPEN_RE.sub(lambda m: m.group(0) + base_tag, html, count=1)
    else:
        html = base_tag + html

    return _picker_response(html)


@shop_rules_bp.post("/<int:rule_id>/test")
@login_required
async def test_rule(rule_id: int):
    from app.shop_adapters.registry import registered_domains

    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)

        form = await request.form
        url = form.get("test_url", "").strip() or rule.test_url or ""
        if not url:
            await flash("No test URL provided.", "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=rule, form_data=None, test_result=None,
                adapter_domains=registered_domains(),
            )

        test_result = await _run_test(rule, url)
        test_result["tested_url"] = url

    return await render_template(
        "shop_rules/rule_form.html",
        rule=rule,
        form_data=None,
        test_result=test_result,
        adapter_domains=registered_domains(),
    )
