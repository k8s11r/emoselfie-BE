import asyncio
import math
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field

from app.inference.protocol import EmotionResult, InferenceError


class InferenceTimeout(InferenceError):
    pass


class InferenceOverloaded(InferenceError):
    pass


class InferenceUnavailable(InferenceError):
    pass


class CircuitOpen(InferenceUnavailable):
    pass


class CircuitBreaker:
    """One process's engine health; monotonic time is independent of game deadlines."""

    def __init__(self, cooldown_sec: float = 60) -> None:
        self.history: deque[bool] = deque(maxlen=20)
        self.open_until = 0.0
        self.cooldown_sec = cooldown_sec

    def is_open(self, now: float) -> bool:
        return now < self.open_until

    def record(self, success: bool, now: float) -> None:
        if self.is_open(now):
            return
        if self.open_until:
            self.history.clear()
            self.open_until = 0.0
        self.history.append(success)
        if len(self.history) == 20 and self.history.count(False) >= 16:
            self.open_until = now + self.cooldown_sec


@dataclass
class _Job:
    image: bytes | None = field(repr=False)
    result: asyncio.Future[EmotionResult] = field(repr=False)
    started: bool = False


class InferenceRunner:
    """Bounded queue + fixed executor. Client timeout never releases an active worker slot."""

    def __init__(
        self,
        classify: Callable[[bytes], EmotionResult],
        *,
        concurrency: int = 2,
        queue_capacity: int = 24,
        timeout_sec: float = 5.0,
        max_bytes: int = 2097152,
        warmup: Callable[[], None] | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        if (
            concurrency < 1
            or queue_capacity < 1
            or not math.isfinite(timeout_sec)
            or timeout_sec <= 0
            or max_bytes < 1
        ):
            raise ValueError("Runner limits must be positive")
        self._classify = classify
        self._warmup = warmup
        self._close_backend = close
        self._queue: asyncio.Queue[_Job | None] = asyncio.Queue(maxsize=queue_capacity)
        self._slots = asyncio.Semaphore(concurrency)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="inference")
        self._concurrency = concurrency
        self._timeout_sec = timeout_sec
        self._max_bytes = max_bytes
        self._workers: list[asyncio.Task[None]] = []
        self._closing: asyncio.Task[None] | None = None
        self._warmup_task: asyncio.Future[None] | None = None
        self._starting = False
        self._ready = False
        self._active = 0
        self.breaker = CircuitBreaker()

    @property
    def ready(self) -> bool:
        return self._ready and self._closing is None

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._closing is not None:
            raise InferenceUnavailable("Inference runner is closed")
        if self._ready:
            return
        if self._starting:
            raise InferenceUnavailable("Inference runner is already starting")
        self._starting = True
        try:
            if self._warmup is not None:
                self._warmup_task = asyncio.get_running_loop().run_in_executor(
                    self._executor, self._warmup
                )
                await asyncio.shield(self._warmup_task)
            if self._closing is not None:
                raise InferenceUnavailable("Inference runner is closing")
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self._concurrency)]
            self._ready = True
        except BaseException:
            await self.aclose()
            raise
        finally:
            self._starting = False

    async def classify(self, image: bytes) -> EmotionResult:
        if not self.ready:
            raise InferenceUnavailable("Inference runner is not ready")
        if len(image) > self._max_bytes:
            raise InferenceOverloaded("Inference input exceeds the byte limit")
        loop = asyncio.get_running_loop()
        if self.breaker.is_open(loop.time()):
            raise CircuitOpen("Inference circuit is open")
        job = _Job(image, loop.create_future())
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise InferenceOverloaded("Inference queue is full") from None
        del image
        try:
            result = await asyncio.wait_for(asyncio.shield(job.result), self._timeout_sec)
        except TimeoutError:
            job.result.cancel()
            if job.started:
                self.breaker.record(False, loop.time())
            raise InferenceTimeout("Inference deadline exceeded") from None
        except asyncio.CancelledError:
            job.result.cancel()
            raise
        except InferenceUnavailable:
            raise
        except InferenceError:
            self.breaker.record(False, loop.time())
            raise
        else:
            self.breaker.record(True, loop.time())
            return result
        finally:
            # If queued, release the bytes now; if running, only the executor retains them.
            job.image = None

    def _invoke(self, image: bytes) -> EmotionResult | None:
        try:
            result = self._classify(image)
            # Detach and revalidate, including nested probabilities mutated by an adapter.
            return EmotionResult.model_validate(result.model_dump())
        except Exception:
            # Do not retain a backend traceback containing an image/tensor in a Future.
            return None

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                if job.result.done() or job.image is None:
                    continue
                if self.breaker.is_open(loop.time()):
                    job.image = None
                    job.result.set_exception(CircuitOpen("Inference circuit is open"))
                    continue
                async with self._slots:
                    job.started = True
                    self._active += 1
                    operation = loop.run_in_executor(self._executor, self._invoke, job.image)
                    job.image = None
                    try:
                        result = await operation
                    finally:
                        self._active -= 1
                    if not job.result.done():
                        if result is None:
                            job.result.set_exception(InferenceError("Inference engine failed"))
                        else:
                            job.result.set_result(result)
            finally:
                self._queue.task_done()

    async def aclose(self) -> None:
        if self._closing is None:
            self._ready = False
            self._closing = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._closing)

    async def _shutdown(self) -> None:
        if self._warmup_task is not None:
            with suppress(Exception):
                await asyncio.shield(self._warmup_task)
        while not self._queue.empty():
            job = self._queue.get_nowait()
            if job is not None:
                job.image = None
                if not job.result.done():
                    job.result.set_exception(InferenceUnavailable("Inference runner is closing"))
            self._queue.task_done()
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers)
        try:
            if self._close_backend is not None:
                await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._close_backend
                )
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
