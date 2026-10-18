"""material master, warehouses, BOM header and items, order colour

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATERIAL_COLUMNS = (
    ("material_group", sa.String()),
    ("color", sa.String()),
    ("reorder_point", sa.Double()),
    ("lead_time_days", sa.BigInteger()),
    ("default_vendor_id", sa.String()),
    ("default_warehouse_id", sa.String()),
)

STOCK_COLUMNS = (
    ("warehouse_id", sa.String()),
    ("reserved_qty", sa.Double()),
    ("incoming_qty", sa.Double()),
)


def upgrade() -> None:
    # 1. New columns on dim_material
    for name, type_ in MATERIAL_COLUMNS:
        op.add_column("dim_material", sa.Column(name, type_, nullable=True))

    # 2. New columns on fact_material_stock
    for name, type_ in STOCK_COLUMNS:
        op.add_column("fact_material_stock", sa.Column(name, type_, nullable=True))

    # 3. New column on fact_customer_order
    op.add_column("fact_customer_order", sa.Column("color", sa.String(), nullable=True))

    # 4. New tables: dim_warehouse, bom_header, bom_item
    op.create_table(
        "dim_warehouse",
        sa.Column("warehouse_id", sa.String(), nullable=False),
        sa.Column("warehouse_name", sa.String(), nullable=True),
        sa.Column("warehouse_type", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("warehouse_id"),
    )

    op.create_table(
        "bom_header",
        sa.Column("bom_id", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("bom_id"),
    )

    op.create_table(
        "bom_item",
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("bom_id", sa.String(), nullable=True),
        sa.Column("line_no", sa.BigInteger(), nullable=True),
        sa.Column("material_id", sa.String(), nullable=True),
        sa.Column("qty_per_unit", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("scrap_pct", sa.Double(), nullable=True),
        sa.Column("process", sa.String(), nullable=True),
        sa.Column("color", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("item_id"),
    )


def downgrade() -> None:
    op.drop_table("bom_item")
    op.drop_table("bom_header")
    op.drop_table("dim_warehouse")

    op.drop_column("fact_customer_order", "color")

    for name, _ in reversed(STOCK_COLUMNS):
        op.drop_column("fact_material_stock", name)

    for name, _ in reversed(MATERIAL_COLUMNS):
        op.drop_column("dim_material", name)
