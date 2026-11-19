"""Create turn_log table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_log",
        sa.Column("turn_id", sa.String(32), primary_key=True),
        sa.Column("hub_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("path", sa.String(16), nullable=False),
        sa.Column("intent", sa.String(64), nullable=False),
        sa.Column("skill_result", sa.String(8), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("t_stt_ms", sa.Integer(), nullable=True),
        sa.Column("t_understand_ms", sa.Integer(), nullable=True),
        sa.Column("t_act_ms", sa.Integer(), nullable=True),
        sa.Column("t_first_audio_ms", sa.Integer(), nullable=True),
        sa.Column("llm_tool_calls", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_turn_log_created_at", "turn_log", ["created_at"])
    op.create_index("ix_turn_log_hub_created", "turn_log", ["hub_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_turn_log_hub_created", table_name="turn_log")
    op.drop_index("ix_turn_log_created_at", table_name="turn_log")
    op.drop_table("turn_log")
