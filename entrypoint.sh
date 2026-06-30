#!/bin/sh
set -e

echo "[SpoolBeacon] Waiting for database..."
until python - <<'EOF'
import os, sys, asyncio, asyncmy

async def ping():
    try:
        conn = await asyncmy.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 3306)),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            db=os.environ["DB_NAME"],
        )
        await conn.ensure_closed()
    except Exception:
        sys.exit(1)

asyncio.run(ping())
EOF
do
    echo "[SpoolBeacon] Database not ready, retrying in 2s..."
    sleep 2
done

echo "[SpoolBeacon] Running migrations..."
python migration.py upgrade head

echo "[SpoolBeacon] Starting app..."
exec python main.py
