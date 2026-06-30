# SpoolBeacon

Self-hosted filament inventory for 3D printing. Track spools, purchases and shop prices — get notified when a filament hits your target price.

---

## Features

- **Inventory** — filaments by manufacturer, material, color and diameter; spools with remaining weight, fill %, storage location and status
- **Purchase history** — price per spool, lot number, currency; spools auto-created on purchase
- **Price monitoring** — shop links with manual prices and automated scraping via ShopRules or built-in adapters
- **Price alerts** — alert when price ≤ target (absolute or per kg); Discord and SMTP notifications
- **Dashboard** — overview, low-stock list, material/color breakdown, active alerts, scheduler status
- **User management** — Admin / Member / Viewer roles

---

## Requirements

- Docker + Docker Compose

For local development: Python 3.12+

---

## Quick Start (Docker)

```bash
cp .env.example .env
```

Edit `.env` — choose strong passwords:

```env
SECRET_KEY=       # generate: python -c "import secrets; print(secrets.token_hex(32))"
DB_PASSWORD=changeme
QUART_AUTH_COOKIE_SECURE=true
```

Start everything:

```bash
docker compose up --build -d
```

MariaDB starts automatically, migrations run on container start. No port is exposed to the host.

### First admin account

Open `http://your-host:5000/setup` — only available when no users exist yet.

---

## Updating

```bash
docker compose pull
docker compose up --build -d
```

Migrations run automatically on restart.

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env   # fill in DB credentials
python migration.py upgrade head
python main.py         # runs on http://localhost:5000
```

Seed sample data:

```bash
python seed.py           # manufacturers, products, purchases, links, rules (idempotent)
python seed.py --reset   # wipe all tables (except users/settings) and re-seed
python seed_shops.py     # ShopRules only, no inventory data
```

---

## Configuration

All runtime settings are managed on the Settings page (`/settings`, admin only) — not via `.env`.

| Section | What you can configure |
|---|---|
| Scheduler | Enable automatic price checks, set interval in minutes |
| Fetch engine | `playwright` (default, JS-capable) or `httpx` (faster, no JS) |
| Discord | Webhook URL, enable/disable, test message |
| Email (SMTP) | Host, port, credentials, TLS, from/to address, test email |
| Spool code template | Pattern for auto-generated spool codes |

---

## Price Monitoring

### Built-in Adapters

These shops work out of the box without any manual configuration:

| Shop | Method |
|---|---|
| `3djake.de` | CSS selector |
| `prusa3d.com` | JSON-LD |
| `anycubic.com` | Shopify JSON-LD |
| `eu.store.bambulab.com` | JSON-LD (cloudscraper) |
| `esun3dstore.com` | Shopify JSON-LD (cloudscraper) |
| `esun3dstoreeu.com` | Shopify JSON-LD (cloudscraper) |
| `elegoo.com` | Shopify og:price:amount |

To add a new adapter: subclass `BaseAdapter` in `app/shop_adapters/`, implement `extract(html, url) -> AdapterResult`, register in `registry.py` via `_reg(YourAdapter())`.

### ShopRules (generic)

For any other shop: create a rule at `/shop-rules` with domain, CSS price selector, and optional regex. German (`1.299,00 €`) and English (`1,299.00`) price formats are detected automatically.

---

## Known Limitations

- **Amazon / eBay** not supported — Amazon requires an authenticated session or the Product Advertising API; eBay blocks scraping via Cloudflare
- **Heavy WAFs** (Cloudflare Enterprise) block even cloudscraper — a proxy or official API is needed
- **No printer / slicer integration** — no Klipper, OrcaSlicer or print job tracking; inventory only
- **No mobile app** — web only, but responsive layout

---

## Troubleshooting

**App won't start — `Database not initialized`**
→ Check DB credentials in `.env`. MariaDB must be reachable and the database must exist.

**Migrations fail — `Table already exists`**
→ Run `python migration.py stamp head` to mark the current state, then migrate.

**Playwright checks time out**
→ Increase `playwright.timeout_ms` in Settings (default: 30 000 ms).

**Price not found**
→ The shop's HTML structure changed. Use the Test button on `/shop-rules` and update the selector/regex.

**Setup page unavailable**
→ `/setup` is locked once a user exists. Add users via `/users` (admin only).
