"""SQLite access for the archive: connect, create the schema, log decisions.

Standard library only. The schema lives in ``archive/schema.sql``; this module
applies it and owns the one piece of logic SQL cannot do on its own: the hash
chain over the append-only ``decision`` table.

Hash chain
    event_hash = sha256(prior_hash + canonical_json(event))
where ``prior_hash`` is the previous decision's ``event_hash`` (64 zeros for the
first decision) and ``event`` is every stored column except ``seq``,
``prior_hash`` and ``event_hash``. JSON columns enter the event as parsed
values, so the hash does not depend on how the JSON text was spaced.
Triggers forbid UPDATE and DELETE on ``decision``; the chain additionally
shows if someone bypassed them (for example by dropping a trigger).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 13  # v2 source_target; v3 fetch_attempt; v4 time_start/time_end; v5 local_file route;
                     # v6 source.merged_into; v7 run.progress, run.heartbeat_at; v8 assessment;
                     # v9 usage.ban_position (the speaker's position on a ban, read-v5);
                     # v10 fingerprint, fingerprint_scan (identifiers in page code);
                     # v11 source.author (who wrote the text, archive/authors.py);
                     # v12 tag, asset (docs/TAGGING.md); decisions and assessments may target them;
                     # v13 concept scheme 'cluster' (the ontology's seven clusters, tagged since tag-v2)

# Column additions for databases created by an older schema.sql. New tables
# and indexes need no entry: schema.sql creates them IF NOT EXISTS.
# (version, table, column, definition)
MIGRATIONS = (
    (4, "section", "time_start", "REAL CHECK (time_start IS NULL OR time_start >= 0)"),
    (4, "section", "time_end", "REAL CHECK (time_end IS NULL OR time_end >= 0)"),
    (4, "passage", "time_start", "REAL CHECK (time_start IS NULL OR time_start >= 0)"),
    (4, "passage", "time_end", "REAL CHECK (time_end IS NULL OR time_end >= 0)"),
    (6, "source", "merged_into", "TEXT REFERENCES source(id)"),
    (7, "run", "progress", "TEXT CHECK (progress IS NULL OR json_valid(progress))"),
    (7, "run", "heartbeat_at", "TEXT"),
    (9, "usage", "ban_position", "TEXT CHECK (ban_position IS NULL OR ban_position IN ('supports', 'opposes', 'none'))"),
    (11, "source", "author", "TEXT"),
)
GENESIS_HASH = "0" * 64

# Columns that make up a decision event (hashed), in table order.
_EVENT_FIELDS = (
    "id", "created_at", "actor_kind", "actor_id", "run_id", "action",
    "target_table", "target_id", "reason", "quote", "audit_verdict",
    "previous_state", "new_state", "reverts_decision_id",
)
_JSON_FIELDS = ("previous_state", "new_state")
_APPEND_ONLY_TRIGGERS = ("decision_no_update", "decision_no_delete")


def utc_now() -> str:
    """Current UTC time as ISO 8601 with a trailing 'Z'."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no spaces, UTF-8 kept, no NaN."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database with foreign keys on and WAL journaling.

    Rows come back as ``sqlite3.Row``. ``":memory:"`` is accepted (WAL is then
    silently not used, which is fine for tests). ``check_same_thread=False``
    lets another thread close a worker's connection (each is still used by
    one thread only).
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def init_db(conn: sqlite3.Connection) -> int:
    """Apply schema.sql (idempotent) and record the schema version.

    Safe to call on every start: existing tables are left alone and dropped
    triggers or indexes are recreated. Returns the schema version.
    """
    sql = schema_sql()
    schema_sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if conn.in_transaction:
        conn.commit()
    _migrate(conn)
    conn.executescript(sql)  # every statement is CREATE ... IF NOT EXISTS
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at, schema_sha256) VALUES (?, ?, ?)",
            (SCHEMA_VERSION, utc_now(), schema_sha),
        )
    return current_schema_version(conn)


# Tables whose CHECK constraints changed: SQLite cannot alter a CHECK, so the
# table is rebuilt from schema.sql when its stored definition lacks the marker.
# (version, table, text the current definition contains)
REBUILDS = (
    (5, "fetch_attempt", "'local_file'"),
    (12, "decision", "'asset'"),
    (12, "assessment", "'asset'"),
    (13, "concept", "'cluster'"),
)


def _create_statement(table: str) -> str:
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", schema_sql(), re.DOTALL)
    if not match:
        raise RuntimeError(f"no CREATE TABLE for {table} in schema.sql")
    return match.group(0)


def _rebuild(conn: sqlite3.Connection, table: str) -> None:
    """Copy a table into its current definition (same columns), keeping every row."""
    columns = ", ".join(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
    create = _create_statement(table).replace(f"CREATE TABLE IF NOT EXISTS {table} (",
                                              f"CREATE TABLE {table}__new (", 1)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(create)
            conn.execute(f"INSERT INTO {table}__new ({columns}) SELECT {columns} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to schema.sql (before schema.sql runs)."""
    for _version, table, column, definition in MIGRATIONS:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns and column not in columns:  # table exists, column missing
            with conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for _version, table, marker in REBUILDS:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                           (table,)).fetchone()
        if row and marker not in row[0]:
            _rebuild(conn, table)


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def _json_text(value: Any) -> str | None:
    """Store JSON columns as canonical text; accept a dict/list or JSON text."""
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return canonical_json(value)


def _event(row: dict) -> dict:
    event = {field: row[field] for field in _EVENT_FIELDS}
    for field in _JSON_FIELDS:
        if event[field] is not None:
            event[field] = json.loads(event[field])
    return event


def event_hash(prior_hash: str, event: dict) -> str:
    """sha256 of prior_hash followed by the canonical JSON of the event."""
    payload = (prior_hash + canonical_json(event)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def chain_head(conn: sqlite3.Connection) -> str:
    """event_hash of the latest decision, or the genesis hash if none."""
    row = conn.execute("SELECT event_hash FROM decision ORDER BY seq DESC LIMIT 1").fetchone()
    return row[0] if row else GENESIS_HASH


def record_decision(
    conn: sqlite3.Connection,
    *,
    actor_kind: str,
    actor_id: str,
    action: str,
    target_table: str,
    target_id: str,
    reason: str,
    run_id: str | None = None,
    quote: str | None = None,
    audit_verdict: str = "not_audited",
    previous_state: Any = None,
    new_state: Any = None,
    reverts_decision_id: str | None = None,
    decision_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Append one decision to the chain and return the stored row as a dict.

    Enum values are checked by the schema (sqlite3.IntegrityError on a bad
    value). This only logs the decision; applying the change to the target
    record is the caller's job, ideally in the same transaction.

    If the connection is already inside a transaction the decision joins it
    and the caller commits; otherwise it runs in its own IMMEDIATE
    transaction, so two writers cannot fork the chain.
    """
    row = {
        "id": decision_id or f"dec_{uuid.uuid4().hex}",
        "created_at": created_at or utc_now(),
        "actor_kind": actor_kind,
        "actor_id": actor_id,
        "run_id": run_id,
        "action": action,
        "target_table": target_table,
        "target_id": target_id,
        "reason": reason,
        "quote": quote,
        "audit_verdict": audit_verdict,
        "previous_state": _json_text(previous_state),
        "new_state": _json_text(new_state),
        "reverts_decision_id": reverts_decision_id,
    }
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        prior = chain_head(conn)
        row["prior_hash"] = prior
        row["event_hash"] = event_hash(prior, _event(row))
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cursor = conn.execute(f"INSERT INTO decision({columns}) VALUES ({marks})", tuple(row.values()))
        row["seq"] = cursor.lastrowid
        if own_transaction:
            conn.commit()
    except BaseException:
        if own_transaction:
            conn.rollback()
        raise
    return row


class ChainReport(NamedTuple):
    ok: bool
    checked: int
    head_hash: str
    errors: tuple[str, ...]


def verify_decision_chain(conn: sqlite3.Connection, *, expected_head: str | None = None) -> ChainReport:
    """Recompute every hash in order and report breaks.

    Detects edited rows (hash mismatch), deleted or reordered rows (prior_hash
    not equal to the previous event_hash) and missing append-only triggers.
    Removing the newest rows leaves a valid shorter chain; pass
    ``expected_head`` (e.g. a release's decision_head_hash) to catch that.
    """
    errors: list[str] = []
    present = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'decision'")}
    for trigger in _APPEND_ONLY_TRIGGERS:
        if trigger not in present:
            errors.append(f"missing append-only trigger {trigger}")

    prior = GENESIS_HASH
    seen: set[str] = set()
    checked = 0
    for raw in conn.execute("SELECT * FROM decision ORDER BY seq"):
        row = dict(raw)
        checked += 1
        seq = row["seq"]
        if row["prior_hash"] != prior:
            errors.append(f"decision seq {seq}: prior_hash does not match previous event_hash "
                          "(a decision was removed or reordered)")
        try:
            expected = event_hash(row["prior_hash"], _event(row))
        except (ValueError, TypeError) as exc:
            errors.append(f"decision seq {seq}: cannot read event ({exc})")
            expected = None
        if expected is not None and expected != row["event_hash"]:
            errors.append(f"decision seq {seq}: event_hash does not match its content (row was edited)")
        seen.add(row["event_hash"])
        prior = row["event_hash"]

    if expected_head is not None and expected_head != GENESIS_HASH and expected_head not in seen:
        errors.append("expected head hash is not in the chain (decisions were removed)")
    return ChainReport(ok=not errors, checked=checked, head_hash=prior, errors=tuple(errors))
