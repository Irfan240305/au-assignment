"""Event ingestion into the append-only interaction ledger.

Ingest only filters what is certainly unusable (malformed lines, duplicate
event_ids). Linking to leads, orphans and unknown dispositions are decided at
report time, because a lead may arrive in a later drop than its events.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .normalize import normalize_phone, parse_timestamp, to_utc_string
from .pii import PiiProtector


def parse_event_line(line: str) -> dict | None:
    """Return the event dict, or None if malformed (bad JSON / no event_id)."""
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    event_id = event.get("event_id")
    if event_id is None or str(event_id).strip() == "":
        return None
    return event


class EventIngestor:
    def __init__(self, conn: sqlite3.Connection, pii: PiiProtector):
        self.conn = conn
        self.pii = pii

    def ingest_drop(self, drop_dir: Path, ingest_seq_start: int) -> int:
        seq = ingest_seq_start
        events_dir = drop_dir / "events"
        if not events_dir.is_dir():
            return seq
        for path in sorted(events_dir.glob("*.jsonl"), key=lambda p: p.name):
            if self.ingest_file(path, seq):
                seq += 1
        return seq

    def ingest_file(self, path: Path, ingest_seq: int) -> bool:
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if self.conn.execute("SELECT 1 FROM ingested_files WHERE file_sha256 = ?", (sha,)).fetchone():
            print(f"[events] already ingested, skipping: {path}")
            return False

        counters = {"events_total": 0, "malformed_events": 0, "duplicate_events_dropped": 0}
        seen_in_file: set[str] = set()
        rows = []

        for line_number, line in enumerate(content.decode("utf-8-sig", errors="replace").splitlines(), 1):
            if not line.strip():
                continue  # empty lines are not counted
            counters["events_total"] += 1

            event = parse_event_line(line)
            if event is None:
                counters["malformed_events"] += 1
                continue

            event_id = str(event["event_id"])
            if event_id in seen_in_file or self._event_exists(event_id):
                counters["duplicate_events_dropped"] += 1
                continue
            seen_in_file.add(event_id)
            rows.append(self._to_row(event, event_id, sha, line_number))

        self.conn.execute("BEGIN")
        try:
            self.conn.execute(
                """INSERT INTO ingested_files (file_sha256, kind, source_path, ingest_seq, ingested_at,
                                               events_total, malformed_events, duplicate_events_dropped)
                   VALUES (?, 'events', ?, ?, ?, ?, ?, ?)""",
                (sha, str(path), ingest_seq, datetime.now(timezone.utc).isoformat(),
                 counters["events_total"], counters["malformed_events"],
                 counters["duplicate_events_dropped"]),
            )
            self.conn.executemany(
                """INSERT INTO events (event_id, event_type, workflow_id, call_id, occurred_at_utc,
                                       phone_hash, disposition_code, payload, file_sha256, line_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        print(f"[events] {path}: {counters}, stored {len(rows)}")
        return True

    def _event_exists(self, event_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone() is not None

    def _to_row(self, event: dict, event_id: str, sha: str, line_number: int) -> tuple:
        phone = normalize_phone(event.get("phone")) if isinstance(event.get("phone"), str) else None
        occurred = parse_timestamp(event.get("occurred_at"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload = {k: v for k, v in payload.items() if k != "phone"}  # never persist raw phones
        code = payload.get("disposition_code")
        return (
            event_id,
            event.get("event_type"),
            event.get("workflow_id"),
            None if event.get("call_id") is None else str(event.get("call_id")),
            to_utc_string(occurred) if occurred else None,
            self.pii.hash_phone(phone) if phone else None,
            None if code is None else str(code),
            json.dumps(payload, sort_keys=True),
            sha,
            line_number,
        )