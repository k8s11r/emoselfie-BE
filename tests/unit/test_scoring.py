from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import SubmissionStatus
from app.domain.scoring.service import (
    ParticipantTotals,
    RoundSubmission,
    accumulate,
    final_ranking,
    most_loved,
    score_round,
    target_score,
)

BASE_MS = 1_788_800_000_000
JOINED = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def submitted(participant_id, score, *, offset_ms=0):
    return RoundSubmission(
        participant_id=participant_id,
        status=SubmissionStatus.SUBMITTED,
        received_at_ms=BASE_MS + offset_ms,
        target_score=Decimal(str(score)),
    )


def without(participant_id, status, *, offset_ms=0):
    return RoundSubmission(
        participant_id=participant_id,
        status=status,
        received_at_ms=None if status == SubmissionStatus.MISSED else BASE_MS + offset_ms,
    )


def points(outcome):
    return {result.participant_id: result.rank_points for result in outcome.results}


def ranks(outcome):
    return {result.participant_id: result.rank for result in outcome.results}


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.9137, "91.4"),
        (0.0, "0.0"),
        (1.0, "100.0"),
        (0.8425, "84.3"),  # half-up, not Python's round() tie-to-even
        (0.00004, "0.0"),
    ],
)
def test_target_score_is_one_decimal_and_rounds_half_up(probability, expected):
    assert target_score(probability) == Decimal(expected)


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan"), float("inf")])
def test_target_score_rejects_impossible_probabilities(probability):
    with pytest.raises(ValueError):
        target_score(probability)


def test_five_scored_players_receive_100_70_50_30_30():
    outcome = score_round(
        (
            submitted(1, "91.3"),
            submitted(2, "84.2"),
            submitted(3, "70.0"),
            submitted(4, "40.5"),
            submitted(5, "12.1"),
        )
    )

    assert outcome.voided is False
    assert ranks(outcome) == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    assert points(outcome) == {1: 100, 2: 70, 3: 50, 4: 30, 5: 30}


def test_equal_scores_rank_the_earlier_submission_higher():
    outcome = score_round((submitted(1, "70.0", offset_ms=900), submitted(2, "70.0")))

    assert ranks(outcome) == {2: 1, 1: 2}
    assert points(outcome) == {2: 100, 1: 70}


def test_identical_score_and_receive_time_stays_deterministic():
    first = score_round((submitted(7, "70.0"), submitted(3, "70.0")))
    second = score_round((submitted(3, "70.0"), submitted(7, "70.0")))

    assert ranks(first) == ranks(second) == {3: 1, 7: 2}


def test_no_face_sorts_last_and_keeps_thirty_points():
    outcome = score_round(
        (
            submitted(1, "91.3"),
            without(2, SubmissionStatus.NO_FACE),
            submitted(3, "0.0", offset_ms=500),
        )
    )

    # no_face sorts as 0.0, so a 0.0 scored submission ties with it and receive time decides.
    assert ranks(outcome) == {1: 1, 2: 2, 3: 3}
    assert points(outcome) == {1: 100, 2: 30, 3: 50}
    scores = {result.participant_id: result.target_score for result in outcome.results}
    assert scores == {1: Decimal("91.3"), 2: Decimal("0.0"), 3: Decimal("0.0")}


def test_no_face_in_a_two_player_room_ranks_second_but_earns_thirty():
    outcome = score_round((submitted(1, "55.0"), without(2, SubmissionStatus.NO_FACE)))

    assert ranks(outcome) == {1: 1, 2: 2}
    assert points(outcome) == {1: 100, 2: 30}


def test_failed_leaves_the_ranking_and_takes_the_awarded_average():
    outcome = score_round(
        (
            submitted(1, "91.3"),
            submitted(2, "84.2"),
            without(3, SubmissionStatus.FAILED),
        )
    )

    assert ranks(outcome) == {1: 1, 2: 2, 3: None}
    # (100 + 70) / 2 = 85, so an engine failure costs nothing.
    assert points(outcome) == {1: 100, 2: 70, 3: 85}
    assert {r.participant_id: r.target_score for r in outcome.results}[3] is None


def test_failed_average_includes_no_face_points_and_rounds_half_up():
    outcome = score_round(
        (
            submitted(1, "91.3"),
            submitted(2, "84.2"),
            submitted(3, "70.0"),
            without(4, SubmissionStatus.NO_FACE),
            without(5, SubmissionStatus.FAILED),
        )
    )

    # (100 + 70 + 50 + 30) / 4 = 62.5 → 63. Tie-to-even rounding would report 62.
    assert points(outcome)[5] == 63


def test_every_submitter_failing_voids_the_round_without_points():
    outcome = score_round(
        (
            without(1, SubmissionStatus.FAILED),
            without(2, SubmissionStatus.FAILED),
            without(3, SubmissionStatus.MISSED),
        )
    )

    assert outcome.voided is True
    assert points(outcome) == {1: 0, 2: 0, 3: 0}
    assert ranks(outcome) == {1: None, 2: None, 3: None}


def test_a_round_without_any_submitter_is_not_voided():
    outcome = score_round(
        (without(1, SubmissionStatus.MISSED), without(2, SubmissionStatus.MISSED))
    )

    assert outcome.voided is False
    assert points(outcome) == {1: 0, 2: 0}


def test_missed_has_no_rank_and_no_points():
    outcome = score_round((submitted(1, "91.3"), without(2, SubmissionStatus.MISSED)))

    assert ranks(outcome) == {1: 1, 2: None}
    assert points(outcome) == {1: 100, 2: 0}


def test_scoring_rejects_two_submissions_from_one_participant():
    with pytest.raises(ValueError):
        score_round((submitted(1, "91.3"), submitted(1, "12.0")))


@pytest.mark.parametrize(
    "values",
    [
        {"status": SubmissionStatus.SUBMITTED, "received_at_ms": BASE_MS},
        {"status": SubmissionStatus.SUBMITTED, "target_score": Decimal("1.0")},
        {"status": SubmissionStatus.MISSED, "received_at_ms": BASE_MS},
        {"status": SubmissionStatus.NO_FACE, "target_score": Decimal("1.0")},
        {
            "status": SubmissionStatus.SUBMITTED,
            "received_at_ms": BASE_MS,
            "target_score": Decimal("100.1"),
        },
    ],
)
def test_invalid_round_submission(values):
    with pytest.raises(ValueError):
        RoundSubmission(participant_id=1, **values)


def test_accumulation_adds_points_and_keeps_the_best_score():
    totals = ParticipantTotals(participant_id=1, joined_at=JOINED)
    outcome = score_round((submitted(1, "91.3"), submitted(2, "40.0")))

    totals = accumulate(totals, outcome.results[0])
    assert (totals.total_points, totals.best_round_score) == (100, Decimal("91.3"))

    lower = score_round((submitted(2, "80.0"), submitted(1, "12.5")))
    totals = accumulate(totals, next(r for r in lower.results if r.participant_id == 1))
    assert (totals.total_points, totals.best_round_score) == (170, Decimal("91.3"))

    missed = score_round((submitted(2, "80.0"), without(1, SubmissionStatus.MISSED)))
    totals = accumulate(totals, next(r for r in missed.results if r.participant_id == 1))
    assert (totals.total_points, totals.best_round_score) == (170, Decimal("91.3"))


def test_accumulation_rejects_another_participants_result():
    outcome = score_round((submitted(1, "91.3"), submitted(2, "40.0")))
    with pytest.raises(ValueError):
        accumulate(ParticipantTotals(participant_id=2, joined_at=JOINED), outcome.results[0])


def test_final_ranking_breaks_ties_by_best_score_then_join_order():
    totals = (
        ParticipantTotals(3, JOINED + timedelta(seconds=30), 200, Decimal("70.0")),
        ParticipantTotals(1, JOINED, 200, Decimal("70.0")),
        ParticipantTotals(2, JOINED + timedelta(seconds=10), 200, Decimal("91.3")),
        ParticipantTotals(4, JOINED - timedelta(seconds=10), 130, Decimal("99.9")),
    )

    assert [(entry.rank, entry.participant_id) for entry in final_ranking(totals)] == [
        (1, 2),
        (2, 1),
        (3, 3),
        (4, 4),
    ]


def test_final_ranking_rejects_duplicate_participants():
    with pytest.raises(ValueError):
        final_ranking(
            (ParticipantTotals(1, JOINED, 10), ParticipantTotals(1, JOINED, 20)),
        )


def test_most_loved_prefers_fewer_questions_then_join_order():
    totals = (
        ParticipantTotals(1, JOINED, like_count=9, question_count=3),
        ParticipantTotals(2, JOINED + timedelta(seconds=10), like_count=9, question_count=1),
        ParticipantTotals(3, JOINED + timedelta(seconds=20), like_count=4),
    )

    award = most_loved(totals)
    assert award is not None
    assert (award.participant_id, award.like_count) == (2, 9)


def test_most_loved_uses_join_order_when_likes_and_questions_are_equal():
    totals = (
        ParticipantTotals(2, JOINED + timedelta(seconds=10), like_count=5, question_count=2),
        ParticipantTotals(1, JOINED, like_count=5, question_count=2),
    )

    award = most_loved(totals)
    assert award is not None and award.participant_id == 1


def test_a_game_without_likes_has_no_award():
    totals = (
        ParticipantTotals(1, JOINED, question_count=4),
        ParticipantTotals(2, JOINED + timedelta(seconds=10)),
    )

    assert most_loved(totals) is None


def test_reactions_never_change_scores_or_ranking():
    submissions = (submitted(1, "40.0"), submitted(2, "91.3"), without(3, SubmissionStatus.FAILED))
    outcome = score_round(submissions)

    quiet = (
        ParticipantTotals(1, JOINED, 70, Decimal("40.0")),
        ParticipantTotals(2, JOINED + timedelta(seconds=10), 100, Decimal("91.3")),
    )
    loved = (
        ParticipantTotals(1, JOINED, 70, Decimal("40.0"), like_count=12, question_count=9),
        ParticipantTotals(2, JOINED + timedelta(seconds=10), 100, Decimal("91.3")),
    )

    assert score_round(submissions) == outcome
    assert final_ranking(loved) == final_ranking(quiet)
    assert most_loved(loved) is not None and most_loved(quiet) is None
