"""rename_spool_status_enum_members_to_english

Revision ID: 033d388a81e3
Revises: fc5b2c227b07
Create Date: 2026-07-02 11:08:30.930017+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '033d388a81e3'
down_revision: Union[str, None] = 'fc5b2c227b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SPOOL_STATUS_MAP = {
    "neu": "new",
    "geoeffnet": "opened",
    "fast_leer": "almost_empty",
    "leer": "empty",
    "archiviert": "archived",
}
_STORAGE_STATUS_MAP = {
    "offen": "open",
    "verschlossen": "sealed",
    "vakuumiert": "vacuum_sealed",
    "unbekannt": "unknown",
    # 'drybox' is unchanged in both languages.
}


def upgrade() -> None:
    # Widen both enum columns to accept old + new values so no row is truncated mid-migration.
    op.alter_column(
        "spools", "status",
        existing_type=sa.Enum("neu", "geoeffnet", "fast_leer", "leer", "archiviert", name="spoolstatus"),
        type_=sa.Enum(
            "neu", "geoeffnet", "fast_leer", "leer", "archiviert",
            "new", "opened", "almost_empty", "empty", "archived",
            name="spoolstatus",
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "spools", "storage_status",
        existing_type=sa.Enum("offen", "verschlossen", "vakuumiert", "drybox", "unbekannt", name="storagestatus"),
        type_=sa.Enum(
            "offen", "verschlossen", "vakuumiert", "drybox", "unbekannt",
            "open", "sealed", "vacuum_sealed", "unknown",
            name="storagestatus",
        ),
        existing_nullable=False,
    )

    for old, new in _SPOOL_STATUS_MAP.items():
        op.execute(f"UPDATE spools SET status = '{new}' WHERE status = '{old}'")
    for old, new in _STORAGE_STATUS_MAP.items():
        op.execute(f"UPDATE spools SET storage_status = '{new}' WHERE storage_status = '{old}'")

    # Narrow to the final English-only value set.
    op.alter_column(
        "spools", "status",
        existing_type=sa.Enum(
            "neu", "geoeffnet", "fast_leer", "leer", "archiviert",
            "new", "opened", "almost_empty", "empty", "archived",
            name="spoolstatus",
        ),
        type_=sa.Enum("new", "opened", "almost_empty", "empty", "archived", name="spoolstatus"),
        existing_nullable=False,
    )
    op.alter_column(
        "spools", "storage_status",
        existing_type=sa.Enum(
            "offen", "verschlossen", "vakuumiert", "drybox", "unbekannt",
            "open", "sealed", "vacuum_sealed", "unknown",
            name="storagestatus",
        ),
        type_=sa.Enum("open", "sealed", "vacuum_sealed", "drybox", "unknown", name="storagestatus"),
        existing_nullable=False,
    )


def downgrade() -> None:
    reverse_spool = {v: k for k, v in _SPOOL_STATUS_MAP.items()}
    reverse_storage = {v: k for k, v in _STORAGE_STATUS_MAP.items()}

    op.alter_column(
        "spools", "status",
        existing_type=sa.Enum("new", "opened", "almost_empty", "empty", "archived", name="spoolstatus"),
        type_=sa.Enum(
            "neu", "geoeffnet", "fast_leer", "leer", "archiviert",
            "new", "opened", "almost_empty", "empty", "archived",
            name="spoolstatus",
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "spools", "storage_status",
        existing_type=sa.Enum("open", "sealed", "vacuum_sealed", "drybox", "unknown", name="storagestatus"),
        type_=sa.Enum(
            "offen", "verschlossen", "vakuumiert", "drybox", "unbekannt",
            "open", "sealed", "vacuum_sealed", "unknown",
            name="storagestatus",
        ),
        existing_nullable=False,
    )

    for new, old in reverse_spool.items():
        op.execute(f"UPDATE spools SET status = '{old}' WHERE status = '{new}'")
    for new, old in reverse_storage.items():
        op.execute(f"UPDATE spools SET storage_status = '{old}' WHERE storage_status = '{new}'")

    op.alter_column(
        "spools", "status",
        existing_type=sa.Enum(
            "neu", "geoeffnet", "fast_leer", "leer", "archiviert",
            "new", "opened", "almost_empty", "empty", "archived",
            name="spoolstatus",
        ),
        type_=sa.Enum("neu", "geoeffnet", "fast_leer", "leer", "archiviert", name="spoolstatus"),
        existing_nullable=False,
    )
    op.alter_column(
        "spools", "storage_status",
        existing_type=sa.Enum(
            "offen", "verschlossen", "vakuumiert", "drybox", "unbekannt",
            "open", "sealed", "vacuum_sealed", "unknown",
            name="storagestatus",
        ),
        type_=sa.Enum("offen", "verschlossen", "vakuumiert", "drybox", "unbekannt", name="storagestatus"),
        existing_nullable=False,
    )
