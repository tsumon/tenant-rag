"""Physical per-tenant SQLite storage pool, read-concurrency connections, vector cache, and online hot backups."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import secrets
import shutil
import sqlite3
import threading
import uuid
from typing import Any

from tenantrag import TenantRAG
from tenantrag.store import _unpack

from .control_plane import validate_tenant_id
from .errors import ConflictError, NotFoundError


def _unit_vec(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0.0:
        return None
    return [x / norm for x in vec]


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class TenantNode:
    """Isolated physical SQLite runtime for a single tenant."""

    def __init__(
        self,
        tenant_id: str,
        tenant_dir: Path,
        embedder,
        *,
        embedding_identity=None,
        quota_docs: int = 10000,
        read_pool_size: int = 4,
    ):
        self.tenant_id = validate_tenant_id(tenant_id)
        self.tenant_dir = tenant_dir
        self.tenant_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tenant_dir / "index.db"
        self.key_file = self.tenant_dir / ".node_key"
        self._node_key = self._load_or_create_node_key()
        self._op_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._write_gen = 0
        self._cached_gen = -1
        self._cached_dim: int | None = None
        self._cached_items: list[tuple[str, list[float] | None, dict[str, Any]]] = []
        self._closed = False

        self.rag = TenantRAG(
            str(self.db_path),
            embedder=embedder,
            embedding_identity=embedding_identity,
        )
        self._ensure_inner_tenant(quota_docs)

        self._read_pool_size = max(1, int(read_pool_size))
        self._read_pool: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(
            maxsize=self._read_pool_size
        )

    def _load_or_create_node_key(self) -> str:
        if self.key_file.exists():
            val = self.key_file.read_text(encoding="utf-8").strip()
            if len(val) >= 16:
                return val
        val = secrets.token_urlsafe(24)
        self.key_file.write_text(val, encoding="utf-8")
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            pass
        return val

    def _ensure_inner_tenant(self, quota_docs: int) -> None:
        key_hash = hashlib.sha256(self._node_key.encode("utf-8")).hexdigest()
        with self.rag.store.lock:
            con = self.rag.store._con
            row = con.execute(
                "SELECT api_key_hash, quota_docs FROM tenants WHERE tenant_id=?",
                (self.tenant_id,),
            ).fetchone()
            if row is None:
                self.rag.register(self.tenant_id, self._node_key, quota_docs=quota_docs)
            elif row[0] != key_hash or row[1] != quota_docs:
                con.execute(
                    "UPDATE tenants SET api_key_hash=?, quota_docs=? WHERE tenant_id=?",
                    (key_hash, quota_docs, self.tenant_id),
                )
                con.commit()

    def sync_quota(self, quota_docs: int) -> None:
        with self.rag.store.lock:
            self.rag.store._con.execute(
                "UPDATE tenants SET quota_docs=? WHERE tenant_id=?",
                (quota_docs, self.tenant_id),
            )
            self.rag.store._con.commit()

    def _open_ro_conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        con.execute("PRAGMA busy_timeout=5000")
        return con

    @contextlib.contextmanager
    def read_connection(self):
        if self._closed:
            raise RuntimeError(f"TenantNode {self.tenant_id!r} is closed")
        con = None
        from_pool = False
        try:
            con = self._read_pool.get_nowait()
            from_pool = True
        except queue.Empty:
            con = self._open_ro_conn()
        try:
            yield con
        finally:
            if self._closed:
                con.close()
            else:
                try:
                    self._read_pool.put_nowait(con)
                except queue.Full:
                    if from_pool or True:
                        con.close()

    def bump_generation(self) -> int:
        with self._cache_lock:
            self._write_gen += 1
            return self._write_gen

    def get_vector_snapshot(self) -> tuple[list[tuple[str, list[float] | None, dict[str, Any]]], int | None]:
        with self._cache_lock:
            target_gen = self._write_gen
            if self._cached_gen == target_gen:
                return self._cached_items, self._cached_dim

        with self.read_connection() as con:
            rows = con.execute(
                "SELECT doc_id, metadata, embedding, dim FROM docs "
                "WHERE tenant_id=? AND embedding IS NOT NULL",
                (self.tenant_id,),
            ).fetchall()

        items: list[tuple[str, list[float] | None, dict[str, Any]]] = []
        observed_dim: int | None = None
        for doc_id, meta_raw, blob, dim in rows:
            if not blob or not dim:
                continue
            if observed_dim is None:
                observed_dim = dim
            vec = _unpack(blob, dim)
            items.append((doc_id, _unit_vec(vec), json.loads(meta_raw or "{}")))

        with self._cache_lock:
            if self._write_gen == target_gen:
                self._cached_items = items
                self._cached_dim = observed_dim
                self._cached_gen = target_gen
        return items, observed_dim

    # ---- Write Operations (serialized per tenant, never across tenants) ----
    def index_doc(self, text: str, doc_id: str | None = None, metadata: dict | None = None) -> str:
        with self._op_lock:
            res = self.rag.index(self._node_key, text, doc_id=doc_id, metadata=metadata)
            self.bump_generation()
            return res

    def ingest_doc(
        self,
        text: str,
        doc_id: str | None = None,
        metadata: dict | None = None,
        source: str | None = None,
        *,
        format: str = "text",
        max_chars: int = 1000,
        overlap: int = 100,
    ) -> dict[str, Any]:
        with self._op_lock:
            res = self.rag.ingest(
                self._node_key,
                text,
                doc_id=doc_id,
                metadata=metadata,
                source=source,
                format=format,
                max_chars=max_chars,
                overlap=overlap,
            )
            self.bump_generation()
            return res

    def ingest_batch(
        self,
        documents: list[dict],
        *,
        max_chars: int = 1000,
        overlap: int = 100,
        batch_size: int = 64,
    ) -> list[dict]:
        with self._op_lock:
            res = self.rag.ingest_batch(
                self._node_key,
                documents,
                max_chars=max_chars,
                overlap=overlap,
                batch_size=batch_size,
            )
            self.bump_generation()
            return res

    def delete_doc(self, doc_id: str) -> bool:
        with self._op_lock:
            res = self.rag.delete(self._node_key, doc_id)
            self.bump_generation()
            return res

    def reindex(self, embedder, *, embedding_identity=None, batch_size: int = 64) -> int:
        with self._op_lock:
            res = self.rag.reindex(
                embedder, embedding_identity=embedding_identity, batch_size=batch_size
            )
            self.bump_generation()
            return res

    # ---- Read Operations ----
    def list_docs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 10000))
        offset = max(0, int(offset))
        with self.read_connection() as con:
            rows = con.execute(
                "SELECT doc_id, text, metadata, created_at, source, kind, chunk_count FROM ("
                "  SELECT d.doc_id AS doc_id, d.text AS text, d.metadata AS metadata, "
                "         d.created_at AS created_at, NULL AS source, 'single' AS kind, 1 AS chunk_count "
                "  FROM docs d WHERE d.tenant_id=? "
                "  AND NOT EXISTS(SELECT 1 FROM doc_chunks c WHERE c.tenant_id=d.tenant_id AND c.doc_id=d.doc_id) "
                "  UNION ALL "
                "  SELECT p.parent_id AS doc_id, p.text AS text, p.metadata AS metadata, "
                "         p.created_at AS created_at, p.source AS source, 'parent' AS kind, "
                "         (SELECT COUNT(*) FROM doc_chunks c WHERE c.tenant_id=p.tenant_id AND c.parent_id=p.parent_id) AS chunk_count "
                "  FROM parent_docs p WHERE p.tenant_id=?"
                ") ORDER BY created_at DESC, doc_id ASC LIMIT ? OFFSET ?",
                (self.tenant_id, self.tenant_id, limit, offset),
            ).fetchall()
        return [
            {
                "doc_id": r[0],
                "text": r[1],
                "metadata": json.loads(r[2] or "{}"),
                "created_at": r[3],
                "source": r[4],
                "kind": r[5],
                "chunk_count": r[6],
            }
            for r in rows
        ]

    def get_document_detail(self, doc_id: str) -> dict[str, Any] | None:
        with self.read_connection() as con:
            parent = con.execute(
                "SELECT parent_id, text, metadata, source, content_hash, options, created_at "
                "FROM parent_docs WHERE tenant_id=? AND parent_id=?",
                (self.tenant_id, doc_id),
            ).fetchone()
            if parent is not None:
                chunk_rows = con.execute(
                    "SELECT c.doc_id, c.start, c.end, d.text, d.metadata "
                    "FROM doc_chunks c JOIN docs d ON c.tenant_id=d.tenant_id AND c.doc_id=d.doc_id "
                    "WHERE c.tenant_id=? AND c.parent_id=? ORDER BY c.start ASC, c.end ASC",
                    (self.tenant_id, doc_id),
                ).fetchall()
                chunks = []
                for idx, r in enumerate(chunk_rows):
                    meta = json.loads(r[4] or "{}")
                    chunks.append(
                        {
                            "chunk_id": r[0],
                            "index": meta.get("_chunk", {}).get("index", idx),
                            "start": r[1],
                            "end": r[2],
                            "text": r[3],
                        }
                    )
                return {
                    "doc_id": parent[0],
                    "kind": "parent",
                    "text": parent[1],
                    "metadata": json.loads(parent[2] or "{}"),
                    "source": parent[3],
                    "content_hash": parent[4],
                    "options": json.loads(parent[5] or "{}"),
                    "created_at": parent[6],
                    "chunks": chunks,
                }

            single = con.execute(
                "SELECT doc_id, text, metadata, created_at FROM docs WHERE tenant_id=? AND doc_id=?",
                (self.tenant_id, doc_id),
            ).fetchone()
            if single is not None:
                return {
                    "doc_id": single[0],
                    "kind": "single",
                    "text": single[1],
                    "metadata": json.loads(single[2] or "{}"),
                    "source": None,
                    "created_at": single[3],
                    "chunks": [
                        {
                            "chunk_id": single[0],
                            "index": 0,
                            "start": 0,
                            "end": len(single[1]),
                            "text": single[1],
                        }
                    ],
                }
        return None

    def stats(self) -> dict[str, Any]:
        with self.read_connection() as con:
            single_count = con.execute(
                "SELECT COUNT(*) FROM docs d WHERE d.tenant_id=? "
                "AND NOT EXISTS(SELECT 1 FROM doc_chunks c WHERE c.tenant_id=d.tenant_id AND c.doc_id=d.doc_id)",
                (self.tenant_id,),
            ).fetchone()[0]
            parent_count = con.execute(
                "SELECT COUNT(*) FROM parent_docs WHERE tenant_id=?", (self.tenant_id,)
            ).fetchone()[0]
            chunk_count = con.execute(
                "SELECT COUNT(*) FROM docs WHERE tenant_id=?", (self.tenant_id,)
            ).fetchone()[0]
        db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_path = self.tenant_dir / "index.db-wal"
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        return {
            "docs": int(single_count + parent_count),
            "parent_docs": int(parent_count),
            "single_docs": int(single_count),
            "chunks": int(chunk_count),
            "db_bytes": int(db_bytes + wal_bytes),
            "db_path": str(self.db_path),
            "cache_generation": self._write_gen,
            "model_identity": self.rag.identity.status(),
        }

    def close(self) -> None:
        with self._op_lock:
            self._closed = True
            while True:
                try:
                    c = self._read_pool.get_nowait()
                    c.close()
                except queue.Empty:
                    break
            self.rag.close()


class TenantPool:
    """Manages isolated per-tenant physical SQLite TenantNodes and backups."""

    def __init__(
        self,
        data_dir: str | Path,
        embedder,
        *,
        embedding_identity=None,
        read_pool_size: int = 4,
    ):
        self.data_dir = Path(data_dir)
        self.tenants_dir = self.data_dir / "tenants"
        self.backups_dir = self.data_dir / "backups"
        self.tenants_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.embedding_identity = embedding_identity
        self.read_pool_size = read_pool_size
        self._lock = threading.RLock()
        self._nodes: dict[str, TenantNode] = {}

    def get_node(self, tenant_id: str, *, quota_docs: int = 10000) -> TenantNode:
        validate_tenant_id(tenant_id)
        with self._lock:
            node = self._nodes.get(tenant_id)
            if node is None:
                node = TenantNode(
                    tenant_id,
                    self.tenants_dir / tenant_id,
                    self.embedder,
                    embedding_identity=self.embedding_identity,
                    quota_docs=quota_docs,
                    read_pool_size=self.read_pool_size,
                )
                self._nodes[tenant_id] = node
            return node

    def sync_quota(self, tenant_id: str, quota_docs: int) -> None:
        validate_tenant_id(tenant_id)
        with self._lock:
            if tenant_id in self._nodes or (self.tenants_dir / tenant_id / "index.db").exists():
                self.get_node(tenant_id, quota_docs=quota_docs).sync_quota(quota_docs)

    def create_backup(self, tenant_id: str) -> dict[str, Any]:
        node = self.get_node(tenant_id)
        backup_id = "bkp_" + uuid.uuid4().hex[:14]
        t_backup_dir = self.backups_dir / tenant_id
        t_backup_dir.mkdir(parents=True, exist_ok=True)
        dest_path = t_backup_dir / f"{backup_id}.db"

        with node._op_lock:
            with node.rag.store.lock:
                dest_con = sqlite3.connect(str(dest_path))
                try:
                    node.rag.store._con.backup(dest_con)
                    check = dest_con.execute("PRAGMA integrity_check").fetchone()
                    integrity_ok = bool(check and check[0] == "ok")
                    dest_con.commit()
                finally:
                    dest_con.close()
            doc_count = node.stats()["docs"]

        if not integrity_ok:
            dest_path.unlink(missing_ok=True)
            raise ConflictError("backup integrity_check failed", error_code="backup_corrupt")

        digest = _file_sha256(dest_path)
        size_bytes = dest_path.stat().st_size
        return {
            "backup_id": backup_id,
            "tenant_id": tenant_id,
            "file_path": str(dest_path),
            "size_bytes": size_bytes,
            "sha256": digest,
            "integrity_ok": integrity_ok,
            "doc_count": doc_count,
        }

    def restore_backup(
        self, tenant_id: str, backup_path: str | Path, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        validate_tenant_id(tenant_id)
        src = Path(backup_path)
        if not src.is_file():
            raise NotFoundError(f"backup file {src} not found", error_code="backup_file_missing")
        actual_sha = _file_sha256(src)
        if expected_sha256 and actual_sha != expected_sha256:
            raise ConflictError("backup SHA-256 verification failed", error_code="backup_checksum_mismatch")

        check_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            res = check_con.execute("PRAGMA integrity_check").fetchone()
            if not res or res[0] != "ok":
                raise ConflictError("backup SQLite integrity check failed", error_code="backup_corrupt")
        finally:
            check_con.close()

        with self._lock:
            node = self._nodes.pop(tenant_id, None)
            if node is not None:
                node.close()
            tenant_dir = self.tenants_dir / tenant_id
            tenant_dir.mkdir(parents=True, exist_ok=True)
            for suffix in ("index.db-wal", "index.db-shm"):
                (tenant_dir / suffix).unlink(missing_ok=True)
            target = tenant_dir / "index.db"
            tmp_target = tenant_dir / f".restore-{uuid.uuid4().hex}.db"
            shutil.copy2(src, tmp_target)
            os.replace(tmp_target, target)
            restored_node = self.get_node(tenant_id)
            return restored_node.stats()

    def shred_tenant(self, tenant_id: str) -> None:
        validate_tenant_id(tenant_id)
        with self._lock:
            node = self._nodes.pop(tenant_id, None)
            if node is not None:
                node.close()
            tdir = self.tenants_dir / tenant_id
            if tdir.exists():
                shutil.rmtree(tdir, ignore_errors=True)
            bdir = self.backups_dir / tenant_id
            if bdir.exists():
                shutil.rmtree(bdir, ignore_errors=True)

    def close_all(self) -> None:
        with self._lock:
            for node in list(self._nodes.values()):
                node.close()
            self._nodes.clear()
