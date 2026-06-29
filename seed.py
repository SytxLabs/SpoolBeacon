"""Seed sample data: manufacturers, products, shop links, price snapshots."""
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from app.config import _build_database_url
from app.models.filament import Manufacturer, FilamentProduct
from app.models.shoplink import ShopLink
from app.models.price_snapshot import PriceSnapshot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


async def get_or_create_manufacturer(session, name: str, website: str) -> Manufacturer:
    existing = (await session.execute(
        select(Manufacturer).where(Manufacturer.name == name)
    )).scalar_one_or_none()
    if existing:
        return existing
    m = Manufacturer(name=name, website=website)
    session.add(m)
    await session.flush()
    return m


async def get_or_create_product(session, mfr_id: int, rp: dict) -> tuple[FilamentProduct, bool]:
    existing = (await session.execute(
        select(FilamentProduct).where(
            FilamentProduct.manufacturer_id == mfr_id,
            FilamentProduct.name == rp["name"],
            FilamentProduct.material == rp["material"],
            FilamentProduct.color_name == rp["color_name"],
        )
    )).scalar_one_or_none()
    if existing:
        return existing, False
    p = FilamentProduct(
        manufacturer_id=mfr_id, name=rp["name"],
        material=rp["material"], color_name=rp["color_name"],
        color_hex=rp["color_hex"], diameter_mm=1.75, nominal_weight_g=1000,
    )
    session.add(p)
    await session.flush()
    return p, True


async def get_or_create_shoplink(session, pid: int, rl: dict) -> tuple[ShopLink, bool]:
    existing = (await session.execute(
        select(ShopLink).where(ShopLink.filament_product_id == pid, ShopLink.url == rl["url"])
    )).scalar_one_or_none()
    if existing:
        return existing, False
    sl = ShopLink(
        filament_product_id=pid,
        shop_name=rl["shop_name"], url=rl["url"],
        currency="EUR", package_weight_g=1000,
        manual_price=rl["manual_price"],
        shipping_price=rl.get("shipping_price"),
        is_active=True,
    )
    session.add(sl)
    await session.flush()
    return sl, True


async def seed():
    engine = create_async_engine(_build_database_url(), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        # ── Manufacturers ───────────────────────────────────────────────────
        mfrs = {
            "Bambu Lab":  await get_or_create_manufacturer(session, "Bambu Lab",  "https://bambulab.com"),
            "Polymaker":  await get_or_create_manufacturer(session, "Polymaker",  "https://polymaker.com"),
            "eSUN":       await get_or_create_manufacturer(session, "eSUN",       "https://esun3d.com"),
            "Prusament":  await get_or_create_manufacturer(session, "Prusament",  "https://prusament.com"),
        }

        # ── FilamentProducts ────────────────────────────────────────────────
        raw_products = [
            # idx 0
            dict(m="Bambu Lab",  name="PLA Basic White",          material="PLA",  color_name="White",        color_hex="#F5F5F0"),
            # idx 1
            dict(m="Bambu Lab",  name="PLA Basic Black",          material="PLA",  color_name="Black",        color_hex="#1A1A1A"),
            # idx 2
            dict(m="Bambu Lab",  name="PETG Basic Grey",          material="PETG", color_name="Grey",         color_hex="#9E9E9E"),
            # idx 3
            dict(m="Bambu Lab",  name="ABS Grey",                 material="ABS",  color_name="Grey",         color_hex="#808080"),
            # idx 4
            dict(m="Polymaker",  name="PolyTerra PLA Army Green", material="PLA",  color_name="Army Green",   color_hex="#4A5240"),
            # idx 5
            dict(m="Polymaker",  name="PolyTerra PLA Matte Red",  material="PLA",  color_name="Matte Red",    color_hex="#C0392B"),
            # idx 6
            dict(m="Polymaker",  name="PolyLite PETG Blue",       material="PETG", color_name="Blue",         color_hex="#2980B9"),
            # idx 7
            dict(m="eSUN",       name="ePLA+ Orange",             material="PLA+", color_name="Orange",       color_hex="#E67E22"),
            # idx 8
            dict(m="eSUN",       name="eTPU-95A Black",           material="TPU",  color_name="Black",        color_hex="#1A1A1A"),
            # idx 9
            dict(m="Prusament",  name="PLA Galaxy Black",         material="PLA",  color_name="Galaxy Black", color_hex="#1C1C2E"),
            # idx 10
            dict(m="Prusament",  name="PETG Jet Black",           material="PETG", color_name="Jet Black",    color_hex="#111111"),
            # idx 11
            dict(m="Prusament",  name="PLA Prusa Orange",         material="PLA",  color_name="Prusa Orange", color_hex="#FA6831"),
        ]
        products = []
        new_products = 0
        for rp in raw_products:
            p, created = await get_or_create_product(session, mfrs[rp["m"]].id, rp)
            products.append(p)
            if created:
                new_products += 1

        # ── ShopLinks ───────────────────────────────────────────────────────
        # pi = products index, history = list of (days_ago, price, shipping, availability)
        raw_links = [
            dict(pi=0,  shop_name="Bambu Lab Store", url="https://bambulab.com/de-de/filament/pla-basic",
                 manual_price=17.99,
                 history=[
                     (60, 19.99, None, "Auf Lager"),
                     (30, 18.99, None, "Auf Lager"),
                     (14, 17.99, None, "Auf Lager"),
                     (3,  16.99, None, "Auf Lager"),
                 ]),
            dict(pi=0,  shop_name="3DJake", url="https://www.3djake.de/bambu-lab/pla-basic-white",
                 manual_price=19.49, shipping_price=4.90,
                 history=[
                     (45, 21.99, 4.90, "Auf Lager"),
                     (20, 20.49, 4.90, "Nur noch 3 Stk."),
                     (5,  19.49, 4.90, "Auf Lager"),
                 ]),
            dict(pi=1,  shop_name="Bambu Lab Store", url="https://bambulab.com/de-de/filament/pla-basic-black",
                 manual_price=17.99,
                 history=[
                     (50, 17.99, None, "Auf Lager"),
                     (10, 17.99, None, "Auf Lager"),
                     (1,  17.99, None, "Auf Lager"),
                 ]),
            dict(pi=4,  shop_name="Polymaker Shop", url="https://polymaker.com/polyterra-pla",
                 manual_price=22.99,
                 history=[
                     (90, 24.99, None, "Auf Lager"),
                     (40, 23.49, None, "Auf Lager"),
                     (7,  22.99, None, "Auf Lager"),
                 ]),
            dict(pi=4,  shop_name="3DJake", url="https://www.3djake.de/polymaker/polyterra-pla-army-green",
                 manual_price=20.49, shipping_price=3.90,
                 history=[
                     (30, 22.99, 3.90, "Auf Lager"),
                     (8,  20.49, 3.90, "Auf Lager"),
                 ]),
            dict(pi=9,  shop_name="Prusa Shop", url="https://www.prusa3d.com/prusament-pla-galaxy-black",
                 manual_price=29.99,
                 history=[
                     (120, 27.99, None, "Auf Lager"),
                     (60,  29.99, None, "Auf Lager"),
                     (21,  31.99, None, "Ausverkauft"),
                     (4,   29.99, None, "Auf Lager"),
                 ]),
            dict(pi=7,  shop_name="Amazon DE", url="https://www.amazon.de/dp/esun-epla-orange",
                 manual_price=16.99, shipping_price=0.00,
                 history=[
                     (35, 18.99, 0.0, "Auf Lager"),
                     (15, 17.49, 0.0, "Auf Lager"),
                     (2,  16.99, 0.0, "Auf Lager"),
                 ]),
        ]

        new_links = 0
        new_snaps = 0
        now = datetime.utcnow()

        for rl in raw_links:
            pid = products[rl["pi"]].id
            sl, link_created = await get_or_create_shoplink(session, pid, rl)
            if link_created:
                new_links += 1

            # Only seed snapshots if none exist yet for this link
            snap_count = (await session.execute(
                select(PriceSnapshot).where(PriceSnapshot.shop_link_id == sl.id).limit(1)
            )).scalar_one_or_none()
            if snap_count is not None:
                continue

            for days_ago, price, ship, avail in sorted(rl.get("history", []), key=lambda x: x[0], reverse=True):
                session.add(PriceSnapshot(
                    shop_link_id=sl.id,
                    price=price,
                    shipping_price=ship,
                    currency="EUR",
                    availability=avail,
                    captured_at=now - timedelta(days=days_ago),
                    source="manual",
                ))
                new_snaps += 1

        await session.commit()
        print(
            f"Done: {new_products} new products, {new_links} new shop links, "
            f"{new_snaps} new snapshots ({len(mfrs)} manufacturers ensured)"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
