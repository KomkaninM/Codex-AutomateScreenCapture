"""Cancellable in-memory scheduling with a ten-second session precheck."""

from __future__ import annotations

import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


class ScheduleCancelled(RuntimeError):
    pass


def parse_interval(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h)", value.lower())
    if not match:
        raise ValueError("Interval must be a duration such as 30m or 1h.")
    seconds = float(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    if not math.isfinite(seconds) or not 10 <= seconds <= 31_536_000:
        raise ValueError("Interval must be between 10 seconds and 365 days.")
    return seconds


@dataclass
class Job:
    id: str
    interval: float | None
    due: datetime
    capture: object
    precheck: object
    cancel: threading.Event = field(default_factory=threading.Event)
    prepared: bool = False
    precheck_future: object = None
    in_flight: bool = False


class Scheduler:
    def __init__(self, dispatch, *, clock=None, max_jobs=32):
        self.dispatch = dispatch
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.max_jobs = max_jobs
        self.jobs = {}
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.thread = None
        self.generation = 0

    def snapshot_generation(self):
        with self.lock:
            return self.generation

    def add(self, interval, due, capture, precheck, *, expected_generation=None):
        if due.tzinfo is None:
            raise ValueError("Schedule timestamps must be timezone-aware.")
        if interval is not None and (not math.isfinite(interval) or interval < 10):
            raise ValueError("Interval must be at least ten seconds.")
        with self.lock:
            if (
                expected_generation is not None
                and expected_generation != self.generation
            ):
                raise ScheduleCancelled("Schedule request superseded by stop-capture.")
            if len(self.jobs) >= self.max_jobs:
                raise ValueError("Maximum active schedules reached.")
            job = Job(uuid.uuid4().hex[:12], interval, due, capture, precheck)
            self.jobs[job.id] = job
            return job.id

    def stop_all(self):
        with self.lock:
            self.generation += 1
            count = len(self.jobs)
            for job in self.jobs.values():
                job.cancel.set()
            self.jobs.clear()
            return count

    def tick(self, now=None):
        now = now or self.clock()
        with self.lock:
            for job in list(self.jobs.values()):
                if job.cancel.is_set() or job.in_flight:
                    continue
                if now >= job.due:
                    # A capture may not overtake its queued/running precheck and logout
                    # before that precheck subsequently restores the session.
                    if (
                        job.precheck_future is not None
                        and not job.precheck_future.done()
                    ):
                        continue
                    job.in_flight = True
                    try:
                        future = self.dispatch(lambda j=job: self._execute(j, False))
                    except RuntimeError:
                        job.in_flight = False
                        continue
                    future.add_done_callback(lambda f, j=job: self._finished(j, f))
                elif not job.prepared and now >= job.due - timedelta(seconds=10):
                    try:
                        future = self.dispatch(lambda j=job: self._execute(j, True))
                    except RuntimeError:
                        continue
                    job.prepared = True
                    job.precheck_future = future
                    future.add_done_callback(self._precheck_finished)

    @staticmethod
    def _execute(job, precheck):
        if not job.cancel.is_set():
            return (job.precheck if precheck else job.capture)(job.cancel)

    @staticmethod
    def _precheck_finished(future):
        if not future.cancelled() and future.exception():
            log.error(
                "Scheduled session precheck failed; capture will revalidate: %s",
                future.exception(),
            )

    def _finished(self, job, future):
        if not future.cancelled() and future.exception():
            log.error("Scheduled capture failed: %s", future.exception())
        with self.lock:
            job.in_flight = False
            if job.id not in self.jobs:
                return
            if job.interval is None:
                self.jobs.pop(job.id, None)
            else:
                # Anchor to the requested timeline and skip missed ticks, never replay a backlog.
                now = self.clock()
                missed = max(
                    1, math.floor((now - job.due).total_seconds() / job.interval) + 1
                )
                job.due += timedelta(seconds=missed * job.interval)
                job.prepared = False
                job.precheck_future = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.shutdown.clear()
        self.thread = threading.Thread(
            target=self._loop, name="bms-scheduler", daemon=True
        )
        self.thread.start()

    def _loop(self):
        while not self.shutdown.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Scheduler tick failed.")
            self.shutdown.wait(0.2)

    def close(self):
        self.shutdown.set()
        self.stop_all()
        if self.thread:
            self.thread.join(timeout=2)
