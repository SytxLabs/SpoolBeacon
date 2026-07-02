from quart import Blueprint, render_template
from quart_auth import login_required
from sqlalchemy import select, func

from app.database import get_db
from app.models.filament import FilamentProduct
from app.models.price_alert_event import PriceAlertEvent
from app.models.price_snapshot import PriceSnapshot
from app.models.purchase import Purchase, PurchaseLine
from app.models.shoplink import ShopLink
from app.models.spool import Spool, SpoolStatus
from app.routes.inventory import load_target_hits

dashboard_bp = Blueprint("dashboard", __name__)

_ACTIVE = Spool.status != SpoolStatus.archived


@dashboard_bp.get("/dashboard")
@login_required
async def index():
    async with get_db() as session:
        total_products = await session.scalar(select(func.count(FilamentProduct.id))) or 0

        total_spools = await session.scalar(
            select(func.count(Spool.id)).where(_ACTIVE)
        ) or 0

        total_remaining = await session.scalar(
            select(func.sum(Spool.remaining_weight_g)).where(_ACTIVE)
        ) or 0

        by_material = (await session.execute(
            select(
                FilamentProduct.material,
                func.count(Spool.id).label("spool_count"),
                func.sum(Spool.remaining_weight_g).label("total_g"),
            )
            .join(Spool, Spool.filament_product_id == FilamentProduct.id)
            .where(_ACTIVE)
            .group_by(FilamentProduct.material)
            .order_by(func.sum(Spool.remaining_weight_g).desc())
        )).all()

        by_color = (await session.execute(
            select(
                FilamentProduct.color_name,
                FilamentProduct.color_hex,
                func.count(Spool.id).label("spool_count"),
                func.sum(Spool.remaining_weight_g).label("total_g"),
            )
            .join(Spool, Spool.filament_product_id == FilamentProduct.id)
            .where(_ACTIVE)
            .group_by(FilamentProduct.color_name, FilamentProduct.color_hex)
            .order_by(func.sum(Spool.remaining_weight_g).desc())
        )).all()

        inventory_value = await session.scalar(
            select(
                func.sum(PurchaseLine.unit_price * Spool.remaining_weight_g / PurchaseLine.spool_weight_g)
            )
            .join(Spool, Spool.purchase_line_id == PurchaseLine.id)
            .where(_ACTIVE)
        ) or 0.0

        avg_price_per_kg = await session.scalar(
            select(func.avg(PurchaseLine.unit_price / PurchaseLine.spool_weight_g * 1000))
        ) or 0.0

        cheapest_row = (await session.execute(
            select(PurchaseLine.unit_price, FilamentProduct.name, FilamentProduct.material, PurchaseLine.currency)
            .join(FilamentProduct, PurchaseLine.filament_product_id == FilamentProduct.id)
            .order_by(PurchaseLine.unit_price)
            .limit(1)
        )).one_or_none()

        last_purchase = (await session.execute(
            select(Purchase).order_by(Purchase.purchase_date.desc()).limit(1)
        )).scalar_one_or_none()

        low_stock_rows = (await session.execute(
            select(Spool, FilamentProduct)
            .join(FilamentProduct, Spool.filament_product_id == FilamentProduct.id)
            .where(
                _ACTIVE,
                Spool.remaining_weight_g < Spool.initial_weight_g * 0.2,
            )
            .order_by(Spool.remaining_weight_g)
            .limit(10)
        )).all()

        total_purchases = await session.scalar(select(func.count(Purchase.id))) or 0
        target_hits = await load_target_hits(session)

        active_alert_count = await session.scalar(
            select(func.count(PriceAlertEvent.id))
            .where(PriceAlertEvent.resolved_at.is_(None))
        ) or 0

        recent_alerts = (await session.execute(
            select(PriceAlertEvent, ShopLink, FilamentProduct)
            .join(ShopLink, PriceAlertEvent.shop_link_id == ShopLink.id)
            .join(FilamentProduct, ShopLink.filament_product_id == FilamentProduct.id)
            .where(PriceAlertEvent.resolved_at.is_(None))
            .order_by(PriceAlertEvent.created_at.desc())
            .limit(10)
        )).all()

    stats = {
        "total_products": total_products,
        "total_spools": total_spools,
        "total_remaining_kg": round(total_remaining / 1000, 2),
        "inventory_value": round(inventory_value, 2),
        "avg_price_per_kg": round(avg_price_per_kg, 2),
        "cheapest": cheapest_row,
        "last_purchase": last_purchase,
        "total_purchases": total_purchases,
    }

    from app.scheduler import get_status as scheduler_status
    return await render_template(
        "dashboard/index.html",
        stats=stats,
        by_material=by_material,
        by_color=by_color,
        low_stock=low_stock_rows,
        target_hits=target_hits,
        active_alert_count=active_alert_count,
        recent_alerts=recent_alerts,
        scheduler=scheduler_status(),
    )
