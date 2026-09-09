"""initial game schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Shared by the room sequence array and round target; create exactly once.
    postgresql.ENUM(
        "happy",
        "sad",
        "angry",
        "surprise",
        "neutral",
        "disgust",
        "fear",
        name="emotion_label",
    ).create(op.get_bind(), checkfirst=False)
    op.create_table(
        "users",
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("uuid", name=op.f("pk_users")),
    )
    op.create_index("idx_users_last_seen", "users", ["last_seen_at"], unique=False)
    op.create_table(
        "rooms",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("invite_slug", sa.Text(), nullable=False),
        sa.Column("host_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("waiting", "playing", "finished", "closed", name="room_status"),
            server_default="waiting",
            nullable=False,
        ),
        sa.Column("round_count", sa.SmallInteger(), server_default="5", nullable=False),
        sa.Column("time_limit_sec", sa.SmallInteger(), server_default="20", nullable=False),
        sa.Column("emotion_set", sa.Text(), server_default="full", nullable=False),
        sa.Column(
            "emotion_sequence",
            postgresql.ARRAY(
                postgresql.ENUM(
                    "happy",
                    "sad",
                    "angry",
                    "surprise",
                    "neutral",
                    "disgust",
                    "fear",
                    name="emotion_label",
                    create_type=False,
                )
            ),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("current_round_id", sa.BigInteger(), nullable=True),
        sa.Column("consecutive_voided", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("emotion_set = 'full'", name=op.f("ck_rooms_emotion_set")),
        sa.CheckConstraint("char_length(invite_slug) >= 12", name=op.f("ck_rooms_slug_len")),
        sa.CheckConstraint("consecutive_voided >= 0", name=op.f("ck_rooms_consecutive_voided")),
        sa.CheckConstraint("round_count IN (3,5,7)", name=op.f("ck_rooms_round_count")),
        sa.CheckConstraint("time_limit_sec IN (15,20,30)", name=op.f("ck_rooms_time_limit")),
        sa.ForeignKeyConstraint(
            ["host_user_id"], ["users.uuid"], name=op.f("fk_rooms_host_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rooms")),
        sa.UniqueConstraint("invite_slug", name=op.f("uq_rooms_invite_slug")),
    )
    op.create_index(
        "idx_rooms_last_active",
        "rooms",
        ["last_active_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('waiting','playing','finished')"),
    )
    op.create_index(
        "uq_room_active_owner",
        "rooms",
        ["host_user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('waiting','playing')"),
    )
    op.create_table(
        "participants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=False),
        sa.Column("color_tag", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("active", "waiting_next_game", "left", name="participant_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "connection_status",
            postgresql.ENUM("connected", "disconnected", name="connection_status"),
            server_default="connected",
            nullable=False,
        ),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_points", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "best_round_score", sa.Numeric(precision=4, scale=1), server_default="0", nullable=False
        ),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "best_round_score BETWEEN 0 AND 100", name=op.f("ck_participants_best_round_score")
        ),
        sa.CheckConstraint(
            "char_length(nickname) BETWEEN 2 AND 10", name=op.f("ck_participants_nickname_len")
        ),
        sa.CheckConstraint("color_tag BETWEEN 0 AND 11", name=op.f("ck_participants_color_tag")),
        sa.CheckConstraint("total_points >= 0", name=op.f("ck_participants_total_points")),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.id"],
            name=op.f("fk_participants_room_id_rooms"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.uuid"], name=op.f("fk_participants_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participants")),
        sa.UniqueConstraint("room_id", "user_id", name="uq_room_user"),
    )
    op.create_index(
        "idx_participants_room_status", "participants", ["room_id", "status"], unique=False
    )
    op.create_table(
        "rounds",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.SmallInteger(), nullable=False),
        sa.Column(
            "target_emotion",
            postgresql.ENUM(
                "happy",
                "sad",
                "angry",
                "surprise",
                "neutral",
                "disgust",
                "fear",
                name="emotion_label",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "revealed",
                "capturing",
                "scoring",
                "finalized",
                "voided",
                "closed",
                name="round_status",
            ),
            server_default="revealed",
            nullable=False,
        ),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewing_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('"index" BETWEEN 1 AND 7', name=op.f("ck_rounds_round_index")),
        sa.CheckConstraint("deadline_at > revealed_at", name=op.f("ck_rounds_deadline")),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name=op.f("fk_rounds_room_id_rooms"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rounds")),
        sa.UniqueConstraint("room_id", "id", name="uq_round_room_id"),
        sa.UniqueConstraint("room_id", "index", name="uq_round_index"),
    )
    op.create_table(
        "round_skips",
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("participant_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_round_skips_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"],
            ["rounds.id"],
            name=op.f("fk_round_skips_round_id_rounds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("round_id", "participant_id", name=op.f("pk_round_skips")),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("participant_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("submitted", "no_face", "failed", "missed", name="submission_status"),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_score", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("top_emotions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rank", sa.SmallInteger(), nullable=True),
        sa.Column("rank_points", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("like_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("question_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "(status = 'missed' AND received_at IS NULL) OR "
            "(status <> 'missed' AND received_at IS NOT NULL)",
            name=op.f("ck_submissions_received"),
        ),
        sa.CheckConstraint(
            "rank_points >= 0 AND like_count >= 0 AND question_count >= 0",
            name=op.f("ck_submissions_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "target_score IS NULL OR target_score BETWEEN 0 AND 100",
            name=op.f("ck_submissions_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_submissions_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"],
            ["rounds.id"],
            name=op.f("fk_submissions_round_id_rounds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
        sa.UniqueConstraint("round_id", "participant_id", name="uq_round_participant"),
    )
    op.create_table(
        "reactions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_participant_id", sa.BigInteger(), nullable=False),
        sa.Column("target_submission_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "type", postgresql.ENUM("like", "question", name="reaction_type"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_participant_id"],
            ["participants.id"],
            name=op.f("fk_reactions_actor_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"],
            ["rounds.id"],
            name=op.f("fk_reactions_round_id_rounds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_submission_id"],
            ["submissions.id"],
            name=op.f("fk_reactions_target_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reactions")),
        sa.UniqueConstraint(
            "target_submission_id", "actor_participant_id", "type", name="uq_reaction"
        ),
    )
    op.create_foreign_key(
        "fk_room_current_round",
        "rooms",
        "rounds",
        ["id", "current_round_id"],
        ["room_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("fk_room_current_round", "rooms", type_="foreignkey")
    op.drop_table("reactions")
    op.drop_table("submissions")
    op.drop_table("round_skips")
    op.drop_table("rounds")
    op.drop_index("idx_participants_room_status", table_name="participants")
    op.drop_table("participants")
    op.drop_index(
        "uq_room_active_owner",
        table_name="rooms",
        postgresql_where=sa.text("status IN ('waiting','playing')"),
    )
    op.drop_index(
        "idx_rooms_last_active",
        table_name="rooms",
        postgresql_where=sa.text("status IN ('waiting','playing','finished')"),
    )
    op.drop_table("rooms")
    op.drop_index("idx_users_last_seen", table_name="users")
    op.drop_table("users")

    # PostgreSQL enum types survive DROP TABLE; remove them for repeatable rollback.
    for name in (
        "reaction_type",
        "submission_status",
        "round_status",
        "connection_status",
        "participant_status",
        "emotion_label",
        "room_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=False)
