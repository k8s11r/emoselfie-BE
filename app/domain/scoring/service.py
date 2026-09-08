"""Pure round and game scoring. No I/O, no clock, no database: §11 and §18.1."""

import math
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import SubmissionStatus

RANK_POINTS = {1: 100, 2: 70, 3: 50}
DEFAULT_POINTS = 30
NO_FACE_SCORE = Decimal("0.0")
SCORE_STEP = Decimal("0.1")
# Submitters. A missed participant never entered the round's ranking population.
SUBMITTED_STATUSES = (
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.NO_FACE,
    SubmissionStatus.FAILED,
)
RANKED_STATUSES = (SubmissionStatus.SUBMITTED, SubmissionStatus.NO_FACE)


def round_half_up(value: Decimal, step: Decimal) -> Decimal:
    """Product rounding is half-up. Python's round() would break ties to even instead."""
    return value.quantize(step, rounding=ROUND_HALF_UP)


def target_score(probability: float) -> Decimal:
    """SC-01: the target emotion's probability as 0.0~100.0 with one decimal."""
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Probability must be a finite value between zero and one")
    return round_half_up(Decimal(str(probability)) * 100, SCORE_STEP)


@dataclass(frozen=True, slots=True)
class RoundSubmission:
    """One participant's outcome for a round, already classified by the inference boundary."""

    participant_id: int
    status: SubmissionStatus
    received_at_ms: int | None = None
    target_score: Decimal | None = None

    def __post_init__(self) -> None:
        if (self.status == SubmissionStatus.MISSED) is (self.received_at_ms is not None):
            raise ValueError("Only a missed submission has no server receive time")
        if (self.status == SubmissionStatus.SUBMITTED) is (self.target_score is None):
            raise ValueError("Only a scored submission carries a target score")
        if self.target_score is not None and not 0 <= self.target_score <= 100:
            raise ValueError("Target score must fall between zero and one hundred")


@dataclass(frozen=True, slots=True)
class ScoredSubmission:
    participant_id: int
    status: SubmissionStatus
    rank: int | None
    rank_points: int
    target_score: Decimal | None


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    voided: bool
    results: tuple[ScoredSubmission, ...]


def _order_key(submission: RoundSubmission) -> tuple[Decimal, int, int]:
    score = NO_FACE_SCORE if submission.target_score is None else submission.target_score
    # SC-03 breaks ties by server receive time; the id keeps identical times deterministic.
    return (-score, submission.received_at_ms or 0, submission.participant_id)


def score_round(submissions: tuple[RoundSubmission, ...]) -> RoundOutcome:
    """§11.2·11.3 ranking and points. Reactions are never an input here (SC-06, RX-01)."""
    if len({submission.participant_id for submission in submissions}) != len(submissions):
        raise ValueError("Each participant submits at most once per round")
    submitters = [s for s in submissions if s.status in SUBMITTED_STATUSES]
    voided = bool(submitters) and all(s.status == SubmissionStatus.FAILED for s in submitters)
    if voided:
        # D-5: a voided round awards nothing, so no ranking is computed at all.
        return RoundOutcome(
            voided=True,
            results=tuple(
                ScoredSubmission(s.participant_id, s.status, None, 0, _final_score(s))
                for s in sorted(submissions, key=lambda s: s.participant_id)
            ),
        )

    ranked = sorted((s for s in submissions if s.status in RANKED_STATUSES), key=_order_key)
    results = [
        ScoredSubmission(
            participant_id=submission.participant_id,
            status=submission.status,
            rank=rank,
            # D-2: no_face keeps 30 even when the rank alone would pay more.
            rank_points=(
                DEFAULT_POINTS
                if submission.status == SubmissionStatus.NO_FACE
                else RANK_POINTS.get(rank, DEFAULT_POINTS)
            ),
            target_score=_final_score(submission),
        )
        for rank, submission in enumerate(ranked, start=1)
    ]
    # D-2: engine failures are not penalised; §11.3 averages the awarded points, no_face included.
    # An empty population only happens when nobody submitted, and then no failure needs a value.
    compensation = (
        int(
            round_half_up(
                Decimal(sum(result.rank_points for result in results)) / len(results), Decimal(1)
            )
        )
        if results
        else DEFAULT_POINTS
    )
    results.extend(
        ScoredSubmission(
            participant_id=submission.participant_id,
            status=submission.status,
            rank=None,
            rank_points=(
                compensation if submission.status == SubmissionStatus.FAILED else 0  # RD-06
            ),
            target_score=None,
        )
        for submission in sorted(
            (s for s in submissions if s.status not in RANKED_STATUSES),
            key=lambda s: s.participant_id,
        )
    )
    return RoundOutcome(voided=False, results=tuple(results))


def _final_score(submission: RoundSubmission) -> Decimal | None:
    if submission.status == SubmissionStatus.NO_FACE:
        return NO_FACE_SCORE  # §11.2: reported as 0.0, not as an unknown score.
    return submission.target_score


@dataclass(frozen=True, slots=True)
class ParticipantTotals:
    """Accumulated game state. joined_at keeps a complete tie deterministic (§11.4)."""

    participant_id: int
    joined_at: datetime
    total_points: int = 0
    best_round_score: Decimal = NO_FACE_SCORE
    like_count: int = 0
    question_count: int = 0

    def __post_init__(self) -> None:
        counts = (self.total_points, self.like_count, self.question_count)
        if any(count < 0 for count in counts) or not 0 <= self.best_round_score <= 100:
            raise ValueError("Accumulated totals cannot be negative or exceed the score range")


def accumulate(totals: ParticipantTotals, result: ScoredSubmission) -> ParticipantTotals:
    """§11.4. Callers apply this once per finalized round; voided rounds award zero."""
    if totals.participant_id != result.participant_id:
        raise ValueError("Accumulated totals belong to another participant")
    return replace(
        totals,
        total_points=totals.total_points + result.rank_points,
        best_round_score=max(totals.best_round_score, result.target_score or NO_FACE_SCORE),
    )


@dataclass(frozen=True, slots=True)
class FinalRank:
    rank: int
    participant_id: int
    total_points: int


@dataclass(frozen=True, slots=True)
class MostLoved:
    participant_id: int
    like_count: int


def final_ranking(totals: tuple[ParticipantTotals, ...]) -> tuple[FinalRank, ...]:
    """SC-06: -totalPoints, -bestRoundScore, joinedAt. Ties never share a rank number."""
    if len({entry.participant_id for entry in totals}) != len(totals):
        raise ValueError("Each participant appears once in the final ranking")
    ordered = sorted(
        totals,
        key=lambda entry: (
            -entry.total_points,
            -entry.best_round_score,
            entry.joined_at,
            entry.participant_id,
        ),
    )
    return tuple(
        FinalRank(rank, entry.participant_id, entry.total_points)
        for rank, entry in enumerate(ordered, start=1)
    )


def most_loved(totals: tuple[ParticipantTotals, ...]) -> MostLoved | None:
    """RX-11: most likes, then fewer questions, then earlier joined_at. No likes, no award."""
    candidates = [entry for entry in totals if entry.like_count > 0]
    if not candidates:
        return None
    winner = min(
        candidates,
        key=lambda entry: (
            -entry.like_count,
            entry.question_count,
            entry.joined_at,
            entry.participant_id,
        ),
    )
    return MostLoved(winner.participant_id, winner.like_count)
