"""Database-wide model identity and optimistic, atomic whole-database reindex."""
from __future__ import annotations
import json
import math
from .store import _pack, _unpack


class EmbeddingIdentityError(RuntimeError):
    """Configured model cannot safely use the persisted vectors."""


class ReindexConflictError(EmbeddingIdentityError):
    """Corpus changed while replacement vectors were prepared."""


class ReindexCancelled(EmbeddingIdentityError):
    """Reindex cancelled without changing the live index."""


def _descriptor(embedder, override=None):
    value = override if override is not None else getattr(embedder, 'embedding_identity', None)
    if not isinstance(value, dict):
        raise EmbeddingIdentityError('custom embedders must declare embedding_identity or supply an identity override')
    if set(value) != {'provider', 'model', 'revision'} or any(
            not isinstance(v, str) or not v.strip() for v in value.values()):
        raise EmbeddingIdentityError('embedding identity requires non-empty provider, model and revision strings')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class EmbeddingIdentity:
    def __init__(self, store, embedder, identity=None):
        self.store, self.embedder, self.override = store, embedder, identity
        with store.lock:
            store._con.execute('CREATE TABLE IF NOT EXISTS embedding_config ('
                               'singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
                               'identity TEXT NOT NULL, dimension INTEGER NOT NULL CHECK(dimension>0))')
            store._con.commit()

    def _row(self):
        return self.store._con.execute(
            'SELECT identity, dimension FROM embedding_config WHERE singleton=1').fetchone()

    def status(self):
        with self.store.lock:
            row = self._row()
            return None if row is None else {**json.loads(row[0]), 'dimension': row[1]}

    def capture(self):
        """Capture model plus identity for a request spanning unlocked provider IO."""
        with self.store.lock:
            return self.embedder, _descriptor(self.embedder, self.override)

    def check(self, dimension=None, *, expected=None):
        """Check before provider IO and again inside the operation transaction."""
        expected = expected if expected is not None else _descriptor(self.embedder, self.override)
        with self.store.lock:
            row = self._row()
            if row is None:
                if self.store._con.execute('SELECT 1 FROM docs LIMIT 1').fetchone():
                    raise EmbeddingIdentityError('legacy index has no model identity; explicitly adopt it or reindex')
            elif row[0] != expected:
                raise EmbeddingIdentityError('embedding model identity differs; reindex the database before use')
            elif dimension is not None and row[1] != dimension:
                raise EmbeddingIdentityError('embedding dimension differs; reindex the database before use')

    @staticmethod
    def validate(vectors, count):
        if not isinstance(vectors, list) or len(vectors) != count:
            raise EmbeddingIdentityError('embedding result count differs from input')
        dim = None
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise EmbeddingIdentityError('embedding vectors must be non-empty lists')
            if dim is not None and len(vector) != dim:
                raise EmbeddingIdentityError('embedding dimensions differ within batch')
            dim = len(vector)
            for value in vector:
                if type(value) not in (float, int):
                    raise EmbeddingIdentityError('embedding values must be finite float32 numbers')
                try:
                    valid = math.isfinite(value) and abs(value) <= 3.4028234663852886e38
                except OverflowError:
                    valid = False
                if not valid:
                    raise EmbeddingIdentityError('embedding values must be finite float32 numbers')
        return dim

    def bind(self, dimension, *, expected=None):
        """Validate/pin inside caller-owned write transaction; does not commit."""
        if type(dimension) is not int or dimension <= 0:
            raise EmbeddingIdentityError('embedding dimension must be positive')
        with self.store.lock:
            if not self.store._con.in_transaction:
                raise RuntimeError('bind requires an active write transaction')
            self.check(dimension, expected=expected)
            self.store._con.execute('INSERT OR IGNORE INTO embedding_config VALUES(1,?,?)',
                                    (expected if expected is not None else _descriptor(self.embedder, self.override), dimension))

    def adopt_legacy(self):
        """Explicit operator assertion of provenance, not model attestation.

        Back up and verify model history first. Refuse mixed dimensions, absent
        vectors and malformed blobs. Never make a provider request.
        """
        identity = _descriptor(self.embedder, self.override)
        with self.store.lock:
            con = self.store._con
            con.execute('BEGIN IMMEDIATE')
            try:
                if self._row() is not None:
                    self.check()
                    con.commit()
                    return self.status()
                rows = con.execute('SELECT embedding, dim FROM docs').fetchall()
                if not rows:
                    raise EmbeddingIdentityError('empty database has nothing to adopt; index a document instead')
                dimensions = {row[1] for row in rows}
                if len(dimensions) != 1:
                    raise EmbeddingIdentityError('legacy vectors have mixed dimensions; reindex required')
                dim = rows[0][1]
                if type(dim) is not int or dim <= 0 or any(
                        not isinstance(blob, bytes) or len(blob) != dim * 4 for blob, _ in rows):
                    raise EmbeddingIdentityError('legacy vectors are incomplete or malformed; reindex required')
                known_dim = vars(self.embedder).get('dim', vars(self.embedder).get('_dim'))
                if known_dim is not None and known_dim != dim:
                    raise EmbeddingIdentityError('configured dimension differs from legacy vectors')
                self.validate([_unpack(blob, dim) for blob, _ in rows], len(rows))
                con.execute('INSERT INTO embedding_config VALUES(1,?,?)', (identity, dim))
                con.commit()
                return self.status()
            except BaseException:
                con.rollback()
                raise

    def _snapshot(self):
        return self.store._con.execute(
            'SELECT tenant_id, doc_id, text, metadata, embedding, dim, created_at '
            'FROM docs ORDER BY tenant_id, doc_id').fetchall()

    def reindex(self, embedder, identity=None, batch_size=64, cancelled=None):
        """Prepare outside locks, compare snapshot and swap atomically.

        Failure/cancellation/process termination before commit leaves the old
        index intact. Retry from the beginning; provider work is not resumed.
        Concurrent corpus/config changes abort instead of overwriting new data.
        """
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError('batch_size must be a positive integer')
        target = _descriptor(embedder, identity)
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise ReindexCancelled('reindex cancelled; live index unchanged')
        check_cancelled()
        with self.store.lock:
            con = self.store._con
            con.execute('BEGIN')
            try:
                old_config, snapshot = self._row(), self._snapshot()
                con.commit()
            except BaseException:
                con.rollback()
                raise
        if not snapshot:
            raise EmbeddingIdentityError('empty database has nothing to reindex')
        prepared, dimension = [], None
        for offset in range(0, len(snapshot), batch_size):
            check_cancelled()
            batch = snapshot[offset:offset + batch_size]
            vectors = embedder.embed([row[2] for row in batch])
            dim = self.validate(vectors, len(batch))
            if dimension is not None and dimension != dim:
                raise EmbeddingIdentityError('replacement dimensions changed between batches')
            dimension = dim
            prepared.extend((_pack(v)[0], dim, row[0], row[1]) for row, v in zip(batch, vectors))
        check_cancelled()
        with self.store.lock:
            con.execute('BEGIN IMMEDIATE')
            try:
                if self._row() != old_config or self._snapshot() != snapshot:
                    raise ReindexConflictError('database changed during reindex; retry using a fresh snapshot')
                check_cancelled()
                con.executemany('UPDATE docs SET embedding=?, dim=? WHERE tenant_id=? AND doc_id=?', prepared)
                con.execute('INSERT INTO embedding_config VALUES(1,?,?) ON CONFLICT(singleton) '
                            'DO UPDATE SET identity=excluded.identity, dimension=excluded.dimension',
                            (target, dimension))
                check_cancelled()
                con.commit()
                self.embedder, self.override = embedder, identity
            except BaseException:
                con.rollback()
                raise
        return len(prepared)
