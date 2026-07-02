# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run dev server
python main.py

# Migrations — always run after creating a migration file
python migration.py upgrade head
python migration.py revision -m "describe_change"   # autogenerates from models
python migration.py downgrade -1
python migration.py current

# Seed sample data (idempotent)
python seed.py           # full demo data: manufacturers, products, purchases, links, rules
python seed.py --reset   # clears all tables except users/app_settings first
python seed_shops.py     # ShopRules only, no inventory data (open-source safe)

# Docker
docker compose up --build
```

No test suite, no linter config exists.

## Architecture

**Stack:** Quart 0.20 (async Flask), SQLAlchemy 2.x async, asyncmy (MariaDB), Alembic, Jinja2, Tailwind CSS (CDN), APScheduler, httpx, selectolax, cloudscraper, Playwright.

**App factory:** `app/__init__.py` → `create_app()`. Registers blueprints, CSRF middleware, scheduler, error handlers (403/404/500), and a context processor that injects `current_user`, `csrf_token`, `nav_alert_count`, `is_admin` into every template.

**DB session:** Always use `async with get_db() as session:`. The context manager auto-commits on clean exit, rolls back on exception.

**Static files:** `app/static/` — served at `/static/`. Logo at `app/static/img/logo.png`, referenced via `url_for('static', filename='img/logo.png')`.

**Blueprints / routes:**

| Blueprint | Prefix | File |
|---|---|---|
| `auth_bp` | `/login`, `/logout`, `/setup` | `routes/auth.py` |
| `dashboard_bp` | `/dashboard` | `routes/dashboard.py` |
| `inventory_bp` | `/inventory` | `routes/inventory.py` |
| `shop_rules_bp` | `/shop-rules` | `routes/shop_rules.py` |
| `settings_bp` | `/settings` | `routes/settings.py` |
| `users_bp` | `/users` | `routes/users.py` |
| `health_bp` | `/health` | `routes/health.py` |

**Error templates:** `app/templates/errors/{403,404,500}.html` — extend `base.html`.

**Models and their relationships:**

```
Manufacturer → FilamentProduct ← PurchaseLine ← Purchase
FilamentProduct → Spool (via purchase_line_id)
FilamentProduct → ShopLink → PriceSnapshot
                  ShopLink → PriceAlertEvent
ShopRule  (domain-keyed scraping config, matched to ShopLink by URL domain)
AppSetting (key/value table for all runtime config: scheduler, notifications, engine)
```

**Price check flow:**
1. Scheduler (`app/scheduler.py`, APScheduler 1-min heartbeat) or manual `/check` POST triggers `check_price()` in `app/price_check_service.py`
2. Domain resolved from `urlparse(link.url).hostname.removeprefix("www.")`
3. Fetch engine chosen: adapter's `fetch_engine` overrides `check.engine` AppSetting (`playwright` default / `httpx` / `cloudscraper`)
4. If registered adapter exists for domain → `adapter.extract(html, url)` → `AdapterResult`; else fall back to `ShopRule` CSS selector + regex
5. `PriceSnapshot` saved; `alert_service.maybe_create_alerts()` creates/auto-resolves `PriceAlertEvent`

**Shop adapters** (`app/shop_adapters/`): Subclass `BaseAdapter`, set `domain` and optionally `fetch_engine = "cloudscraper"`, implement `extract(html, url) → AdapterResult`. Register in `registry.py` via `_reg(YourAdapter())`. Currently registered: `3djake.de`, `prusa3d.com`, `anycubic.com`, `eu.store.bambulab.com`, `esun3dstore.com`, `esun3dstoreeu.com`, `elegoo.com`.

**Price parsing:** `parse_price()` in `routes/shop_rules.py` handles German (`1.299,00 €`) and English (`1,299.00`) formats, plus JSON-LD key fragments. Import from there when needed elsewhere.

**Notification settings:** Stored in `AppSetting` key/value rows. Access via `app/settings_service.py` (`get_all` / `set_many`). Send via `app/notification_service.py`. Keys: `discord.enabled`, `discord.webhook_url`, `smtp.*`.

## Key conventions

**Language:** All code and user-facing text must be English — no Other Language words anywhere in this repo (open-source on GitHub). This includes flash messages, validation errors, log messages, notification/email/Discord copy, comments, and seed data. Conversation with the user may stay in German per their global preference, but nothing written to a file in this repo may contain German. Double-check every new or edited string before finishing a task — this has been missed before (e.g. `app/notification_service.py`, `app/alert_service.py` initially shipped with German copy and had to be fixed after the fact).

**CSRF:** Every state-changing form needs `<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">`. The `before_request` hook validates it on POST/PUT/PATCH/DELETE. JSON requests (content-type contains `json`) are exempt.

**Admin guard:** `@admin_required` is defined in both `routes/settings.py` and `routes/users.py`. Stack after `@login_required`. Use the one from the same file to avoid cross-import.

**Form pattern:** Templates use a `fval(name, default)` Jinja macro to populate inputs from either form re-post data, existing model, or default — in that priority order. Follow this pattern for all forms.

**Card headers:** Inside `.card` elements, use `<div class="pb-3 border-b border-gray-800"><p class="text-sm font-medium text-gray-200">Title</p></div>` as the section header — not `<h2 class="section-label">`. Labels inside cards use `text-xs text-gray-400`.

**Nested forms:** Never nest `<form>` inside another `<form>` (invalid HTML). For action buttons that need separate POST targets (test/delete), place those forms outside the main form and reference them via the HTML5 `form="form-id"` attribute on the button.

**Secret fields (webhook URL, SMTP password):** Never display stored value in input. Submit empty = keep existing value. The POST handler in `routes/settings.py` shows the pattern.

**Migrations:** `alembic.ini` uses file template `YYYYMMDD_HHMM_<rev>_<slug>`. `migrations/env.py` imports `app.models` to populate `Base.metadata` for autogenerate. Always run `python migration.py upgrade head` after creating a migration file.

**Scheduler:** APScheduler always starts (1-min heartbeat). Enabled/interval are controlled via `AppSetting` (`scheduler.enabled`, `scheduler.interval_minutes`, `scheduler.min_interval_minutes`) — set via the settings page, not ENV. `apply_settings()` in `app/scheduler.py` updates in-memory state immediately after save.

**Spool codes:** Auto-generated using the template in `AppSetting` key `spool.code_template` (default: `SB-{product_id}-{line_id}-{timestamp}-{seq:02d}`). The `{seq}` counter is product-scoped (counts all existing spools for the product, not just the current line). `remaining_weight_g` allows 0 (empty spool); validation rejects negative values only.
