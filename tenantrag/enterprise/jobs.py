"""Background async ingestion and reindex job runner backed by ControlPlane persistence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any, Callable

from .control_plane import ControlPlane


class AsyncJobManager:
    """Thread-pool job queue tracking multi-stage progress in ControlPlane."""

    def __init__(self, control_plane: ControlPlane, worker_count: int = 2):
        self.control_plane = control_plane
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(worker_count)), thread_name_prefix="tr-job")

    def submit(
        self,
        *,
        tenant_id: str,
        job_type: str,
        filename: str | None = None,
        file_format: str | None = None,
        total_docs: int = 1,
        fn: Callable[[Callable[[int, int, int], None]], dict[str, Any]],
        on_done: Callable[[str, str, dict[str, Any] | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        job = self.control_plane.create_job(
            tenant_id,
            job_type,
            filename=filename,
            file_format=file_format,
            total_docs=total_docs,
        )
        job_id = job["job_id"]

        def progress(processed_docs: int, total_chunks: int, pct: int) -> None:
            self.control_plane.update_job(
                job_id,
                processed_docs=processed_docs,
                total_chunks=total_chunks,
                progress_pct=max(0, min(100, int(pct))),
            )

        def runner() -> None:
            self.control_plane.update_job(
                job_id, status="running", progress_pct=10, started_at=time.time()
            )
            try:
                result = fn(progress)
                total_chunks = int(result.get("total_chunks", result.get("chunks", 0)))
                processed = int(result.get("processed_docs", total_docs))
                self.control_plane.update_job(
                    job_id,
                    status="succeeded",
                    processed_docs=processed,
                    total_chunks=total_chunks,
                    progress_pct=100,
                    result=result,
                    finished_at=time.time(),
                )
                if on_done:
                    on_done(job_id, "succeeded", result, None)
            except Exception as exc:
                self.control_plane.update_job(
                    job_id,
                    status="failed",
                    error_message=str(exc),
                    finished_at=time.time(),
                )
                if on_done:
                    on_done(job_id, "failed", None, str(exc))

        self._executor.submit(runner)
        return job

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
