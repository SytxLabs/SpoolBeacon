"""
Seed demo data: manufacturers, filaments, purchases, spools, shop links, snapshots, alerts.

Usage:
  python seed.py            # idempotent — skip existing records
  python seed.py --reset    # truncate all demo tables first, then seed fresh
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

# pylint: disable=wrong-import-position
# Must be imported after load_dotenv() — app.config reads DB_* env vars at import time.
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Config, _build_database_url
from app.models.filament import Manufacturer, FilamentProduct
from app.models.purchase import Purchase, PurchaseLine
from app.models.shoplink import ShopLink
from app.models.price_snapshot import PriceSnapshot
from app.models.price_alert_event import PriceAlertEvent
from app.models.shop_rule import ShopRule
from app.models.spool import Spool, SpoolStatus, StorageStatus
from app.models.print_job import PrintJob, PrintJobLine, PrintJobFile, PrintFileKind, PrintJobStatus
# pylint: enable=wrong-import-position


# Tiny valid ASCII STL (a 10mm cube) used to seed one "uploaded file" print job
# so the 3D preview has something real to render in screenshots/demos.
_DEMO_STL = b"""solid cube
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 10 0
vertex 10 10 0
endloop
endfacet
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 10 10 0
vertex 10 0 0
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 10
vertex 10 10 10
vertex 0 10 10
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 10
vertex 10 0 10
vertex 10 10 10
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 10 0 0
vertex 10 0 10
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 10 0 10
vertex 0 0 10
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 0 10 0
vertex 0 10 10
vertex 10 10 10
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 0 10 0
vertex 10 10 10
vertex 10 10 0
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 10 10
vertex 0 10 0
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 0 10
vertex 0 10 10
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 10 0 0
vertex 10 10 0
vertex 10 10 10
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 10 0 0
vertex 10 10 10
vertex 10 0 10
endloop
endfacet
endsolid cube
"""


# ── helpers ────────────────────────────────────────────────────────────────────

async def clear_tables(session: AsyncSession) -> None:
    """Delete all demo data in FK-safe order. Does NOT touch users or app_settings."""
    demo_uploads = (await session.execute(
        select(PrintJobFile.stored_filename).where(PrintJobFile.stored_filename.is_not(None))
    )).scalars().all()
    for stored_filename in demo_uploads:
        (Path(Config.UPLOAD_DIR) / stored_filename).unlink(missing_ok=True)

    for model in (
        PrintJobFile, PrintJobLine, PrintJob,
        PriceAlertEvent, PriceSnapshot, ShopLink,
        Spool, PurchaseLine, Purchase,
        FilamentProduct, Manufacturer,
        ShopRule,
    ):
        await session.execute(delete(model))
    await session.commit()
    print("clear_tables: all demo tables truncated.")


async def upsert_print_job(session, spools_by_product: dict, data: dict) -> bool:
    existing = (await session.execute(
        select(PrintJob).where(PrintJob.print_name == data["print_name"])
    )).scalar_one_or_none()
    if existing:
        return False

    status = data["status"]
    job = PrintJob(
        job_code=data["job_code"],
        print_name=data["print_name"],
        notes=data.get("notes"),
        status=status,
        printed_at=data["created_at"],
        completed_at=data.get("completed_at"),
        created_at=data["created_at"],
    )
    session.add(job)
    await session.flush()

    for pi, used_g in data["lines"]:
        spool = spools_by_product[pi][0]
        product = spool.filament_product
        session.add(PrintJobLine(
            print_job_id=job.id,
            spool_id=spool.id,
            spool_code=spool.spool_code,
            product_name=f"{product.manufacturer.name} {product.name} – {product.color_name}",
            used_g=used_g,
        ))

    for fd in data.get("files", []):
        if fd["kind"] == "link":
            session.add(PrintJobFile(
                print_job_id=job.id,
                kind=PrintFileKind.link,
                provider=fd["provider"],
                url=fd["url"],
            ))
        else:
            os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
            stored_filename = f"{uuid4().hex}.stl"
            (Path(Config.UPLOAD_DIR) / stored_filename).write_bytes(_DEMO_STL)
            session.add(PrintJobFile(
                print_job_id=job.id,
                kind=PrintFileKind.upload,
                stored_filename=stored_filename,
                original_filename=fd["original_filename"],
                file_ext="stl",
                file_size_bytes=len(_DEMO_STL),
            ))

    return True


async def upsert_manufacturer(session, name: str, website: str) -> Manufacturer:
    m = (await session.execute(
        select(Manufacturer).where(Manufacturer.name == name)
    )).scalar_one_or_none()
    if m:
        return m
    m = Manufacturer(name=name, website=website)
    session.add(m)
    await session.flush()
    return m


async def upsert_product(session, mfr_id: int, data: dict) -> tuple[FilamentProduct, bool]:
    p = (await session.execute(
        select(FilamentProduct).where(
            FilamentProduct.manufacturer_id == mfr_id,
            FilamentProduct.name == data["name"],
            FilamentProduct.material == data["material"],
            FilamentProduct.color_name == data["color_name"],
        )
    )).scalar_one_or_none()
    if p:
        return p, False
    p = FilamentProduct(
        manufacturer_id=mfr_id,
        name=data["name"],
        material=data["material"],
        color_name=data["color_name"],
        color_hex=data["color_hex"],
        diameter_mm=data.get("diameter_mm", 1.75),
        nominal_weight_g=data.get("nominal_weight_g", 1000),
        notes=data.get("notes"),
    )
    session.add(p)
    await session.flush()
    return p, True


async def upsert_shoplink(session, pid: int, data: dict) -> tuple[ShopLink, bool]:
    sl = (await session.execute(
        select(ShopLink).where(ShopLink.filament_product_id == pid, ShopLink.url == data["url"])
    )).scalar_one_or_none()
    if sl:
        return sl, False
    sl = ShopLink(
        filament_product_id=pid,
        shop_name=data["shop_name"],
        url=data["url"],
        currency=data.get("currency", "EUR"),
        package_weight_g=data.get("package_weight_g", 1000),
        manual_price=data["manual_price"],
        shipping_price=data.get("shipping_price"),
        target_price=data.get("target_price"),
        target_price_per_kg=data.get("target_price_per_kg"),
        is_active=data.get("is_active", True),
        notes=data.get("notes"),
    )
    session.add(sl)
    await session.flush()
    return sl, True


# ── seed ───────────────────────────────────────────────────────────────────────

async def seed(reset: bool = False) -> None:
    engine = create_async_engine(_build_database_url(), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.utcnow()

    async with factory() as session:

        if reset:
            await clear_tables(session)

        # ── Manufacturers ──────────────────────────────────────────────────────
        mfrs = {
            k: await upsert_manufacturer(session, k, v) for k, v in {
                "Bambu Lab":  "https://bambulab.com",
                "Elegoo":     "https://elegoo.com",
                "Polymaker":  "https://polymaker.com",
                "eSUN":       "https://esun3d.com",
                "Prusament":  "https://prusament.com",
                "Fiberlogy":  "https://fiberlogy.com",
                "Anycubic":   "https://www.anycubic.com",
            }.items()
        }

        # ── FilamentProducts ───────────────────────────────────────────────────
        # Indices (pi) used in raw_links / raw_purchases below:
        #  0 Elegoo Rapid PLA+ Black
        #  1 Bambu Lab PLA Basic White
        #  2 Polymaker PolyTerra Army Green
        #  3 eSUN ePETG Black
        #  4 Prusament PETG Prusa Orange
        #  5 Fiberlogy Easy PLA Gray
        #  6 Bambu Lab PLA Basic Black
        #  7 Prusament PLA Galaxy Black
        #  8 Anycubic PLA Basic White   (NEW)
        #  9 eSUN ePLA Pro White        (NEW)
        raw_products = [
            {
                "m": "Elegoo",
                "name": "Rapid PLA+ Black",
                "material": "PLA+",
                "color_name": "Black",
                "color_hex": "#1A1A1A",
            },
            {
                "m": "Bambu Lab",
                "name": "PLA Basic White",
                "material": "PLA",
                "color_name": "White",
                "color_hex": "#F5F5F0",
            },
            {
                "m": "Polymaker",
                "name": "PolyTerra PLA Army Green",
                "material": "PLA",
                "color_name": "Army Green",
                "color_hex": "#4A5240",
            },
            {"m": "eSUN", "name": "ePETG Black", "material": "PETG", "color_name": "Black", "color_hex": "#111111"},
            {
                "m": "Prusament",
                "name": "PETG Prusa Orange",
                "material": "PETG",
                "color_name": "Prusa Orange",
                "color_hex": "#FA6831",
            },
            {
                "m": "Fiberlogy",
                "name": "Easy PLA Gray",
                "material": "PLA",
                "color_name": "Gray",
                "color_hex": "#9E9E9E",
            },
            {
                "m": "Bambu Lab",
                "name": "PLA Basic Black",
                "material": "PLA",
                "color_name": "Black",
                "color_hex": "#1A1A1A",
            },
            {
                "m": "Prusament",
                "name": "PLA Galaxy Black",
                "material": "PLA",
                "color_name": "Galaxy Black",
                "color_hex": "#1C1C2E",
            },
            {
                "m": "Anycubic",
                "name": "PLA Basic White",
                "material": "PLA",
                "color_name": "White",
                "color_hex": "#F0F0EE",
            },
            {
                "m": "eSUN",
                "name": "ePLA Pro White",
                "material": "PLA",
                "color_name": "White",
                "color_hex": "#FAFAFA",
                "notes": "2-roll bundle available on esun3dstore.com.",
            },
        ]
        products = []
        for rp in raw_products:
            p, _ = await upsert_product(session, mfrs[rp["m"]].id, rp)
            products.append(p)

        # ── Purchases + PurchaseLines + Spools ────────────────────────────────
        raw_purchases = [
            {
                "shop_name": "3DJake",
                "order_number": "3DJ-2025-11-0082",
                "purchase_date": date(2025, 11, 4),
                "shipping_price": 4.90,
                "currency": "EUR",
                "lines": [
                    {
                        "pi": 1,
                        "qty": 3,
                        "unit_price": 12.49,
                        "spool_weight_g": 1000,
                        "lot": "BL-2025-W44",
                        "spools": [
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 650,
                                "storage": "Shelf A",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2025, 11, 10),
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf A",
                                "storage_status": StorageStatus.sealed,
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf A",
                                "storage_status": StorageStatus.sealed,
                            },
                        ],
                    },
                    {
                        "pi": 2,
                        "qty": 2,
                        "unit_price": 17.99,
                        "spool_weight_g": 1000,
                        "lot": "PM-AG-2025-38",
                        "spools": [
                            {
                                "status": SpoolStatus.almost_empty,
                                "remaining": 120,
                                "storage": "Workshop",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2025, 11, 20),
                            },
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 780,
                                "storage": "Drybox 1",
                                "storage_status": StorageStatus.drybox,
                                "opened_at": datetime(2026, 1, 5),
                            },
                        ],
                    },
                ],
            },
            {
                "shop_name": "Prusa Shop",
                "order_number": "PRS-EU-2025-34821",
                "purchase_date": date(2025, 12, 18),
                "shipping_price": 6.00,
                "currency": "EUR",
                "lines": [
                    {
                        "pi": 4,
                        "qty": 2,
                        "unit_price": 29.99,
                        "spool_weight_g": 1000,
                        "lot": "PRS-PETG-OR-W50",
                        "spools": [
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 410,
                                "storage": "Shelf B",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2025, 12, 28),
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Vacuum box",
                                "storage_status": StorageStatus.vacuum_sealed,
                            },
                        ],
                    },
                    {
                        "pi": 7,
                        "qty": 1,
                        "unit_price": 29.99,
                        "spool_weight_g": 1000,
                        "lot": "PRS-PLA-GB-W50",
                        "spools": [
                            {
                                "status": SpoolStatus.empty,
                                "remaining": 0,
                                "storage": "Shelf B",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2026, 1, 2),
                            }
                        ],
                    },
                ],
            },
            {
                "shop_name": "Elegoo Official",
                "order_number": "ELG-2026-00441",
                "purchase_date": date(2026, 2, 12),
                "shipping_price": 0.00,
                "currency": "EUR",
                "lines": [
                    {
                        "pi": 0,
                        "qty": 4,
                        "unit_price": 18.99,
                        "spool_weight_g": 1000,
                        "lot": "ELG-RPLA-BK-0226",
                        "spools": [
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 850,
                                "storage": "Drybox 1",
                                "storage_status": StorageStatus.drybox,
                                "opened_at": datetime(2026, 2, 20),
                            },
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 920,
                                "storage": "Drybox 1",
                                "storage_status": StorageStatus.drybox,
                                "opened_at": datetime(2026, 3, 1),
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf A",
                                "storage_status": StorageStatus.vacuum_sealed,
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf A",
                                "storage_status": StorageStatus.vacuum_sealed,
                            },
                        ],
                    }
                ],
            },
            {
                "shop_name": "Fiberlogy EU Store",
                "order_number": "FBG-EU-2026-1188",
                "purchase_date": date(2026, 4, 3),
                "shipping_price": 5.50,
                "currency": "EUR",
                "lines": [
                    {
                        "pi": 5,
                        "qty": 2,
                        "unit_price": 21.90,
                        "spool_weight_g": 850,
                        "lot": "FBG-EPLA-GR-Q1-26",
                        "spools": [
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 600,
                                "storage": "Shelf B",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2026, 4, 10),
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 850,
                                "storage": "Shelf B",
                                "storage_status": StorageStatus.sealed,
                            },
                        ],
                    },
                    {
                        "pi": 3,
                        "qty": 1,
                        "unit_price": 22.90,
                        "spool_weight_g": 1000,
                        "lot": "FBG-PETG-BK-Q1-26",
                        "spools": [
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf B",
                                "storage_status": StorageStatus.sealed,
                            }
                        ],
                    },
                ],
            },
            {
                "shop_name": "Anycubic Store",
                "order_number": "ANC-2026-09914",
                "purchase_date": date(2026, 5, 20),
                "shipping_price": 0.00,
                "currency": "USD",
                "lines": [
                    {
                        "pi": 8,
                        "qty": 3,
                        "unit_price": 17.99,
                        "spool_weight_g": 1000,
                        "lot": "ANC-PLA-WH-Q2-26",
                        "spools": [
                            {
                                "status": SpoolStatus.opened,
                                "remaining": 900,
                                "storage": "Shelf C",
                                "storage_status": StorageStatus.open,
                                "opened_at": datetime(2026, 5, 28),
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf C",
                                "storage_status": StorageStatus.sealed,
                            },
                            {
                                "status": SpoolStatus.new,
                                "remaining": 1000,
                                "storage": "Shelf C",
                                "storage_status": StorageStatus.sealed,
                            },
                        ],
                    }
                ],
            },
        ]

        ts_base = int(now.timestamp())
        line_seq = 0

        for rp in raw_purchases:
            existing_purchase = (await session.execute(
                select(Purchase).where(Purchase.order_number == rp["order_number"])
            )).scalar_one_or_none()
            if existing_purchase:
                continue

            purchase = Purchase(
                purchase_date=rp["purchase_date"],
                shop_name=rp["shop_name"],
                order_number=rp["order_number"],
                shipping_price=rp["shipping_price"],
                currency=rp["currency"],
            )
            session.add(purchase)
            await session.flush()

            for ld in rp["lines"]:
                line_seq += 1
                product = products[ld["pi"]]
                line = PurchaseLine(
                    purchase_id=purchase.id,
                    filament_product_id=product.id,
                    quantity=ld["qty"],
                    unit_price=ld["unit_price"],
                    currency=rp["currency"],
                    spool_weight_g=ld["spool_weight_g"],
                    lot_number=ld.get("lot"),
                )
                session.add(line)
                await session.flush()

                for si, sd in enumerate(ld["spools"]):
                    code = f"SB-{product.id}-{line.id}-{ts_base + line_seq}-{si + 1:02d}"
                    spool = Spool(
                        filament_product_id=product.id,
                        purchase_line_id=line.id,
                        spool_code=code,
                        status=sd["status"],
                        initial_weight_g=float(ld["spool_weight_g"]),
                        remaining_weight_g=float(sd["remaining"]),
                        storage_location=sd.get("storage"),
                        storage_status=sd.get("storage_status", StorageStatus.unknown),
                        opened_at=sd.get("opened_at"),
                    )
                    session.add(spool)

        await session.flush()

        # ── Print Jobs (board demo data) ──────────────────────────────────────
        spools_by_product: dict[int, list[Spool]] = {}
        for pi, product in enumerate(products):
            rows = (await session.execute(
                select(Spool)
                .options(selectinload(Spool.filament_product).selectinload(FilamentProduct.manufacturer))
                .where(Spool.filament_product_id == product.id)
                .order_by(Spool.id)
            )).scalars().all()
            if rows:
                spools_by_product[pi] = rows

        raw_print_jobs = [
            {
                "job_code": "PJ-DEMO-001",
                "print_name": "Articulated Dragon",
                "notes": "Fine layer height (0.12mm). Queue for the weekend, needs ~9h.",
                "status": PrintJobStatus.planned,
                "created_at": now - timedelta(hours=6),
                "lines": [(5, 45.0)],
                "files": [{"kind": "link", "provider": "printables", "url": "https://www.printables.com/model/198813-flexi-print-in-place-dragon"}],
            },
            {
                "job_code": "PJ-DEMO-002",
                "print_name": "Calibration Cube",
                "notes": "First-layer + dimensional accuracy check for the new Black spool.",
                "status": PrintJobStatus.planned,
                "created_at": now - timedelta(hours=3),
                "lines": [(0, 8.0)],
                "files": [{"kind": "upload", "original_filename": "calibration_cube.stl"}],
            },
            {
                "job_code": "PJ-DEMO-003",
                "print_name": "Phone Stand v2",
                "notes": None,
                "status": PrintJobStatus.planned,
                "created_at": now - timedelta(hours=1),
                "lines": [(8, 28.0)],
                "files": [{"kind": "link", "provider": "makerworld", "url": "https://makerworld.com/en/models/511693-adjustable-phone-stand"}],
            },
            {
                "job_code": "PJ-DEMO-004",
                "print_name": "Camera Mount Arm",
                "notes": "On the printer now — check bed adhesion at layer 10.",
                "status": PrintJobStatus.printing,
                "created_at": now - timedelta(hours=2),
                "lines": [(1, 62.0)],
                "files": [{"kind": "link", "provider": "thingiverse", "url": "https://www.thingiverse.com/thing:3861767"}],
            },
            {
                "job_code": "PJ-DEMO-005",
                "print_name": "Voronoi Vase",
                "notes": "Vase mode, 0.6mm nozzle. Turned out great.",
                "status": PrintJobStatus.done,
                "created_at": now - timedelta(days=4),
                "completed_at": now - timedelta(days=4, hours=-5),
                "lines": [(2, 96.0)],
                "files": [],
            },
            {
                "job_code": "PJ-DEMO-006",
                "print_name": "Dual-Color Nameplate",
                "notes": "AMS colour swap at z=1.2mm for the black inlay.",
                "status": PrintJobStatus.done,
                "created_at": now - timedelta(days=1, hours=8),
                "completed_at": now - timedelta(days=1, hours=5),
                "lines": [(1, 18.0), (3, 6.0)],
                "files": [],
            },
        ]

        print_job_count = 0
        for pj in raw_print_jobs:
            if all(pi in spools_by_product for pi, _ in pj["lines"]):
                created = await upsert_print_job(session, spools_by_product, pj)
                if created:
                    print_job_count += 1

        await session.flush()

        # ── ShopLinks + PriceSnapshots + Alerts ───────────────────────────────
        raw_links = [
            # ── Elegoo Rapid PLA+ Black (pi=0) ──────────────────────────────
            {
                "pi": 0,
                "shop_name": "Elegoo Official",
                "currency": "EUR",
                "url": "https://www.elegoo.com/products/elegoo-rapid-series-pla-plus",
                "package_weight_g": 1000,
                "manual_price": 18.99,
                "shipping_price": 0.00,
                "target_price": 17.00,
                "is_active": False,
                "notes": "Free shipping above 25 EUR.",
                "history": [
                    (90, 21.99, 0.0, "In Stock"),
                    (60, 20.49, 0.0, "In Stock"),
                    (30, 19.49, 0.0, "In Stock"),
                    (7, 18.99, 0.0, "In Stock"),
                    (1, 18.99, 0.0, "In Stock"),
                ],
            },
            {
                "pi": 0,
                "shop_name": "Amazon DE",
                "currency": "EUR",
                "url": "https://www.amazon.de/dp/B0CF35BLP5",
                "package_weight_g": 1000,
                "manual_price": 19.89,
                "shipping_price": 0.00,
                "is_active": False,
                "notes": "Blocked — returns empty page without authenticated browser session.",
                "history": [(14, 21.99, 0.0, "In Stock"), (3, 19.89, 0.0, "In Stock")],
            },
            # ── Bambu Lab PLA Basic White (pi=1) ─────────────────────────────
            {
                "pi": 1,
                "shop_name": "Bambu Lab EU Store",
                "currency": "EUR",
                "url": "https://eu.store.bambulab.com/en/products/pla-basic-filament",
                "package_weight_g": 1000,
                "manual_price": 22.99,
                "shipping_price": None,
                "target_price": 20.00,
                "target_price_per_kg": 20.00,
                "is_active": True,
                "notes": "Cloudscraper adapter — eu.store.bambulab.com (NOT bambulab.com which is blocked).",
                "history": [
                    (75, 24.99, None, "In Stock"),
                    (40, 23.99, None, "In Stock"),
                    (10, 22.99, None, "In Stock"),
                    (2, 22.99, None, "In Stock"),
                ],
            },
            {
                "pi": 1,
                "shop_name": "3DJake",
                "currency": "EUR",
                "url": "https://www.3djake.de/bambu-lab/pla-basic-white",
                "package_weight_g": 1000,
                "manual_price": 12.49,
                "shipping_price": 4.90,
                "target_price": 18.00,
                "is_active": True,
                "history": [
                    (60, 14.99, 4.90, "In Stock"),
                    (30, 13.99, 4.90, "In Stock"),
                    (14, 13.49, 4.90, "In Stock"),
                    (3, 12.49, 4.90, "In Stock"),
                ],
                "alert_resolved": True,
            },
            {
                "pi": 1,
                "shop_name": "Filamentworld",
                "currency": "EUR",
                "url": (
                    "https://filamentworld.de/shop/filament-3d-drucker/"
                    "bambu-lab-pla-basic-weiss-1-75mm/?switch_shop=b2c"
                ),
                "package_weight_g": 1000,
                "manual_price": 19.90,
                "shipping_price": 4.90,
                "target_price": 25.00,
                "is_active": True,
                "notes": "WooCommerce EUR. Use direct product URL — category pages may return 0,00€.",
                "history": [(30, 22.90, 4.90, "In Stock"), (14, 21.90, 4.90, "In Stock"), (5, 19.90, 4.90, "In Stock")],
            },
            # ── Polymaker PolyTerra Army Green (pi=2) ────────────────────────
            {
                "pi": 2,
                "shop_name": "3DJake",
                "currency": "EUR",
                "url": "https://www.3djake.de/polymaker/polyterra-pla-army-green",
                "package_weight_g": 1000,
                "manual_price": 17.99,
                "shipping_price": 3.90,
                "target_price": 22.00,
                "is_active": True,
                "history": [
                    (45, 22.99, 3.90, "In Stock"),
                    (20, 20.49, 3.90, "In Stock"),
                    (5, 17.99, 3.90, "In Stock"),
                    (0, None, None, None),
                ],
                "alert_active": True,
            },
            {
                "pi": 2,
                "shop_name": "Polymaker Shop",
                "currency": "USD",
                "url": "https://polymaker.com/product/polyterra-pla/",
                "package_weight_g": 1000,
                "manual_price": 22.99,
                "shipping_price": None,
                "is_active": False,
                "notes": "Marketing/product-info site — no prices. Products sold via distributors.",
                "history": [(30, 24.99, None, "In Stock"), (5, 22.99, None, "In Stock")],
            },
            # ── Prusament PETG Prusa Orange (pi=4) ───────────────────────────
            {
                "pi": 4,
                "shop_name": "Prusa Shop",
                "currency": "EUR",
                "url": "https://www.prusa3d.com/de/produkt/prusament-petg-prusa-orange-1kg/",
                "package_weight_g": 1000,
                "manual_price": 29.99,
                "shipping_price": 6.00,
                "target_price": 32.00,
                "is_active": True,
                "history": [
                    (90, 34.99, 6.00, "In Stock"),
                    (45, 32.99, 6.00, "In Stock"),
                    (14, 30.99, 6.00, "In Stock"),
                    (3, 29.99, 6.00, "In Stock"),
                ],
            },
            # ── Fiberlogy Easy PLA Gray (pi=5) ───────────────────────────────
            {
                "pi": 5,
                "shop_name": "eBay DE",
                "currency": "EUR",
                "url": "https://www.ebay.de/itm/fiberlogy-easy-pla-gray",
                "package_weight_g": 850,
                "manual_price": 19.50,
                "shipping_price": 3.90,
                "is_active": False,
                "notes": "Blocked — eBay Cloudflare protection. Consider eBay Browse API.",
                "history": [(20, 21.90, 3.90, "In Stock"), (8, 19.50, 3.90, "In Stock")],
            },
            # ── Anycubic PLA Basic White (pi=8) ──────────────────────────────
            {
                "pi": 8,
                "shop_name": "Anycubic",
                "currency": "USD",
                "url": "https://www.anycubic.com/products/pla-filament",
                "package_weight_g": 1000,
                "manual_price": 17.99,
                "shipping_price": None,
                "target_price": 20.00,
                "is_active": True,
                "notes": "Shopify USD store. SSR — no JS required. Adapter available.",
                "history": [(30, 22.99, None, "In Stock"), (14, 19.99, None, "In Stock"), (5, 17.99, None, "In Stock")],
            },
            # ── eSUN ePLA Pro White (pi=9) ────────────────────────────────────
            {
                "pi": 9,
                "shop_name": "eSUN Store",
                "currency": "USD",
                "url": "https://esun3dstore.com/products/pla-pro-2-rolls",
                "package_weight_g": 2000,
                "manual_price": 31.99,
                "shipping_price": None,
                "target_price": 38.00,
                "is_active": True,
                "notes": "2-roll bundle (2 kg). Cloudscraper adapter — esun3dstore.com. USD store.",
                "history": [(20, 37.99, None, "In Stock"), (10, 34.99, None, "In Stock"), (3, 31.99, None, "In Stock")],
            },
        ]

        alert_count = 0
        snap_count = 0

        for rl in raw_links:
            product = products[rl["pi"]]
            sl, _ = await upsert_shoplink(session, product.id, rl)

            snap_exists = (await session.execute(
                select(PriceSnapshot).where(PriceSnapshot.shop_link_id == sl.id).limit(1)
            )).scalar_one_or_none()
            if snap_exists:
                continue

            last_snap = None
            for days_ago, price, ship, avail in sorted(
                rl.get("history", []), key=lambda x: x[0], reverse=True
            ):
                if price is None:
                    snap = PriceSnapshot(
                        shop_link_id=sl.id, price=0.0, currency=rl.get("currency", "EUR"),
                        captured_at=now - timedelta(days=days_ago, hours=2),
                        source="error", error_message="Connection timeout — selector returned no match.",
                    )
                else:
                    snap = PriceSnapshot(
                        shop_link_id=sl.id, price=price, shipping_price=ship,
                        currency=rl.get("currency", "EUR"), availability=avail,
                        captured_at=now - timedelta(days=days_ago), source="manual",
                    )
                    last_snap = snap
                session.add(snap)
                snap_count += 1

            await session.flush()

            if rl.get("alert_resolved") and last_snap:
                session.add(PriceAlertEvent(
                    shop_link_id=sl.id, price_snapshot_id=last_snap.id,
                    alert_type="target_price",
                    message=(
                        f"Target price hit: {sl.target_price:.2f} EUR — "
                        f"current total {last_snap.price + (last_snap.shipping_price or 0):.2f} EUR"
                    ),
                    created_at=now - timedelta(days=3),
                    resolved_at=now - timedelta(days=1),
                ))
                alert_count += 1

            if rl.get("alert_active") and last_snap:
                session.add(PriceAlertEvent(
                    shop_link_id=sl.id, price_snapshot_id=last_snap.id,
                    alert_type="target_price",
                    message=(
                        f"Target price hit: {sl.target_price:.2f} EUR — "
                        f"current total {last_snap.price + (last_snap.shipping_price or 0):.2f} EUR"
                    ),
                    created_at=now - timedelta(days=5),
                    resolved_at=None,
                ))
                alert_count += 1

        # ── ShopRules ─────────────────────────────────────────────────────────
        # Domains with a registered adapter (app/shop_adapters/registry.py) are
        # intentionally NOT seeded here — the adapter always takes priority over
        # a ShopRule for the same domain, so a rule for it would be dead weight
        # and confusing in the UI. See app.shop_adapters.registry.registered_domains().
        raw_rules = [
            {
                "domain": "filamentworld.de",
                "price_selector": ".price",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h1",
                "availability_selector": ".stock",
                "currency": "EUR",
                "test_url": (
                    "https://filamentworld.de/shop/filament-3d-drucker/"
                    "pla-filament-1-75-mm-braun/?switch_shop=b2c"
                ),
                "is_active": True,
                "notes": "WooCommerce EUR. Confirmed 2026-06-30.",
            },
            # ── Blocked / inactive (reference only) ───────────────────────────
            {
                "domain": "bambulab.com",
                "price_selector": "[class*='price']",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h1",
                "currency": "EUR",
                "test_url": "https://bambulab.com/de-de/filament/pla-basic",
                "is_active": False,
                "notes": (
                    "BLOCKED — Cloudflare WAF (httpx + Playwright + cloudscraper). "
                    "Use eu.store.bambulab.com instead."
                ),
            },
            {
                "domain": "amazon.de",
                "price_selector": ".a-price .a-offscreen",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "#productTitle",
                "availability_selector": "#availability span",
                "currency": "EUR",
                "test_url": "",
                "is_active": False,
                "notes": "BLOCKED — ASIN pages return 404 without session. Future: Amazon Product Advertising API.",
            },
            {
                "domain": "ebay.de",
                "price_selector": ".x-price-primary .ux-textspans",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h1.x-item-title__mainTitle",
                "currency": "EUR",
                "test_url": "",
                "is_active": False,
                "notes": "BLOCKED — Cloudflare (httpx + Playwright + cloudscraper). Future: eBay Browse API.",
            },
            {
                "domain": "aliexpress.com",
                "price_selector": "[class*='price--current']",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h1",
                "currency": "EUR",
                "test_url": "",
                "is_active": False,
                "notes": "JS-rendered — headless Playwright returns empty body (anti-bot fingerprinting).",
            },
            {
                "domain": "polymaker.com",
                "price_selector": ".price-item--regular",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h2",
                "currency": "USD",
                "test_url": "https://polymaker.com/product/polyterra-pla/",
                "is_active": False,
                "notes": "Marketing/product-info site only — no purchasable prices. Sold via distributors.",
            },
            {
                "domain": "sunlu.com",
                "price_selector": "[class*='price']",
                "price_regex": r"\d+[,\.]\d{2}",
                "title_selector": "h1",
                "currency": "USD",
                "test_url": "",
                "is_active": False,
                "notes": "HTTP 500 on collection pages. Server instability or geo-blocking.",
            },
        ]

        rule_count = 0
        for rr in raw_rules:
            existing = (await session.execute(
                select(ShopRule).where(ShopRule.domain == rr["domain"])
            )).scalar_one_or_none()
            if existing:
                continue
            session.add(ShopRule(**rr))
            rule_count += 1

        await session.commit()
        print(
            f"Seed complete — {len(mfrs)} manufacturers, {len(products)} products, "
            f"{len(raw_purchases)} purchases, {snap_count} snapshots, "
            f"{alert_count} alerts, {rule_count} shop rules, "
            f"{print_job_count} print jobs seeded."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed(reset="--reset" in sys.argv))
