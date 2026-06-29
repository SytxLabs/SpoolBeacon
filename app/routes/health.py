from quart import Blueprint, jsonify
from sqlalchemy import text

from app.database import get_db

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
async def health():
    try:
        async with get_db() as session:
            await session.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "db": "ok"})
    except Exception as e:
        return jsonify({"status": "degraded", "db": str(e)}), 503
