"""Lightweight in-process async job queue with Mongo-backed progress.

Why a custom queue instead of Celery/RQ/Arq?
  - The analysis pipeline already lives inside this FastAPI process and reuses
    its httpx client + Motor connection. A side-car worker would force us to
    duplicate that state without adding any throughput (we're CPU-light and
    rate-limited by upstream API quotas, not by our worker count).
  - Persistence: jobs are checkpointed in Mongo so a server restart leaves a
    visible "failed/expired" state instead of an orphan in-memory task.
  - Polling: frontend polls `/api/analysis/jobs/{id}` every ~2s — no need for
    WebSockets behind the K8s ingress.

Lifecycle stages (kept simple):
    queued → ingesting → enriching → analyzing → done | failed

API:
    create_job(db, user_id, kind, params)   → job_id
    update_progress(db, job_id, stage, pct, message=None)
    finish(db, job_id, result)              → done
    fail(db, job_id, error)                 → failed
    enqueue(db, coro_factory, user_id, kind, params) → starts asyncio task
    get(db, job_id, user_id=None)           → job doc
    list_active(db, user_id)                → recent active jobs
    cleanup_stale(db, max_age_minutes=15)   → mark forgotten jobs as failed
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger("job_queue")

STAGES = ("queued", "ingesting", "enriching", "analyzing", "done", "failed")
TERMINAL = {"done", "failed"}

# Soft cap: ANALYZE jobs typically take 60-120s. Anything older than this with
# no progress update is considered abandoned (e.g. process restart mid-run).
STALE_THRESHOLD_MINUTES = 15


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_job(db, user_id: str, kind: str, params: dict) -> str:
    """Persist a queued job and return its id."""
    job_id = uuid.uuid4().hex[:16]
    doc = {
        "id": job_id,
        "user_id": user_id,
        "kind": kind,
        "params": params,
        "stage": "queued",
        "progress": 0,
        "message": "Pending start",
        "created_at": _now(),
        "updated_at": _now(),
        "result": None,
        "error": None,
    }
    await db.analysis_jobs.insert_one(doc)
    return job_id


async def update_progress(db, job_id: str, stage: str, progress: int, message: str | None = None) -> None:
    """Push a new progress checkpoint. Idempotent; safe to call from any task."""
    if stage not in STAGES:
        stage = "ingesting"
    progress = max(0, min(int(progress), 100))
    patch: dict = {"stage": stage, "progress": progress, "updated_at": _now()}
    if message is not None:
        patch["message"] = message
    await db.analysis_jobs.update_one({"id": job_id}, {"$set": patch})


async def finish(db, job_id: str, result: dict) -> None:
    await db.analysis_jobs.update_one(
        {"id": job_id},
        {"$set": {
            "stage": "done",
            "progress": 100,
            "message": "Completed",
            "result": result,
            "updated_at": _now(),
            "finished_at": _now(),
        }},
    )


async def fail(db, job_id: str, error: str) -> None:
    await db.analysis_jobs.update_one(
        {"id": job_id},
        {"$set": {
            "stage": "failed",
            "progress": 100,
            "message": error[:300],
            "error": error,
            "updated_at": _now(),
            "finished_at": _now(),
        }},
    )


async def get(db, job_id: str, user_id: str | None = None) -> dict | None:
    """Fetch a job. If `user_id` is provided, enforces ownership."""
    q = {"id": job_id}
    if user_id is not None:
        q["user_id"] = user_id
    doc = await db.analysis_jobs.find_one(q)
    if doc:
        doc.pop("_id", None)
    return doc


async def list_active(db, user_id: str, limit: int = 5) -> list[dict]:
    """Most recent jobs for a user — caller may filter to non-terminal client-side."""
    cursor = db.analysis_jobs.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    for d in items:
        d.pop("_id", None)
    return items


async def cleanup_stale(db, max_age_minutes: int = STALE_THRESHOLD_MINUTES) -> int:
    """Mark non-terminal jobs older than the threshold as failed.

    Called opportunistically on each list_active() / dashboard load so the UI
    never displays a "stuck" running job after a process restart.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    res = await db.analysis_jobs.update_many(
        {"stage": {"$nin": list(TERMINAL)}, "updated_at": {"$lt": cutoff}},
        {"$set": {
            "stage": "failed",
            "progress": 100,
            "message": "Timeout / stale (server may have restarted)",
            "error": "stale",
            "updated_at": _now(),
            "finished_at": _now(),
        }},
    )
    return res.modified_count or 0


def enqueue(
    db,
    coro_factory: Callable[[str], Awaitable[Any]],
    user_id: str,
    kind: str,
    params: dict,
) -> str:
    """Create the job row and schedule its coroutine on the running loop.

    `coro_factory` is a callable that receives the job_id and returns the
    awaitable to be executed. It MUST handle its own progress/finish/fail calls
    OR raise; if it raises, the queue auto-records the failure.

    Returns the job_id immediately (synchronous control flow).
    """
    async def _wrapper() -> None:
        job_id = await create_job(db, user_id, kind, params)
        # Stash the job_id on the closure so the caller (which already got the
        # id below via the await chain) doesn't need to thread it back.
        _wrapper.__job_id__ = job_id  # type: ignore[attr-defined]
        try:
            await coro_factory(job_id)
        except Exception as exc:
            log.exception("job %s failed: %s", job_id, exc)
            try:
                await fail(db, job_id, str(exc))
            except Exception:
                pass

    # Because create_job is awaitable, we can't call it synchronously here.
    # Instead: create the job row in a side coroutine and return its id
    # synchronously by awaiting it via a small bridge. The cleanest path
    # is to require callers to await `enqueue_async` directly.
    raise NotImplementedError("Use enqueue_async() from an async context.")


async def enqueue_async(
    db,
    coro_factory: Callable[[str], Awaitable[Any]],
    user_id: str,
    kind: str,
    params: dict,
) -> str:
    """Awaitable variant: create job row, schedule task, return job_id."""
    job_id = await create_job(db, user_id, kind, params)

    async def _runner() -> None:
        try:
            await coro_factory(job_id)
        except Exception as exc:
            log.exception("job %s failed: %s", job_id, exc)
            try:
                await fail(db, job_id, str(exc))
            except Exception:
                pass

    # Schedule on the running event loop without awaiting completion.
    # asyncio.create_task keeps a strong ref via the default task set up to
    # Python 3.11; we explicitly hold a reference on a module-level set to
    # avoid "Task was destroyed but it is pending!" warnings.
    task = asyncio.create_task(_runner(), name=f"job-{kind}-{job_id}")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return job_id


# Module-level reference holder — prevents GC of in-flight background tasks.
_BACKGROUND_TASKS: set[asyncio.Task] = set()
