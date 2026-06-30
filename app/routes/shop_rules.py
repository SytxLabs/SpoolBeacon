import re

import httpx
from quart import Blueprint, render_template, request, redirect, url_for, abort, flash
from quart_auth import login_required
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.database import get_db
from app.models.shop_rule import ShopRule

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
        return {"ok": False, "error": f"Timeout nach {_FETCH_TIMEOUT:.0f}s."}
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
        return "Domain ist Pflichtfeld."
    if not _DOMAIN_RE.match(domain):
        return "Ungueltige Domain (Beispiel: shop.example.com)."
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
        "is_active": form.get("is_active") == "1",
        "notes": form.get("notes", "").strip() or None,
    }


@shop_rules_bp.get("/")
@login_required
async def index():
    async with get_db() as session:
        rules = (await session.execute(
            select(ShopRule).order_by(ShopRule.domain)
        )).scalars().all()
    return await render_template("shop_rules/index.html", rules=rules)


@shop_rules_bp.route("/new", methods=["GET", "POST"])
@login_required
async def new():
    if request.method == "GET":
        return await render_template("shop_rules/rule_form.html", rule=None, form_data=None)

    async with get_db() as session:
        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template("shop_rules/rule_form.html", rule=None, form_data=form)

        domain = form.get("domain", "").strip().lower()
        dup = (await session.execute(
            select(ShopRule).where(ShopRule.domain == domain)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Regel fuer "{domain}" existiert bereits.', "error")
            return await render_template("shop_rules/rule_form.html", rule=None, form_data=form)

        session.add(ShopRule(**_fields(form)))

    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.route("/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
async def edit(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)

        if request.method == "GET":
            return await render_template("shop_rules/rule_form.html", rule=rule, form_data=None)

        form = await request.form
        error = _validate(form)
        if error:
            await flash(error, "error")
            return await render_template("shop_rules/rule_form.html", rule=rule, form_data=form)

        domain = form.get("domain", "").strip().lower()
        dup = (await session.execute(
            select(ShopRule).where(ShopRule.domain == domain, ShopRule.id != rule_id)
        )).scalar_one_or_none()
        if dup:
            await flash(f'Regel fuer "{domain}" existiert bereits.', "error")
            return await render_template("shop_rules/rule_form.html", rule=rule, form_data=form)

        for k, v in _fields(form).items():
            setattr(rule, k, v)

    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.post("/<int:rule_id>/toggle")
@login_required
async def toggle(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)
        rule.is_active = not rule.is_active
    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.post("/<int:rule_id>/delete")
@login_required
async def delete(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)
        await session.delete(rule)
    return redirect(url_for("shop_rules.index"))


@shop_rules_bp.post("/<int:rule_id>/test")
@login_required
async def test_rule(rule_id: int):
    async with get_db() as session:
        rule = await session.get(ShopRule, rule_id)
        if not rule:
            abort(404)

        form = await request.form
        url = form.get("test_url", "").strip() or rule.test_url or ""
        if not url:
            await flash("Keine Test-URL angegeben.", "error")
            return await render_template(
                "shop_rules/rule_form.html", rule=rule, form_data=None, test_result=None
            )

        test_result = await _run_test(rule, url)
        test_result["tested_url"] = url

    return await render_template(
        "shop_rules/rule_form.html",
        rule=rule,
        form_data=None,
        test_result=test_result,
    )
