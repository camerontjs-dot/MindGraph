"""Independent stored identity must defeat colliding document/chunk/hash rows."""
import base64
import json
import sqlite3

import pytest
from mindgraph.expansion_binding import (
    ExpansionBindingError, decode_handle, encode_handle, selected_index_id,
    validate_target,
)


def connection(index="index-a", trust="durable_knowledge"):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE documents(id TEXT PRIMARY KEY,index_id TEXT,namespace TEXT,
                               path TEXT,content_hash TEXT,trust_profile TEXT);
        CREATE TABLE index_meta(key TEXT PRIMARY KEY,value TEXT);
    """)
    conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                 ("collision", index, "notes", "source.md", "same-hash", trust))
    return conn


def handle(**changes):
    values = dict(scope="reader-alias", index_id="index-a", namespace="notes",
                  path="source.md", doc_id="collision", chunk_index=0, content_hash="same-hash")
    values.update(changes)
    return encode_handle(**values)


def test_matching_index_and_scope_alias_are_distinct():
    conn = connection()
    try:
        row, matched = validate_target(conn, decode_handle(handle()), requested_scope="reader-alias")
        assert row["index_id"] == "index-a"
        assert matched is True
        assert selected_index_id(conn) == "index-a"
    finally:
        conn.close()


@pytest.mark.parametrize("trust", ["durable_knowledge", "project_status", None])
def test_collision_is_rejected_independently_of_trust(trust):
    conn = connection(index="index-b", trust=trust)
    try:
        with pytest.raises(ExpansionBindingError, match="selected index"):
            validate_target(conn, decode_handle(handle()))
    finally:
        conn.close()


@pytest.mark.parametrize("index", [None, "", " "])
def test_missing_stored_identity_is_not_invented(index):
    conn = connection(index=index)
    try:
        with pytest.raises(ExpansionBindingError, match="unavailable"):
            validate_target(conn, decode_handle(handle()))
    finally:
        conn.close()


@pytest.mark.parametrize("other", ["index-b", None])
def test_mixed_index_declarations_fail_closed(other):
    conn = connection()
    try:
        conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("other", other, None, "other.md", "hash", None))
        with pytest.raises(ExpansionBindingError, match="ambiguous"):
            validate_target(conn, decode_handle(handle()))
    finally:
        conn.close()


def test_meta_declaration_cannot_mask_row_identity():
    conn = connection()
    try:
        conn.execute("INSERT INTO index_meta VALUES('index_id','index-b')")
        with pytest.raises(ExpansionBindingError, match="contradictory"):
            validate_target(conn, decode_handle(handle()))
    finally:
        conn.close()


@pytest.mark.parametrize("change", [dict(index_id=None), dict(path=None)])
def test_missing_handle_binding_is_explicitly_unsupported(change):
    conn = connection()
    try:
        with pytest.raises(ExpansionBindingError, match="handle lacks"):
            validate_target(conn, decode_handle(handle(**change)))
    finally:
        conn.close()


@pytest.mark.parametrize("change", [dict(namespace="other"), dict(path="other.md")])
def test_target_namespace_and_path_must_match(change):
    conn = connection()
    try:
        with pytest.raises(ExpansionBindingError, match="target identity"):
            validate_target(conn, decode_handle(handle(**change)))
    finally:
        conn.close()


@pytest.mark.parametrize("observed", [None, "different-hash"])
def test_known_hash_cannot_be_downgraded(observed):
    conn = connection()
    try:
        conn.execute("UPDATE documents SET content_hash=?", (observed,))
        with pytest.raises(ExpansionBindingError, match="stale"):
            validate_target(conn, decode_handle(handle()))
    finally:
        conn.close()


def test_unknown_hash_stays_unknown_without_weakening_index_check():
    conn = connection()
    try:
        assert validate_target(conn, decode_handle(handle(content_hash=None)))[1] is None
        with pytest.raises(ExpansionBindingError, match="scope"):
            validate_target(conn, decode_handle(handle()), requested_scope="different-alias")
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["exp1:e30=", "exp2:!!!", "exp2:e30=", "", "exp2:" + "A" * 9000])
def test_malformed_or_legacy_locator_fails_closed(bad):
    with pytest.raises(ExpansionBindingError):
        decode_handle(bad)


@pytest.mark.parametrize("chunk", [True, -1, 1.5, "0"])
def test_chunk_index_has_strict_integer_type(chunk):
    with pytest.raises(ExpansionBindingError):
        handle(chunk_index=chunk)


def test_duplicate_json_keys_are_not_silently_accepted():
    raw = b'{"v":"exp2","v":"exp2"}'
    with pytest.raises(ExpansionBindingError):
        decode_handle("exp2:" + base64.urlsafe_b64encode(raw).decode())


def test_handle_preserves_unicode_without_source_root():
    encoded = handle(path="notes/caf\u00e9.md")
    assert decode_handle(encoded)["path"] == "notes/caf\u00e9.md"
    assert "source_root" not in decode_handle(encoded)
    assert encoded == handle(path="notes/caf\u00e9.md")


def test_identity_reads_do_not_write_or_commit_callers_transaction():
    conn = connection()
    try:
        before = conn.total_changes
        assert conn.in_transaction
        validate_target(conn, decode_handle(handle()))
        assert conn.total_changes == before
        assert conn.in_transaction
    finally:
        conn.close()
