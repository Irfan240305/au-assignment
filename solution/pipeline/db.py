"""Database connection, schema creation and reference-data loading."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from .pii import PiiKeys

SCHEMA_SQL = Path(__file__).with_name("schema.sql")
DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "assignment-a-data" / "schemas"


class KeyMismatchError(RuntimeError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None: we control transactions explicitly with BEGIN/COMMIT.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def check_key_fingerprint(conn: sqlite3.Connection, keys: PiiKeys) -> None:
    """Refuse to run with different keys than the existing data used."""
    fingerprint = keys.fingerprint()
    row = conn.execute("SELECT value FROM meta WHERE key = 'pii_key_fingerprint'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta (key, value) VALUES ('pii_key_fingerprint', ?)",
                     (fingerprint,))
    elif row["value"] != fingerprint:
        raise KeyMismatchError(
            "PII keys differ from the ones this database was created with. "
            "Phone hashes would no longer match. Use the original keys or a new --db."
        )


def load_workflow_schemas(schemas_dir: Path) -> dict[str, dict]:
    schemas = {}
    for path in sorted(schemas_dir.glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[schema["workflow_id"]] = schema
    return schemas


def load_dispositions(conn: sqlite3.Connection, schemas_dir: Path) -> None:
    """(Re)load the catalogue. Idempotent: upserts by (workflow_id, code)."""
    path = schemas_dir / "dispositions.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            (r["workflow_id"].strip(), r["code"].strip(), r.get("label", "").strip(),
             int(r["is_terminal"].strip().lower() == "true"),
             int(r["is_success"].strip().lower() == "true"))
            for r in csv.DictReader(handle)
        ]
    conn.execute("BEGIN")
    conn.executemany(
        """INSERT INTO dispositions (workflow_id, code, label, is_terminal, is_success)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (workflow_id, code) DO UPDATE SET
               label = excluded.label,
               is_terminal = excluded.is_terminal,
               is_success = excluded.is_success""",
        rows,
    )
    conn.execute("COMMIT")


def next_ingest_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) + 1 AS seq FROM ingested_files").fetchone()
    return row["seq"]