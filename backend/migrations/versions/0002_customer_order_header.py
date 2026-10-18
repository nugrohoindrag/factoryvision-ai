"""customer order header and customer contact fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CUSTOMER_COLUMNS = ("pic_name", "pic_contact", "delivery_address", "dock_number")
ORDER_COLUMNS = (
    ("order_number", sa.String()),
    ("line_no", sa.BigInteger()),
    ("po_number", sa.String()),
    ("order_channel", sa.String()),
    ("customer_pic", sa.String()),
    ("delivery_address", sa.String()),
    ("dock_number", sa.String()),
)


def upgrade() -> None:
    for name in CUSTOMER_COLUMNS:
        op.add_column("dim_customer", sa.Column(name, sa.String(), nullable=True))
    for name, type_ in ORDER_COLUMNS:
        op.add_column("fact_customer_order", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(ORDER_COLUMNS):
        op.drop_column("fact_customer_order", name)
    for name in reversed(CUSTOMER_COLUMNS):
        op.drop_column("dim_customer", name)
