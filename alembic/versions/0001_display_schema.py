"""Create MySQL display schema.

Revision ID: 0001_display_schema
Revises:
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

from backend.app.db_models import Base


revision = "0001_display_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=True)
