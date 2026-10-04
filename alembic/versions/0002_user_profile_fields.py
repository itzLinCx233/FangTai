"""Add personal info fields to users: gender/birthday/taboo/height_cm/weight_kg.

Revision ID: 0002_user_profile
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_user_profile"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("gender", sa.String(8), nullable=True))
    op.add_column("users", sa.Column("birthday", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("taboo", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("height_cm", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("weight_kg", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "weight_kg")
    op.drop_column("users", "height_cm")
    op.drop_column("users", "taboo")
    op.drop_column("users", "birthday")
    op.drop_column("users", "gender")
