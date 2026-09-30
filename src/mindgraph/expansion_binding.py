"""Explicit, read-only index binding for expansion handles.

exp2 separates a caller's scope alias from stored index identity. Handles are
locators, not credentials or proof that a source is current or true. No schema
migration, identity inferred from trust labels, or source-text fallback occurs.
"""
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from typing import Any

VERSION = "exp2"
MAX_HANDLE_BYTES = 8192
_KEYS = {"v", "scope", "index_id", "namespace", "path", "doc_id", "chunk_index", "content_hash"}


class ExpansionBindingError(ValueError):
    """A requested locator cannot be matched to independent stored identity."""


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        raise ExpansionBindingError("invalid expansion handle: unexpected or missing fields")
    if payload["v"] != VERSION:
        raise ExpansionBindingError("invalid expansion handle: unsupported version; query again")
    if not _nonblank(payload["doc_id"]):
        raise ExpansionBindingError("invalid expansion handle: document identity missing")
    chunk = payload["chunk_index"]
    if type(chunk) is not int or chunk < 0:
        raise ExpansionBindingError("invalid expansion handle: chunk index must be a nonnegative integer")
    for key in ("scope", "index_id", "namespace", "path", "content_hash"):
        if payload[key] is not None and not _nonblank(payload[key]):
            raise ExpansionBindingError(f"invalid expansion handle: {key} must be nonblank or null")
    return payload


def encode_handle(*, scope: str | None, index_id: str | None,
                  namespace: str | None, path: str | None, doc_id: str,
                  chunk_index: int, content_hash: str | None) -> str:
    payload = _validate(dict(v=VERSION, scope=scope, index_id=index_id,
                             namespace=namespace, path=path, doc_id=doc_id,
                             chunk_index=chunk_index, content_hash=content_hash))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    handle = VERSION + ":" + base64.urlsafe_b64encode(raw).decode("ascii")
    if len(handle.encode("utf-8")) > MAX_HANDLE_BYTES:
        raise ExpansionBindingError("invalid expansion handle: exceeds size bound")
    return handle


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExpansionBindingError("invalid expansion handle: duplicate JSON field")
        result[key] = value
    return result


def decode_handle(handle: str) -> dict[str, Any]:
    if not isinstance(handle, str) or not handle.startswith(VERSION + ":"):
        raise ExpansionBindingError("invalid expansion handle: exp2 required; legacy handles must be requeried")
    if len(handle.encode("utf-8")) > MAX_HANDLE_BYTES:
        raise ExpansionBindingError("invalid expansion handle: exceeds size bound")
    token = handle[len(VERSION) + 1:]
    try:
        raw = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode("ascii") != token:
            raise ExpansionBindingError("invalid expansion handle: noncanonical base64")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ExpansionBindingError("invalid expansion handle: malformed encoding or JSON") from exc
    _validate(payload)
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except UnicodeError as exc:
        raise ExpansionBindingError("invalid expansion handle: invalid Unicode") from exc
    if canonical != raw:
        raise ExpansionBindingError("invalid expansion handle: noncanonical JSON")
    return payload


def selected_index_id(conn: sqlite3.Connection) -> str:
    """Read identity from stored declarations, never from the requested handle.

    Existing ingests store index_id on every document. A coherent nonempty
    declaration set identifies the selected index without a migration. Null,
    mixed, empty or contradictory declarations cannot establish the binding.
    An optional index_meta declaration must agree; it cannot mask bad rows.
    """
    try:
        rows = conn.execute("SELECT index_id FROM documents GROUP BY index_id LIMIT 2").fetchall()
        if len(rows) != 1 or not _nonblank(rows[0][0]):
            raise ExpansionBindingError("expansion index binding unavailable: missing or ambiguous stored identity")
        actual = rows[0][0]
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_meta'"
        ).fetchone()
        if has_meta:
            declared = conn.execute("SELECT value FROM index_meta WHERE key='index_id'").fetchone()
            if declared is not None and declared[0] != actual:
                raise ExpansionBindingError("expansion index binding invalid: contradictory stored identity")
        return actual
    except sqlite3.Error as exc:
        raise ExpansionBindingError("expansion index binding unavailable: stored identity cannot be read") from exc


def validate_target(conn: sqlite3.Connection, payload: dict[str, Any], *,
                    requested_scope: str | None = None) -> tuple[sqlite3.Row, bool | None]:
    """Validate index, alias, document, path, namespace and hash before text.

    The caller must keep validation and chunk reads in one read transaction.
    No value in this routine comes from trust_profile or a database filename.
    """
    _validate(payload)
    if requested_scope is not None and payload["scope"] != requested_scope:
        raise ExpansionBindingError("expansion handle scope does not match requested scope")
    if not _nonblank(payload["index_id"]) or not _nonblank(payload["path"]):
        raise ExpansionBindingError("expansion index binding unavailable: handle lacks index or path; requery a scoped index")
    actual = selected_index_id(conn)
    if payload["index_id"] != actual:
        raise ExpansionBindingError("expansion handle index does not match selected index")
    try:
        cursor = conn.execute(
            "SELECT id, index_id, namespace, path, content_hash FROM documents WHERE id=?",
            (payload["doc_id"],),
        )
        cursor.row_factory = sqlite3.Row
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        raise ExpansionBindingError("expansion target identity lookup failed") from exc
    if row is None:
        raise ExpansionBindingError("expansion target missing: document not in selected index")
    if row["index_id"] != actual or row["namespace"] != payload["namespace"] or row["path"] != payload["path"]:
        raise ExpansionBindingError("expansion target identity mismatch: index, namespace or path changed")
    expected = payload["content_hash"]
    observed = row["content_hash"]
    if expected is not None:
        if not _nonblank(observed) or expected.strip().lower() != observed.strip().lower():
            raise ExpansionBindingError("stale expansion handle: indexed content hash changed or is missing")
        return row, True
    return row, None
