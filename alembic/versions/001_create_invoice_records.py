"""Create invoice_records table

Revision ID: 001_create_invoice_records
Revises: (none — this is the first migration, there is nothing before it)
Create Date: 2026-10-04

WHAT THIS FILE IS:
  A migration file is a versioned script that describes a schema change.
  Every migration has two functions:
    - upgrade():   what to DO   (applied forward in time, e.g. `alembic upgrade head`)
    - downgrade(): what to UNDO (rolled back, e.g. `alembic downgrade -1`)

HOW ALEMBIC TRACKS THIS:
  Alembic creates a table called `alembic_version` in your PostgreSQL database.
  After this migration runs successfully, Alembic writes the revision ID
  ('001_create_invoice_records') into that table. Next time you run
  `alembic upgrade head`, Alembic checks that table, sees this revision was
  already applied, and skips it.

GENERATING FUTURE MIGRATIONS:
  When you add a new column/model, run:
    alembic revision --autogenerate -m "add_user_preferences"
  Alembic will compare your SQLAlchemy models (via Base.metadata in env.py)
  against the ACTUAL database schema and auto-write the ALTER TABLE SQL for you.
  Always review the auto-generated file before applying — autogenerate is smart
  but not perfect (it can miss some things like CHECK constraints or custom types).

WHY WRITE MIGRATIONS INSTEAD OF JUST USING `CREATE TABLE IF NOT EXISTS`?
  SQLAlchemy's Base.metadata.create_all() can create tables, but it cannot
  ALTER existing ones. Once a table exists, if you add a column to your model,
  create_all() silently does nothing. Alembic tracks versions, so `ALTER TABLE
  ADD COLUMN` is applied exactly once, in order, on every environment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Alembic uses these identifiers to chain migrations in order.
# `down_revision = None` means this is the first migration in the chain.
revision: str = '001_create_invoice_records'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Applied when running: alembic upgrade head (or alembic upgrade 001_create_invoice_records)
    Creates the invoice_records table and all its indexes.
    """
    op.create_table(
        'invoice_records',

        # Primary key: UUID matches the task_id returned to API clients
        sa.Column('task_id', sa.UUID(), nullable=False),

        # Entra ID Object ID — immutable, unique per user across all tenants.
        # Used for ownership checks (can user X see task Y?).
        sa.Column('user_id', sa.String(length=128), nullable=False),

        # User email / UPN for display purposes. Optional because some token
        # flows don't include it (service principals, etc.)
        sa.Column('user_upn', sa.String(length=256), nullable=True),

        # File metadata
        sa.Column('filename', sa.String(length=256), nullable=False),
        sa.Column('file_hash', sa.String(length=64), nullable=False),   # SHA-256 = always 64 hex chars
        sa.Column('file_size_bytes', sa.Integer(), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),

        # Processing state machine: processing → done | failed
        sa.Column('status', sa.String(length=32), nullable=False, server_default='processing'),

        # Full extraction JSON from Azure. JSONB (not JSON) because:
        #   - JSONB is stored in a binary parsed format (faster reads)
        #   - JSONB supports indexing with GIN indexes (useful for future queries)
        #   - JSON is stored as raw text (slower, no indexing)
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),

        # Top-level fields duplicated from result JSONB for fast SQL queries.
        # Without these, filtering "invoices over $500" requires scanning JSONB.
        # With these, it's a simple indexed WHERE clause.
        sa.Column('vendor_name', sa.String(length=256), nullable=True),
        sa.Column('invoice_total', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=16), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),

        # Error details — only populated when status='failed'
        sa.Column('error_message', sa.Text(), nullable=True),

        # server_default=now() means PostgreSQL sets these automatically on INSERT.
        # The timezone=True stores as TIMESTAMPTZ (timestamp with time zone) —
        # critical for correctness: stores UTC, displays in any local timezone.
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),

        sa.PrimaryKeyConstraint('task_id')
    )

    # Indexes — trade storage for query speed on the columns we'll filter by most often.
    # created automatically named by Alembic's convention: ix_{table}_{column}
    op.create_index(op.f('ix_invoice_records_user_id'), 'invoice_records', ['user_id'], unique=False)
    op.create_index(op.f('ix_invoice_records_file_hash'), 'invoice_records', ['file_hash'], unique=False)
    op.create_index(op.f('ix_invoice_records_status'), 'invoice_records', ['status'], unique=False)
    op.create_index(op.f('ix_invoice_records_vendor_name'), 'invoice_records', ['vendor_name'], unique=False)
    op.create_index(op.f('ix_invoice_records_created_at'), 'invoice_records', ['created_at'], unique=False)


def downgrade() -> None:
    """
    Applied when running: alembic downgrade -1  (or alembic downgrade base)
    Reverses upgrade() exactly — drops indexes first, then the table.
    Order matters: you can't drop a table that still has dependent objects.
    """
    op.drop_index(op.f('ix_invoice_records_created_at'), table_name='invoice_records')
    op.drop_index(op.f('ix_invoice_records_vendor_name'), table_name='invoice_records')
    op.drop_index(op.f('ix_invoice_records_status'), table_name='invoice_records')
    op.drop_index(op.f('ix_invoice_records_file_hash'), table_name='invoice_records')
    op.drop_index(op.f('ix_invoice_records_user_id'), table_name='invoice_records')
    op.drop_table('invoice_records')
