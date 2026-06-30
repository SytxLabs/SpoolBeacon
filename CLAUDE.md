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

**Stack:** Quart 0.20 (async Flask), SQLAlchemy 2.x async, asyncmy (MariaDB), Alembic, Jinja2, Tailwind CSS (CDN), APScheduler, httpx, selectolax, cloudscraper.

**App factory:** `app/__init__.py` → `create_app()`. Registers blueprints, CSRF middleware, scheduler, and a context processor that injects `current_user`, `csrf_token`, `nav_alert_count`, `is_admin` into every template.

**DB session:** Always use `async with get_db() as session:`. The context manager auto-commits on clean exit, rolls back on exception.

**Blueprints / routes:**

| Blueprint | Prefix | File |
|---|---|---|
| `auth_bp` | `/login`, `/logout`, `/setup` | `routes/auth.py` |
| `dashboard_bp` | `/dashboard` | `routes/dashboard.py` |
| `inventory_bp` | `/inventory` | `routes/inventory.py` |
| `shop_rules_bp` | `/shop-rules` | `routes/shop_rules.py` |
| `settings_bp` | `/settings` | `routes/settings.py` |
| `health_bp` | `/health` | `routes/health.py` |

**Models and their relationships:**

```
Manufacturer → FilamentProduct ← PurchaseLine ← Purchase
FilamentProduct → Spool (via purchase_line_id)
FilamentProduct → ShopLink → PriceSnapshot
                  ShopLink → PriceAlertEvent
ShopRule  (domain-keyed scraping config, matched to ShopLink by URL domain)
AppSetting (key/value table for notification config)
```

**Price check flow:**
1. Scheduler (`app/scheduler.py`, APScheduler) or manual `/check` POST triggers `_check_one()`
2. `ShopRule` matched by `urlparse(link.url).hostname.removeprefix("www.")` → `rule.domain`
3. httpx fetches URL → selectolax CSS selector + optional regex extracts price
4. `PriceSnapshot` saved; `alert_service.maybe_create_alerts()` creates/auto-resolves `PriceAlertEvent`

**Price parsing:** `parse_price()` in `routes/shop_rules.py` handles both German (`1.299,00 €`) and English (`1,299.00`) formats. Import it from there when needed elsewhere.

**Notification settings:** Stored in `AppSetting` key/value rows (keys: `discord.enabled`, `discord.webhook_url`, `smtp.*`). Access via `app/settings_service.py`. Send via `app/notification_service.py`.

## Key conventions

**CSRF:** Every state-changing form needs `<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">`. The `before_request` hook validates it on POST/PUT/PATCH/DELETE.

**Admin guard:** `settings_bp` uses `@admin_required` decorator (defined in `routes/settings.py`). Stack it after `@login_required`.

**Form pattern:** Templates use a `fval(name, default)` Jinja macro to populate inputs from either form re-post data, existing model, or default — in that priority order. Follow this pattern for all forms.

**Secret fields (webhook URL, SMTP password):** Never display stored value in input. Submit empty = keep existing value. The POST handler in `routes/settings.py` shows the pattern.

**Migrations:** `alembic.ini` uses file template `YYYYMMDD_HHMM_<rev>_<slug>`. `migrations/env.py` imports `app.models` to populate `Base.metadata` for autogenerate. Always run `python migration.py upgrade head` after creating a migration file.

**Scheduler:** Disabled by default (`ENABLE_SCHEDULER=false`). Enable in `.env`. `PRICE_CHECK_MIN_INTERVAL_MINUTES` prevents re-checking a link that was checked recently.

**Spool codes:** Auto-generated as `SB-{product_id}-{line_id}-{timestamp}-{seq}` in `inventory.create_spools_from_line`.
