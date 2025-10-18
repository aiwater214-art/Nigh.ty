"""create users stats worlds

Revision ID: 20240101_000001
Revises:
Create Date: 2024-01-01 00:00:01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240101_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "worlds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("active_players", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_worlds_id"), "worlds", ["id"], unique=False)
    op.create_index(op.f("ix_worlds_name"), "worlds", ["name"], unique=True)

    op.create_table(
        "user_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cells_eaten", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("food_eaten", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worlds_explored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sessions_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_stats_id"), "user_stats", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_stats_id"), table_name="user_stats")
    op.drop_table("user_stats")
    op.drop_index(op.f("ix_worlds_name"), table_name="worlds")
    op.drop_index(op.f("ix_worlds_id"), table_name="worlds")
    op.drop_table("worlds")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
