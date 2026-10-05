"""Add app_settings KV table (runtime-switchable LLM config etc.).

Revision ID: 0004_app_settings
Revises: 0003_user_city
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "0004_app_settings"
down_revision = "0003_user_city"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
