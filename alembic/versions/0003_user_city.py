"""Add residence city field to users.

Revision ID: 0003_user_city
Revises: 0002_user_profile
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_user_city"
down_revision = "0002_user_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("city", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "city")
