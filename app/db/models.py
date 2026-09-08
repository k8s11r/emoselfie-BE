from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.enums import (
    ConnectionStatus,
    EmotionLabel,
    ParticipantStatus,
    ReactionType,
    RoomStatus,
    RoundStatus,
    SubmissionStatus,
)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def enum_type(enum: type[StrEnum], name: str) -> ENUM:
    return ENUM(enum, name=name, values_callable=lambda values: [item.value for item in values])


class User(Base):
    __tablename__ = "users"
    uuid: Mapped[UUID] = mapped_column(primary_key=True)
    nickname: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (Index("idx_users_last_seen", "last_seen_at"),)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invite_slug: Mapped[str] = mapped_column(Text, unique=True)
    host_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.uuid"))
    status: Mapped[RoomStatus] = mapped_column(
        enum_type(RoomStatus, "room_status"), server_default="waiting"
    )
    round_count: Mapped[int] = mapped_column(SmallInteger, server_default="5")
    time_limit_sec: Mapped[int] = mapped_column(SmallInteger, server_default="20")
    emotion_set: Mapped[str] = mapped_column(Text, server_default="full")
    emotion_sequence: Mapped[list[EmotionLabel]] = mapped_column(
        ARRAY(enum_type(EmotionLabel, "emotion_label")), server_default="{}"
    )
    current_round_id: Mapped[int | None] = mapped_column(BigInteger)
    consecutive_voided: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint("round_count IN (3,5,7)", name="round_count"),
        CheckConstraint("time_limit_sec IN (15,20,30)", name="time_limit"),
        CheckConstraint("char_length(invite_slug) >= 12", name="slug_len"),
        CheckConstraint("emotion_set = 'full'", name="emotion_set"),
        CheckConstraint("consecutive_voided >= 0", name="consecutive_voided"),
        Index(
            "uq_room_active_owner",
            "host_user_id",
            unique=True,
            postgresql_where=text("status IN ('waiting','playing')"),
        ),
        Index(
            "idx_rooms_last_active",
            "last_active_at",
            postgresql_where=text("status IN ('waiting','playing','finished')"),
        ),
        ForeignKeyConstraint(
            ["id", "current_round_id"],
            ["rounds.room_id", "rounds.id"],
            name="fk_room_current_round",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.uuid"))
    nickname: Mapped[str] = mapped_column(Text)
    color_tag: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[ParticipantStatus] = mapped_column(
        enum_type(ParticipantStatus, "participant_status"), server_default="active"
    )
    connection_status: Mapped[ConnectionStatus] = mapped_column(
        enum_type(ConnectionStatus, "connection_status"), server_default="connected"
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_points: Mapped[int] = mapped_column(Integer, server_default="0")
    best_round_score: Mapped[Decimal] = mapped_column(Numeric(4, 1), server_default="0")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("char_length(nickname) BETWEEN 2 AND 10", name="nickname_len"),
        CheckConstraint("color_tag BETWEEN 0 AND 11", name="color_tag"),
        CheckConstraint("total_points >= 0", name="total_points"),
        CheckConstraint("best_round_score BETWEEN 0 AND 100", name="best_round_score"),
        UniqueConstraint("room_id", "user_id", name="uq_room_user"),
        Index("idx_participants_room_status", "room_id", "status"),
    )


class Round(Base):
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(SmallInteger)
    target_emotion: Mapped[EmotionLabel] = mapped_column(enum_type(EmotionLabel, "emotion_label"))
    status: Mapped[RoundStatus] = mapped_column(
        enum_type(RoundStatus, "round_status"), server_default="revealed"
    )
    revealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewing_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("room_id", "index", name="uq_round_index"),
        UniqueConstraint("room_id", "id", name="uq_round_room_id"),
        CheckConstraint("deadline_at > revealed_at", name="deadline"),
        CheckConstraint('"index" BETWEEN 1 AND 7', name="round_index"),
    )


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    status: Mapped[SubmissionStatus] = mapped_column(
        enum_type(SubmissionStatus, "submission_status")
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    top_emotions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    rank_points: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    like_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    question_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    __table_args__ = (
        UniqueConstraint("round_id", "participant_id", name="uq_round_participant"),
        CheckConstraint(
            "target_score IS NULL OR target_score BETWEEN 0 AND 100", name="score_range"
        ),
        CheckConstraint(
            "(status = 'missed' AND received_at IS NULL) OR "
            "(status <> 'missed' AND received_at IS NOT NULL)",
            name="received",
        ),
        CheckConstraint(
            "rank_points >= 0 AND like_count >= 0 AND question_count >= 0",
            name="nonnegative_counts",
        ),
    )


class Reaction(Base):
    __tablename__ = "reactions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    actor_participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE")
    )
    target_submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE")
    )
    type: Mapped[ReactionType] = mapped_column(enum_type(ReactionType, "reaction_type"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "target_submission_id", "actor_participant_id", "type", name="uq_reaction"
        ),
    )


class RoundSkip(Base):
    __tablename__ = "round_skips"
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), primary_key=True
    )
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
