# SpoolBeacon

Self-hosted filament inventory tracker for 3D printing. Tracks filament spools (weight, status, storage), purchase history, shop prices, and alerts you when a filament hits your target price.

## Features

- **Inventory** — filament products with manufacturer, material, color, diameter; spool tracking with remaining weight, fill %, storage location and status
- **Purchase history** — per-product purchase log with unit price, lot number, currency; spools auto-created from purchases
- **Shop price tracking** — shop links with manual prices and automated scraping via ShopRules or built-in adapters
- **Price snapshots** — history per shop link, with availability, auto-check via scheduler
- **Target price alerts** — alert when scraped price ≤ target (absolute or per-kg); Discord and SMTP notifications
- **Dashboard** — totals, low-stock list, by-material/color breakdown, active alerts, scheduler status
- **User management** — admin / member / viewer roles; admin-only settings and user management
- **Docker-ready** — single container, connects to external MariaDB

## Requirements

- Python 3.12+ (for local dev)
- MariaDB 10.6+ (external, not bundled)
- Playwright/Chromium (for JS-heavy shop pages; installed via `playwright install --with-deps chromium`)
- Docker + Docker Compose (for container deployment)

## Docker Setup

### 1. Create `.env` from example

```bash
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=<generate a long random string>
DB_HOST=your-mariadb-host
DB_PORT=3306
DB_USER=spoolbeacon
DB_PASSWORD=your-db-password
DB_NAME=spoolbeacon
DEBUG=false
QUART_AUTH_COOKIE_SECURE=true   # set true if serving over HTTPS
```

### 2. Create the MariaDB database

Connect to your MariaDB instance and run:

```sql
CREATE DATABASE spoolbeacon CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'spoolbeacon'@'%' IDENTIFIED BY 'your-db-password';
GRANT ALL PRIVILEGES ON spoolbeacon.* TO 'spoolbeacon'@'%';
FLUSH PRIVILEGES;
```

### 3. Start the container

```bash
docker compose up --build -d
```

The container exposes port `5000`. A `/health` endpoint is available for monitoring.

### 4. Run migrations (first run and after updates)

```bash
docker compose exec web python migration.py upgrade head
```

### 5. First admin setup

Open `http://your-host:5000/setup` — only available when no users exist. Creates the first admin account.

---

## Local Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env   # fill in DB credentials
python migration.py upgrade head
python main.py         # starts on http://localhost:5000
```

## Seed Data

For a quick demo with sample filaments, purchases, and shop links:

```bash
python seed.py           # idempotent — skips existing records
python seed.py --reset   # wipes all tables (except users/settings) and re-seeds
python seed_shops.py     # ShopRules only — safe for open-source use (no personal data)
```

---

## .env Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Flask/Quart session secret. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_HOST` | `localhost` | MariaDB host |
| `DB_PORT` | `3306` | MariaDB port |
| `DB_USER` | `spoolbeacon` | MariaDB user |
| `DB_PASSWORD` | *(required)* | MariaDB password |
| `DB_NAME` | `spoolbeacon` | MariaDB database name |
| `DEBUG` | `false` | Enable debug mode (do not use in production) |
| `QUART_AUTH_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS |

---

## Migrations

Migrations use Alembic. Always run after pulling updates:

```bash
python migration.py upgrade head   # apply all pending migrations
python migration.py current        # show current revision
python migration.py downgrade -1   # roll back one step
```

To create a migration after changing a model:

```bash
python migration.py revision -m "describe_change"
python migration.py upgrade head
```

Migration files are auto-named `YYYYMMDD_HHMM_<rev>_<slug>.py`.

---

## Settings Page (`/settings`)

Admin-only. Configure:

- **Scheduler** — enable/disable automatic price checks, set interval (minutes), minimum re-check interval per link
- **Price-check engine** — `playwright` (default, handles JS-rendered pages) or `httpx` (faster, no JS)
- **Playwright options** — headless mode, timeout, max concurrent checks
- **Discord notifications** — webhook URL, enable/disable
- **SMTP notifications** — host, port, credentials, TLS, from/to addresses

Test buttons for both Discord and SMTP are on the settings page.

---

## Scheduler

The scheduler runs a 1-minute heartbeat (APScheduler) and reads its configuration from the database (Settings page). It does **not** use `.env` — enable it from the UI.

- **Enabled** — controlled via Settings → Scheduler → Enabled toggle
- **Interval** — how often (in minutes) a full price-check run fires
- **Min re-check interval** — a link that was checked within this window is skipped

The scheduler status (next run, last run) is shown on the dashboard.

---

## Price Checks: ShopRules and Adapters

Two mechanisms fetch prices automatically:

### Built-in Adapters

Pre-built adapters with custom extraction logic for specific shops. Currently registered:

| Domain | Method |
|---|---|
| `3djake.de` | CSS selector (SSR PHP) |
| `prusa3d.com` | JSON-LD |
| `anycubic.com` | Shopify JSON-LD |
| `filamentworld.de` | WooCommerce |
| `eu.store.bambulab.com` | JSON-LD (cloudscraper) |
| `esun3dstore.com` | Shopify JSON-LD (cloudscraper) |

To add a new adapter: subclass `BaseAdapter` in `app/shop_adapters/`, implement `extract(html, url) -> AdapterResult`, and register in `app/shop_adapters/registry.py`. Set `fetch_engine = "cloudscraper"` on the adapter class if the shop uses Cloudflare.

### ShopRules (generic)

For any shop not covered by an adapter: define a ShopRule at `/shop-rules`. A rule has:
- **Domain** — matched against the shop link's URL hostname
- **Price selector** — CSS selector to find the price element
- **Price regex** — optional regex applied to the selected text
- **Availability selector + regex** — same pattern for stock status

The `parse_price()` function handles German (`1.299,00 €`) and English (`1,299.00`) formats automatically.

### Manual snapshots

Manual price entry is always available via the "Add Snapshot" button on each shop link — no rule or adapter required.

---

## Notifications

Configured in Settings. Triggered when a price snapshot hits a target price (absolute or per-kg).

**Discord** — sends a plain-text message to a Discord webhook URL.

**SMTP** — sends an email. Port 465 uses `SMTP_SSL`; other ports use STARTTLS when TLS is enabled. Leave user/password empty for unauthenticated relays.

Notifications are sent once per alert event (not on every check). Alerts auto-resolve when the price rises above the target again.

---

## Known Limitations

- **No spool delete** — spools can be set to 0 g (empty) or archived but not deleted from the UI (prevents accidental data loss; workaround: set status to `archiviert`)
- **Amazon / eBay not supported** — Amazon ASIN pages require an authenticated session or the Product Advertising API; eBay is blocked by Cloudflare. See `app/shop_adapters/registry.py` → `PLANNED` for details on evaluated shops
- **Some shops block automation** — Cloudflare WAF blocks httpx and Playwright for certain shops. `cloudscraper` bypasses basic JS challenges; heavy WAF requires a paid proxy or API
- **No printer / slicer integration** — no Klipper, OrcaSlicer, Bambu Studio, or print-job tracking. SpoolBeacon is inventory-only
- **No mobile app** — web-only, responsive layout

---

## Troubleshooting

**App fails to start — `Database not initialized`**
→ Check DB credentials in `.env`. Ensure MariaDB is reachable and the database exists.

**Migrations fail — `Table already exists`**
→ Database was created manually. Run `python migration.py stamp head` to mark the current state, then apply new migrations.

**Playwright price checks timeout**
→ Increase `playwright.timeout_ms` in Settings (default 30 000 ms). Some shops are slow or geo-blocked.

**`playwright install --with-deps chromium` fails in Docker**
→ The Dockerfile uses `python:3.12-slim` (Debian-based). `--with-deps` installs all Chromium system dependencies automatically. Rebuild the image after any requirements change.

**Price not extracted (`Price not found`)**
→ The shop's HTML structure changed. Test the ShopRule at `/shop-rules` → Test button, update the CSS selector / regex. For adapter-backed shops, check `app/shop_adapters/_<shop>.py`.

**Setup page unavailable after first user is created**
→ `/setup` is disabled once any user exists. Add users via `/users` (admin only).
