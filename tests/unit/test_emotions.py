import random

import pytest

from app.domain.enums import EmotionLabel
from app.domain.game.emotions import EMOTIONS, FULL_SET, HARD, build_sequence, describe


def test_master_table_covers_every_label_exactly_once():
    assert len(EMOTIONS) == 7
    assert set(FULL_SET) == set(EmotionLabel)
    assert len({emotion.color for emotion in EMOTIONS}) == 7
    assert HARD == {EmotionLabel.DISGUST, EmotionLabel.FEAR}


def test_emotion_view_carries_the_display_fields_without_difficulty():
    view = describe(EmotionLabel.SURPRISE).view()

    assert view == {
        "label": "surprise",
        "displayName": "놀람",
        "emoji": "😲",
        "color": "#8F66FF",
        "hint": "뒤에서 누가 부른 것처럼",
    }


@pytest.mark.parametrize("round_count", [3, 5, 7])
def test_sequence_draws_without_replacement(round_count):
    for seed in range(50):
        sequence = build_sequence(round_count, random.Random(seed))
        assert len(sequence) == round_count
        assert len(set(sequence)) == round_count  # RD-02
        assert set(sequence) <= set(FULL_SET)


@pytest.mark.parametrize("round_count", [0, 8, -1])
def test_sequence_rejects_impossible_round_counts(round_count):
    with pytest.raises(ValueError):
        build_sequence(round_count)


def test_hard_emotions_are_separated_when_a_reshuffle_can_do_it():
    for seed in range(50):
        sequence = build_sequence(7, random.Random(seed))
        adjacent = [
            (first, second)
            for first, second in zip(sequence, sequence[1:], strict=False)
            if first in HARD and second in HARD
        ]
        assert adjacent == []  # EM-03


def test_a_two_round_draw_of_both_hard_emotions_still_starts_the_game():
    class AlwaysHard(random.Random):
        def sample(self, population, k):
            return [EmotionLabel.DISGUST, EmotionLabel.FEAR]

        def shuffle(self, sequence):
            return None

    # EM-03 is P1: twenty failed retries must not block the round (§9.2).
    assert build_sequence(2, AlwaysHard()) == (EmotionLabel.DISGUST, EmotionLabel.FEAR)
