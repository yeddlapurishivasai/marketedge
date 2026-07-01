"""Per-stage progress tracking for queue jobs.

Every job type (stage-2 analysis, scanner, ingestion, stock-refresh, fundamentals) is a
sequence of ordered *stages*. ``StageTracker`` holds those stages, records each one's
status + progress, and persists the whole list as JSON in ``JobRuns.Stages`` alongside the
single overall ``Progress`` bar the UI already shows.

Two modes:

* ``write_progress=True`` (in-process jobs like stage-2 and the scanner): the tracker owns
  the overall ``Progress`` too, deriving it as a weighted average of the stages' progress so
  the overall bar and the stage breakdown always agree.
* ``write_progress=False`` (jobs whose work runs in an ingestion subprocess): the subprocess
  reports the overall ``Progress`` in-band, so the tracker leaves ``Progress`` untouched and
  only writes the ``Stages`` column (skeleton + start/complete/fail transitions), while the
  subprocess advances the active stage's own progress via
  ``ingestion.db.update_job_stage_progress``.

Status values: ``pending`` | ``running`` | ``completed`` | ``failed`` | ``skipped``.
A ``skipped`` stage is one that was intentionally not run (or a best-effort step that failed
without aborting the job); it counts as done for overall-progress purposes.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from db import update_job_status

# Default human labels for stage keys shared across jobs (subprocess ingestion steps).
STAGE_LABELS = {
    "bars": "Daily bars",
    "technical": "Technical snapshot",
    "fundamentals": "Fundamentals",
    "refresh_bars": "Refresh bars",
    "rs": "RS ratings",
    "benchmark": "Benchmark",
    "analyze": "Analyze stocks",
    "finalize": "Finalize",
    "scan": "Run scanners",
    "persist": "Persist results",
    "breakouts": "Breakouts",
    "resolve": "Resolve universe",
}

_DONE = ("completed", "skipped")


def label_for(key: str) -> str:
    return STAGE_LABELS.get(key, key.replace("_", " ").title())


class StageTracker:
    """Tracks ordered stages for a single JobRun and persists them to the DB."""

    def __init__(
        self,
        conn: Any,
        run_id: int,
        specs: Sequence[Sequence[Any]],
        *,
        write_progress: bool = True,
    ) -> None:
        """``specs`` is an ordered list of ``(key, label, weight)`` or ``(key, label)`` or
        ``(key,)`` tuples. Missing labels fall back to :func:`label_for`; missing weights
        default to 1.0."""
        self._conn = conn
        self._run_id = int(run_id)
        self._write_progress = write_progress
        self._stages: list[dict[str, Any]] = []
        for spec in specs:
            key = spec[0]
            label = spec[1] if len(spec) > 1 and spec[1] else label_for(key)
            weight = float(spec[2]) if len(spec) > 2 and spec[2] is not None else 1.0
            self._stages.append(
                {"key": key, "label": label, "status": "pending", "progress": 0,
                 "weight": weight, "detail": None}
            )
        self._by_key = {s["key"]: s for s in self._stages}

    # --- snapshot / overall -------------------------------------------------- #
    def snapshot(self) -> list[dict[str, Any]]:
        """The persisted shape: key/label/status/progress (+ detail when set); no weights."""
        out: list[dict[str, Any]] = []
        for s in self._stages:
            item = {"key": s["key"], "label": s["label"], "status": s["status"],
                    "progress": int(s["progress"])}
            if s.get("detail"):
                item["detail"] = s["detail"]
            out.append(item)
        return out

    def overall(self) -> int:
        num = 0.0
        den = 0.0
        for s in self._stages:
            w = s["weight"]
            den += w
            eff = 100 if s["status"] in _DONE else s["progress"]
            num += w * eff
        return int(round(num / den)) if den else 0

    # --- persistence --------------------------------------------------------- #
    def _flush(self, *, status: str = "running", **extra: Any) -> None:
        progress = self.overall() if self._write_progress else None
        update_job_status(
            self._conn, self._run_id, status,
            progress=progress, stages=self.snapshot(), **extra,
        )

    def publish(self, **extra: Any) -> None:
        """Write the current stage skeleton without changing any stage status."""
        self._flush(**extra)

    def _set(self, key: str, *, status: str | None = None, progress: int | None = None,
             detail: str | None = None) -> None:
        s = self._by_key[key]
        if status is not None:
            s["status"] = status
        if progress is not None:
            s["progress"] = max(0, min(100, int(progress)))
        if detail is not None:
            s["detail"] = detail

    def start(self, key: str, *, detail: str | None = None, **extra: Any) -> None:
        self._set(key, status="running", detail=detail)
        self._flush(**extra)

    def progress(self, key: str, pct: float, *, detail: str | None = None, **extra: Any) -> None:
        s = self._by_key[key]
        if s["status"] == "pending":
            s["status"] = "running"
        self._set(key, progress=int(pct), detail=detail)
        self._flush(**extra)

    def complete(self, key: str, *, detail: str | None = None, **extra: Any) -> None:
        self._set(key, status="completed", progress=100, detail=detail)
        self._flush(**extra)

    def skip(self, key: str, *, detail: str | None = None, **extra: Any) -> None:
        self._set(key, status="skipped", detail=detail)
        self._flush(**extra)

    def fail(self, key: str, *, detail: str | None = None, **extra: Any) -> None:
        self._set(key, status="failed", detail=detail)
        self._flush(**extra)

    def fail_running(self, *, detail: str | None = None) -> None:
        """Flip any still-running/pending stages to failed (used on a job crash). In-memory
        only — the caller is responsible for flushing (often via the terminal status write)."""
        for s in self._stages:
            if s["status"] in ("pending", "running"):
                s["status"] = "failed"
                if detail and not s.get("detail"):
                    s["detail"] = detail

    def finish(self, *, status: str = "completed", **extra: Any) -> None:
        """Terminal flush. On a successful finish, any still-open stages are marked completed."""
        if status == "completed":
            for s in self._stages:
                if s["status"] in ("pending", "running"):
                    s["status"] = "completed"
                    s["progress"] = 100
        self._flush(status=status, **extra)


def steps_to_specs(steps: Iterable[str], weight: float = 1.0) -> list[list[Any]]:
    """Build equal-weight specs for a list of ingestion step keys."""
    return [[step, label_for(step), weight] for step in steps]
