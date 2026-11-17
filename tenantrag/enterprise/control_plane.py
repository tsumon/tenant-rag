"""Enterprise Control Plane: multi-tenant IAM, scoped API keys, jobs, backups, and tamper-evident audit chain."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any

from .errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError

VALID_SCOPES = ("read", "write", "chat", "eval", "tenant_admin")
VALID_STATUSES = ("active", "suspended")
TENANT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
GENESIS_HASH = "0" * 64


def validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant identifier strictly to prevent filesystem path traversal."""
    if not isinstance(tenant_id, str) or not TENANT_ID_RE.match(tenant_id) or tenant_id in (".", ".."):
        raise ValueError("tenant_id must match ^[A-Za-z0-9._-]{1,64}$ and cannot be '.' or '..'")
    return tenant_id


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _compute_audit_hash(
    prev_hash: str,
    ts_str: str,
    actor_type: str,
    actor_id: str,
    tenant_id: str | None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    status: str,
    detail_json: str,
) -> str:
    payload = "|".join([
        prev_hash,
        ts_str,
        actor_type,
        actor_id,
        tenant_id or "",
        action,
        resource_type or "",
        resource_id or "",
        status,
        detail_json,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id       TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    isolation_mode  TEXT NOT NULL DEFAULT 'physical_sqlite',
    quota_docs      INTEGER NOT NULL DEFAULT 10000,
    quota_chunks    INTEGER NOT NULL DEFAULT 100000,
    rate_limit_rpm  INTEGER NOT NULL DEFAULT 120,
    description     TEXT NOT NULL DEFAULT '',
    settings_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id       TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes_json  TEXT NOT NULL,
    expires_at   REAL,
    revoked_at   REAL,
    rotated_from TEXT,
    last_used_at REAL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id         TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    job_type       TEXT NOT NULL,
    status         TEXT NOT NULL,
    filename       TEXT,
    file_format    TEXT,
    total_docs     INTEGER NOT NULL DEFAULT 0,
    processed_docs INTEGER NOT NULL DEFAULT 0,
    total_chunks   INTEGER NOT NULL DEFAULT 0,
    progress_pct   INTEGER NOT NULL DEFAULT 0,
    result_json    TEXT,
    error_message  TEXT,
    created_at     REAL NOT NULL,
    started_at     REAL,
    finished_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_created ON ingestion_jobs(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS backups (
    backup_id    TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    file_path    TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    integrity_ok INTEGER NOT NULL,
    doc_count    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backups_tenant ON backups(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT,
    timestamp     REAL NOT NULL,
    actor_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    tenant_id     TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    status        TEXT NOT NULL,
    ip_address    TEXT,
    latency_ms    REAL,
    detail_json   TEXT NOT NULL DEFAULT '{}',
    prev_hash     TEXT NOT NULL,
    entry_hash    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_logs(tenant_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
"""


class ControlPlane:
    """Thread-safe control plane managing tenants, scoped keys, jobs, backups, and audit chain."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.RLock()
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        try:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA busy_timeout=5000")
            self._con.execute("PRAGMA foreign_keys=ON")
            self._con.executescript(CONTROL_SCHEMA)
        except Exception:
            self._con.close()
            raise

    def close(self) -> None:
        with self.lock:
            self._con.close()

    # ---- Tenant Governance ----
    @staticmethod
    def _row_to_tenant(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "tenant_id": row["tenant_id"],
            "display_name": row["display_name"],
            "status": row["status"],
            "isolation_mode": row["isolation_mode"],
            "quota_docs": row["quota_docs"],
            "quota_chunks": row["quota_chunks"],
            "rate_limit_rpm": row["rate_limit_rpm"],
            "description": row["description"],
            "settings": json.loads(row["settings_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_tenant(
        self,
        tenant_id: str,
        display_name: str | None = None,
        *,
        quota_docs: int = 10000,
        quota_chunks: int = 100000,
        rate_limit_rpm: int = 120,
        description: str = "",
        settings: dict | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        validate_tenant_id(tenant_id)
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        if type(quota_docs) is not int or quota_docs < 0:
            raise ValueError("quota_docs must be a non-negative integer")
        if type(quota_chunks) is not int or quota_chunks < 0:
            raise ValueError("quota_chunks must be a non-negative integer")
        if type(rate_limit_rpm) is not int or rate_limit_rpm <= 0:
            raise ValueError("rate_limit_rpm must be a positive integer")
        now = time.time()
        display = (display_name or tenant_id).strip() or tenant_id
        with self.lock:
            if self._con.execute("SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone():
                raise ConflictError(f"tenant {tenant_id!r} already exists", error_code="tenant_exists")
            self._con.execute(
                "INSERT INTO tenants(tenant_id, display_name, status, isolation_mode, quota_docs, "
                "quota_chunks, rate_limit_rpm, description, settings_json, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tenant_id,
                    display,
                    status,
                    "physical_sqlite",
                    quota_docs,
                    quota_chunks,
                    rate_limit_rpm,
                    description or "",
                    _canonical_json(settings or {}),
                    now,
                    now,
                ),
            )
            self._con.commit()
            return self.get_tenant(tenant_id)

    def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        with self.lock:
            row = self._con.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"tenant {tenant_id!r} not found", error_code="tenant_not_found")
            return self._row_to_tenant(row)

    def list_tenants(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._con.execute("SELECT * FROM tenants ORDER BY created_at ASC, tenant_id ASC").fetchall()
            return [self._row_to_tenant(r) for r in rows]

    def update_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        quota_docs: int | None = None,
        quota_chunks: int | None = None,
        rate_limit_rpm: int | None = None,
        description: str | None = None,
        settings: dict | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            current = self.get_tenant(tenant_id)
            if status is not None and status not in VALID_STATUSES:
                raise ValueError(f"status must be one of {VALID_STATUSES}")
            if quota_docs is not None and (type(quota_docs) is not int or quota_docs < 0):
                raise ValueError("quota_docs must be a non-negative integer")
            if quota_chunks is not None and (type(quota_chunks) is not int or quota_chunks < 0):
                raise ValueError("quota_chunks must be a non-negative integer")
            if rate_limit_rpm is not None and (type(rate_limit_rpm) is not int or rate_limit_rpm <= 0):
                raise ValueError("rate_limit_rpm must be a positive integer")
            now = time.time()
            self._con.execute(
                "UPDATE tenants SET display_name=?, status=?, quota_docs=?, quota_chunks=?, "
                "rate_limit_rpm=?, description=?, settings_json=?, updated_at=? WHERE tenant_id=?",
                (
                    display_name.strip() if isinstance(display_name, str) and display_name.strip() else current["display_name"],
                    status if status is not None else current["status"],
                    quota_docs if quota_docs is not None else current["quota_docs"],
                    quota_chunks if quota_chunks is not None else current["quota_chunks"],
                    rate_limit_rpm if rate_limit_rpm is not None else current["rate_limit_rpm"],
                    description if description is not None else current["description"],
                    _canonical_json(settings if settings is not None else current["settings"]),
                    now,
                    tenant_id,
                ),
            )
            self._con.commit()
            return self.get_tenant(tenant_id)

    def delete_tenant_record(self, tenant_id: str) -> bool:
        with self.lock:
            cur = self._con.execute("DELETE FROM tenants WHERE tenant_id=?", (tenant_id,))
            self._con.commit()
            return cur.rowcount > 0

    # ---- Scoped API Key Lifecycle ----
    @staticmethod
    def _validate_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
        if scopes is None:
            return ["read", "write", "chat", "eval"]
        if not isinstance(scopes, (list, tuple)) or not scopes:
            raise ValueError("scopes must be a non-empty list")
        cleaned: list[str] = []
        for s in scopes:
            if s not in VALID_SCOPES:
                raise ValueError(f"invalid scope {s!r}; allowed: {VALID_SCOPES}")
            if s not in cleaned:
                cleaned.append(s)
        return cleaned

    @staticmethod
    def _row_to_key(row: sqlite3.Row) -> dict[str, Any]:
        now = time.time()
        revoked = row["revoked_at"] is not None
        expired = row["expires_at"] is not None and row["expires_at"] <= now
        status = "revoked" if revoked else ("expired" if expired else "active")
        return {
            "key_id": row["key_id"],
            "tenant_id": row["tenant_id"],
            "name": row["name"],
            "key_prefix": row["key_prefix"],
            "scopes": json.loads(row["scopes_json"]),
            "status": status,
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "rotated_from": row["rotated_from"],
            "last_used_at": row["last_used_at"],
            "created_at": row["created_at"],
        }

    def issue_key(
        self,
        tenant_id: str,
        name: str = "default",
        scopes: list[str] | None = None,
        *,
        expires_in_days: float | None = None,
        raw_key_override: str | None = None,
        rotated_from: str | None = None,
    ) -> dict[str, Any]:
        clean_scopes = self._validate_scopes(scopes)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("key name must be a non-empty string")
        if expires_in_days is not None:
            if type(expires_in_days) not in (int, float) or expires_in_days <= 0:
                raise ValueError("expires_in_days must be a positive number")
        now = time.time()
        expires_at = now + float(expires_in_days) * 86400.0 if expires_in_days is not None else None
        raw_key = raw_key_override or ("tr_live_" + secrets.token_urlsafe(32))
        if len(raw_key) < 12:
            raise ValueError("api key must be at least 12 characters")
        key_id = "key_" + uuid.uuid4().hex[:16]
        key_prefix = raw_key[:14] + "..."
        key_hash = _hash_key(raw_key)

        with self.lock:
            self.get_tenant(tenant_id)
            if self._con.execute("SELECT 1 FROM api_keys WHERE key_hash=?", (key_hash,)).fetchone():
                raise ConflictError("api key hash collision", error_code="key_exists")
            self._con.execute(
                "INSERT INTO api_keys(key_id, tenant_id, name, key_prefix, key_hash, scopes_json, "
                "expires_at, revoked_at, rotated_from, last_used_at, created_at) "
                "VALUES(?,?,?,?,?,?,?,NULL,?,NULL,?)",
                (key_id, tenant_id, name.strip(), key_prefix, key_hash, json.dumps(clean_scopes), expires_at, rotated_from, now),
            )
            self._con.commit()
            row = self._con.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            record = self._row_to_key(row)
            record["api_key"] = raw_key
            return record

    def list_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            if tenant_id is not None:
                rows = self._con.execute(
                    "SELECT * FROM api_keys WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,)
                ).fetchall()
            else:
                rows = self._con.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
            return [self._row_to_key(r) for r in rows]

    def get_key(self, key_id: str) -> dict[str, Any]:
        with self.lock:
            row = self._con.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"api key {key_id!r} not found", error_code="key_not_found")
            return self._row_to_key(row)

    def revoke_key(self, key_id: str) -> dict[str, Any]:
        with self.lock:
            row = self._con.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"api key {key_id!r} not found", error_code="key_not_found")
            if row["revoked_at"] is None:
                self._con.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=?", (time.time(), key_id))
                self._con.commit()
            return self.get_key(key_id)

    def rotate_key(
        self,
        key_id: str,
        *,
        grace_seconds: int = 3600,
        name: str | None = None,
        expires_in_days: float | None = None,
    ) -> dict[str, Any]:
        if type(grace_seconds) is not int or grace_seconds < 0:
            raise ValueError("grace_seconds must be a non-negative integer")
        with self.lock:
            row = self._con.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"api key {key_id!r} not found", error_code="key_not_found")
            if row["revoked_at"] is not None:
                raise ConflictError("cannot rotate a revoked api key", error_code="key_revoked")
            now = time.time()
            if row["expires_at"] is not None and row["expires_at"] <= now:
                raise ConflictError("cannot rotate an expired api key", error_code="key_expired")
            scopes = json.loads(row["scopes_json"])
            new_name = name.strip() if isinstance(name, str) and name.strip() else f"{row['name']} (rotated)"
            new_key = self.issue_key(
                row["tenant_id"],
                name=new_name,
                scopes=scopes,
                expires_in_days=expires_in_days,
                rotated_from=key_id,
            )
            if grace_seconds == 0:
                self._con.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=?", (now, key_id))
            else:
                grace_exp = now + grace_seconds
                old_exp = row["expires_at"]
                final_exp = min(old_exp, grace_exp) if old_exp is not None else grace_exp
                self._con.execute("UPDATE api_keys SET expires_at=? WHERE key_id=?", (final_exp, key_id))
            self._con.commit()
            return {
                "new_key": new_key,
                "previous_key": self.get_key(key_id),
                "grace_seconds": grace_seconds,
            }

    def authenticate_key(self, raw_key: str, required_scope: str | None = None) -> dict[str, Any]:
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise UnauthorizedError("missing or invalid API key")
        key_hash = _hash_key(raw_key.strip())
        now = time.time()
        with self.lock:
            row = self._con.execute(
                "SELECT k.*, t.display_name, t.status AS tenant_status, t.quota_docs, "
                "t.quota_chunks, t.rate_limit_rpm, t.isolation_mode "
                "FROM api_keys k JOIN tenants t ON k.tenant_id = t.tenant_id "
                "WHERE k.key_hash=?",
                (key_hash,),
            ).fetchone()
            if row is None:
                raise UnauthorizedError("invalid API key")
            if row["revoked_at"] is not None:
                raise UnauthorizedError("API key has been revoked", error_code="key_revoked")
            if row["expires_at"] is not None and row["expires_at"] <= now:
                raise UnauthorizedError("API key has expired", error_code="key_expired")
            if row["tenant_status"] != "active":
                raise ForbiddenError(
                    f"tenant {row['tenant_id']!r} is suspended", error_code="tenant_suspended"
                )
            scopes = json.loads(row["scopes_json"])
            if required_scope is not None:
                if required_scope not in scopes and "tenant_admin" not in scopes:
                    raise ForbiddenError(
                        f"API key lacks required scope {required_scope!r}",
                        error_code="insufficient_scope",
                    )
            self._con.execute("UPDATE api_keys SET last_used_at=? WHERE key_id=?", (now, row["key_id"]))
            self._con.commit()
            return {
                "key_id": row["key_id"],
                "key_name": row["name"],
                "key_prefix": row["key_prefix"],
                "tenant_id": row["tenant_id"],
                "display_name": row["display_name"],
                "tenant_status": row["tenant_status"],
                "isolation_mode": row["isolation_mode"],
                "scopes": scopes,
                "quota_docs": row["quota_docs"],
                "quota_chunks": row["quota_chunks"],
                "rate_limit_rpm": row["rate_limit_rpm"],
            }

    # ---- Ingestion Jobs ----
    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "tenant_id": row["tenant_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "filename": row["filename"],
            "file_format": row["file_format"],
            "total_docs": row["total_docs"],
            "processed_docs": row["processed_docs"],
            "total_chunks": row["total_chunks"],
            "progress_pct": row["progress_pct"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def create_job(
        self,
        tenant_id: str,
        job_type: str,
        *,
        filename: str | None = None,
        file_format: str | None = None,
        total_docs: int = 1,
    ) -> dict[str, Any]:
        job_id = "job_" + uuid.uuid4().hex[:16]
        now = time.time()
        with self.lock:
            self._con.execute(
                "INSERT INTO ingestion_jobs(job_id, tenant_id, job_type, status, filename, "
                "file_format, total_docs, processed_docs, total_chunks, progress_pct, created_at) "
                "VALUES(?,?,?,'queued',?,?,?,0,0,0,?)",
                (job_id, tenant_id, job_type, filename, file_format, total_docs, now),
            )
            self._con.commit()
            return self.get_job(job_id)

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        processed_docs: int | None = None,
        total_chunks: int | None = None,
        progress_pct: int | None = None,
        result: Any = None,
        error_message: str | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            cur_job = self.get_job(job_id)
            self._con.execute(
                "UPDATE ingestion_jobs SET status=?, processed_docs=?, total_chunks=?, "
                "progress_pct=?, result_json=?, error_message=?, started_at=?, finished_at=? "
                "WHERE job_id=?",
                (
                    status if status is not None else cur_job["status"],
                    processed_docs if processed_docs is not None else cur_job["processed_docs"],
                    total_chunks if total_chunks is not None else cur_job["total_chunks"],
                    progress_pct if progress_pct is not None else cur_job["progress_pct"],
                    _canonical_json(result) if result is not None else (
                        _canonical_json(cur_job["result"]) if cur_job["result"] is not None else None
                    ),
                    error_message if error_message is not None else cur_job["error_message"],
                    started_at if started_at is not None else cur_job["started_at"],
                    finished_at if finished_at is not None else cur_job["finished_at"],
                    job_id,
                ),
            )
            self._con.commit()
            return self.get_job(job_id)

    def get_job(self, job_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            row = self._con.execute("SELECT * FROM ingestion_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or (tenant_id is not None and row["tenant_id"] != tenant_id):
                raise NotFoundError(f"job {job_id!r} not found", error_code="job_not_found")
            return self._row_to_job(row)

    def list_jobs(self, tenant_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.lock:
            if tenant_id is not None:
                rows = self._con.execute(
                    "SELECT * FROM ingestion_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = self._con.execute(
                    "SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._row_to_job(r) for r in rows]

    # ---- Backups Metadata ----
    def record_backup(
        self,
        backup_id: str,
        tenant_id: str,
        file_path: str,
        size_bytes: int,
        sha256: str,
        integrity_ok: bool,
        doc_count: int,
    ) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            self._con.execute(
                "INSERT INTO backups(backup_id, tenant_id, file_path, size_bytes, sha256, "
                "integrity_ok, doc_count, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (backup_id, tenant_id, file_path, size_bytes, sha256, 1 if integrity_ok else 0, doc_count, now),
            )
            self._con.commit()
            return self.get_backup(backup_id)

    def get_backup(self, backup_id: str) -> dict[str, Any]:
        with self.lock:
            row = self._con.execute("SELECT * FROM backups WHERE backup_id=?", (backup_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"backup {backup_id!r} not found", error_code="backup_not_found")
            return {
                "backup_id": row["backup_id"],
                "tenant_id": row["tenant_id"],
                "file_path": row["file_path"],
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
                "integrity_ok": bool(row["integrity_ok"]),
                "doc_count": row["doc_count"],
                "created_at": row["created_at"],
            }

    def list_backups(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            if tenant_id is not None:
                rows = self._con.execute(
                    "SELECT * FROM backups WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,)
                ).fetchall()
            else:
                rows = self._con.execute("SELECT * FROM backups ORDER BY created_at DESC").fetchall()
            return [
                {
                    "backup_id": r["backup_id"],
                    "tenant_id": r["tenant_id"],
                    "file_path": r["file_path"],
                    "size_bytes": r["size_bytes"],
                    "sha256": r["sha256"],
                    "integrity_ok": bool(r["integrity_ok"]),
                    "doc_count": r["doc_count"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    # ---- Tamper-Evident SHA-256 Audit Log Chain ----
    def append_audit(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        status: str = "ok",
        tenant_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        latency_ms: float | None = None,
        detail: dict | None = None,
    ) -> dict[str, Any]:
        ts = round(time.time(), 6)
        ts_str = f"{ts:.6f}"
        detail_json = _canonical_json(detail)
        with self.lock:
            prev_row = self._con.execute(
                "SELECT entry_hash FROM audit_logs ORDER BY audit_id DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev_row["entry_hash"] if prev_row else GENESIS_HASH
            entry_hash = _compute_audit_hash(
                prev_hash,
                ts_str,
                actor_type,
                actor_id,
                tenant_id,
                action,
                resource_type,
                resource_id,
                status,
                detail_json,
            )
            cur = self._con.execute(
                "INSERT INTO audit_logs(request_id, timestamp, actor_type, actor_id, tenant_id, "
                "action, resource_type, resource_id, status, ip_address, latency_ms, detail_json, "
                "prev_hash, entry_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    ts,
                    actor_type,
                    actor_id,
                    tenant_id,
                    action,
                    resource_type,
                    resource_id,
                    status,
                    ip_address,
                    round(float(latency_ms), 3) if latency_ms is not None else None,
                    detail_json,
                    prev_hash,
                    entry_hash,
                ),
            )
            self._con.commit()
            row = self._con.execute("SELECT * FROM audit_logs WHERE audit_id=?", (cur.lastrowid,)).fetchone()
            return self._row_to_audit(row)

    @staticmethod
    def _row_to_audit(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "audit_id": row["audit_id"],
            "request_id": row["request_id"],
            "timestamp": row["timestamp"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "tenant_id": row["tenant_id"],
            "action": row["action"],
            "resource_type": row["resource_type"],
            "resource_id": row["resource_id"],
            "status": row["status"],
            "ip_address": row["ip_address"],
            "latency_ms": row["latency_ms"],
            "detail": json.loads(row["detail_json"] or "{}"),
            "prev_hash": row["prev_hash"],
            "entry_hash": row["entry_hash"],
        }

    def query_audit(
        self,
        *,
        tenant_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clauses, params = [], []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self._con.execute(
                f"SELECT * FROM audit_logs {where} ORDER BY audit_id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [self._row_to_audit(r) for r in rows]

    def verify_audit_chain(self) -> dict[str, Any]:
        with self.lock:
            rows = self._con.execute("SELECT * FROM audit_logs ORDER BY audit_id ASC").fetchall()
            expected_prev = GENESIS_HASH
            for idx, r in enumerate(rows):
                if r["prev_hash"] != expected_prev:
                    return {
                        "valid": False,
                        "checked": idx,
                        "total": len(rows),
                        "broken_at": r["audit_id"],
                        "reason": "prev_hash mismatch",
                    }
                computed = _compute_audit_hash(
                    r["prev_hash"],
                    f"{float(r['timestamp']):.6f}",
                    r["actor_type"],
                    r["actor_id"],
                    r["tenant_id"],
                    r["action"],
                    r["resource_type"],
                    r["resource_id"],
                    r["status"],
                    r["detail_json"],
                )
                if computed != r["entry_hash"]:
                    return {
                        "valid": False,
                        "checked": idx,
                        "total": len(rows),
                        "broken_at": r["audit_id"],
                        "reason": "entry_hash mismatch (record tampered)",
                    }
                expected_prev = r["entry_hash"]
            return {
                "valid": True,
                "checked": len(rows),
                "total": len(rows),
                "broken_at": None,
                "head_hash": expected_prev,
            }
