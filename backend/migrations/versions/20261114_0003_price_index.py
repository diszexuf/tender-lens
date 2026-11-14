"""price index

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_index",
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(10, 4), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("period", name=op.f("pk_price_index")),
    )


def downgrade() -> None:
    op.drop_table("price_index")
