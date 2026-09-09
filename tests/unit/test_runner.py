import asyncio
import threading

import pytest

from app.inference.protocol import EmotionResult, InferenceError
from app.inference.runner import (
    CircuitBreaker,
    CircuitOpen,
    InferenceOverloaded,
    InferenceRunner,
    InferenceTimeout,
    InferenceUnavailable,
)


class BlockingBackend:
    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0
        self.closed = False

    def classify(self, data):
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("Test failed to release backend")
        return EmotionResult(face_detected=False)

    def close(self):
        self.closed = True


async def started(backend):
    assert await asyncio.to_thread(backend.started.wait, 2)


async def test_timeout_keeps_slot_until_native_operation_finishes():
    backend = BlockingBackend()
    runner = InferenceRunner(backend.classify, concurrency=1, timeout_sec=0.2, close=backend.close)
    await runner.start()
    try:
        first = asyncio.create_task(runner.classify(b"first"))
        await started(backend)
        with pytest.raises(InferenceTimeout):
            await first
        assert runner.active_count == 1
        second = asyncio.create_task(runner.classify(b"second"))
        await asyncio.sleep(0.02)
        assert backend.calls == 1
        backend.release.set()
        assert not (await second).face_detected
        assert backend.calls == 2
        assert list(runner.breaker.history) == [False, True]
    finally:
        backend.release.set()
        await runner.aclose()
    assert backend.closed


async def test_bounded_queue_and_cancellation_do_not_run_abandoned_image():
    backend = BlockingBackend()
    runner = InferenceRunner(backend.classify, concurrency=1, queue_capacity=1, timeout_sec=2)
    await runner.start()
    try:
        first = asyncio.create_task(runner.classify(b"first"))
        await started(backend)
        second = asyncio.create_task(runner.classify(b"abandoned"))
        await asyncio.sleep(0)
        with pytest.raises(InferenceOverloaded):
            await runner.classify(b"excess")
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert runner.active_count == 1
        backend.release.set()
        await first
    finally:
        backend.release.set()
        await runner.aclose()
    assert backend.calls == 1


async def test_shutdown_rejects_queued_work_and_drains_active_work_before_close():
    backend = BlockingBackend()
    runner = InferenceRunner(backend.classify, concurrency=1, timeout_sec=2, close=backend.close)
    await runner.start()
    first = asyncio.create_task(runner.classify(b"active"))
    await started(backend)
    second = asyncio.create_task(runner.classify(b"queued"))
    await asyncio.sleep(0)
    closing = asyncio.create_task(runner.aclose())
    try:
        with pytest.raises(InferenceUnavailable):
            await second
        assert not backend.closed
        assert not runner.ready
    finally:
        backend.release.set()
        await first
        await closing
    assert backend.closed
    assert backend.calls == 1
    await runner.aclose()
    with pytest.raises(InferenceUnavailable):
        await runner.classify(b"late")


async def test_cancelled_active_request_does_not_cancel_native_work_or_poison_circuit():
    backend = BlockingBackend()
    runner = InferenceRunner(backend.classify, concurrency=1)
    await runner.start()
    task = asyncio.create_task(runner.classify(b"active"))
    await started(backend)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runner.active_count == 1
        assert not runner.breaker.history
    finally:
        backend.release.set()
        await runner.aclose()


async def test_engine_failures_are_sanitized_and_open_circuit_at_80_percent():
    calls = 0

    def classify(image):
        nonlocal calls
        calls += 1
        if calls <= 16:
            raise RuntimeError("private-image-buffer-and-token")
        return EmotionResult(face_detected=False)

    runner = InferenceRunner(classify)
    await runner.start()
    try:
        for _ in range(16):
            with pytest.raises(InferenceError, match="Inference engine failed") as error:
                await runner.classify(b"private-input")
            assert "private" not in str(error.value)
            assert error.value.__cause__ is None
        for _ in range(4):
            await runner.classify(b"no-face")
        with pytest.raises(CircuitOpen):
            await runner.classify(b"not-run")
        assert calls == 20
    finally:
        await runner.aclose()


def test_circuit_cooldown_and_successful_recovery():
    breaker = CircuitBreaker()
    for _ in range(16):
        breaker.record(False, 100)
    for _ in range(4):
        breaker.record(True, 100)
    assert breaker.is_open(159.999)
    assert not breaker.is_open(160)
    breaker.record(True, 160)
    assert list(breaker.history) == [True]


async def test_warmup_failure_releases_backend_and_never_becomes_ready():
    closed = []

    def warmup():
        raise RuntimeError("warmup failed")

    runner = InferenceRunner(
        lambda _: EmotionResult(face_detected=False),
        warmup=warmup,
        close=lambda: closed.append(True),
    )
    with pytest.raises(RuntimeError, match="warmup failed"):
        await runner.start()
    assert not runner.ready
    assert closed == [True]


async def test_cancelled_warmup_finishes_before_backend_close():
    backend = BlockingBackend()
    runner = InferenceRunner(
        backend.classify, warmup=lambda: backend.classify(b"warmup"), close=backend.close
    )
    task = asyncio.create_task(runner.start())
    await started(backend)
    try:
        task.cancel()
        await asyncio.sleep(0)
        assert not backend.closed
    finally:
        backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert backend.closed and not runner.ready
