"""Create user_preferences table

Revision ID: 002_create_user_preferences
Revises: 001_create_invoice_records
Create Date: 2026-10-14

WHAT THIS MIGRATION DOES:
  Creates the `user_preferences` table to store per-user UI settings (theme: dark/light).
  Keyed by Entra ID `user_id` (immutable object_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '002_create_user_preferences'
down_revision: Union[str, None] = '001_create_invoice_records'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Creates the user_preferences table."""
    op.create_table(
        'user_preferences',
        sa.Column('user_id', sa.String(length=128), primary_key=True, nullable=False),
        sa.Column('theme', sa.String(length=16), nullable=False, server_default='dark'),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index('ix_user_preferences_user_id', 'user_preferences', ['user_id'], unique=True)


def downgrade() -> None:
    """Rolls back the user_preferences table."""
    op.drop_index('ix_user_preferences_user_id', table_name='user_preferences')
    op.drop_table('user_preferences')
