from enum import StrEnum


class EmotionLabel(StrEnum):
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISE = "surprise"
    NEUTRAL = "neutral"
    DISGUST = "disgust"
    FEAR = "fear"


class RoomStatus(StrEnum):
    WAITING = "waiting"
    PLAYING = "playing"
    FINISHED = "finished"
    CLOSED = "closed"


class ParticipantStatus(StrEnum):
    ACTIVE = "active"
    WAITING_NEXT_GAME = "waiting_next_game"
    LEFT = "left"


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class RoundStatus(StrEnum):
    REVEALED = "revealed"
    CAPTURING = "capturing"
    SCORING = "scoring"
    FINALIZED = "finalized"
    VOIDED = "voided"
    CLOSED = "closed"


class SubmissionStatus(StrEnum):
    SUBMITTED = "submitted"
    NO_FACE = "no_face"
    FAILED = "failed"
    MISSED = "missed"


class ReactionType(StrEnum):
    LIKE = "like"
    QUESTION = "question"
