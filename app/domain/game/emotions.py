"""§9 emotion master table and sequence generation. The server is the single source (EM-02)."""

import random
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.enums import EmotionLabel


@dataclass(frozen=True, slots=True)
class Emotion:
    label: EmotionLabel
    display_name: str
    emoji: str
    color: str
    hint: str
    difficulty: Literal["easy", "hard"]

    def view(self) -> dict[str, Any]:
        return {
            "label": self.label.value,
            "displayName": self.display_name,
            "emoji": self.emoji,
            "color": self.color,
            "hint": self.hint,
        }


EMOTIONS = (
    Emotion(EmotionLabel.HAPPY, "기쁨", "😆", "#FFD72F", "좋은 소식을 방금 들은 것처럼", "easy"),
    Emotion(EmotionLabel.SAD, "슬픔", "😢", "#5AC8FF", "아끼던 걸 잃어버린 것처럼", "easy"),
    Emotion(EmotionLabel.ANGRY, "분노", "😠", "#FF5A47", "새치기를 당한 것처럼", "easy"),
    Emotion(EmotionLabel.SURPRISE, "놀람", "😲", "#8F66FF", "뒤에서 누가 부른 것처럼", "easy"),
    Emotion(EmotionLabel.NEUTRAL, "시크", "😐", "#80EFD6", "아무 일도 없다는 듯이", "easy"),
    Emotion(EmotionLabel.DISGUST, "우웩", "🤢", "#B8F16A", "상한 우유를 마신 것처럼", "hard"),
    Emotion(
        EmotionLabel.FEAR, "무서움", "😨", "#FF3D86", "어두운 복도에서 소리가 난 것처럼", "hard"
    ),
)
BY_LABEL = {emotion.label: emotion for emotion in EMOTIONS}
FULL_SET = tuple(emotion.label for emotion in EMOTIONS)
HARD = frozenset(emotion.label for emotion in EMOTIONS if emotion.difficulty == "hard")
SHUFFLE_ATTEMPTS = 20


def describe(label: EmotionLabel) -> Emotion:
    return BY_LABEL[label]


def _adjacent_hard(sequence: list[EmotionLabel]) -> bool:
    return any(
        first in HARD and second in HARD
        for first, second in zip(sequence, sequence[1:], strict=False)
    )


def build_sequence(round_count: int, rng: random.Random | None = None) -> tuple[EmotionLabel, ...]:
    """RD-02 draws without replacement; EM-03 retries to separate the two hard emotions.

    EM-03 is P1, so an unlucky draw is used as it is rather than blocking the game.
    """
    if not 1 <= round_count <= len(FULL_SET):
        raise ValueError("A game runs between one and seven rounds")
    rng = rng or random.SystemRandom()
    sequence = rng.sample(FULL_SET, round_count)
    for _ in range(SHUFFLE_ATTEMPTS):
        if not _adjacent_hard(sequence):
            break
        rng.shuffle(sequence)
    return tuple(sequence)
