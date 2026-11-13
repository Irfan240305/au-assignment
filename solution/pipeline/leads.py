"""Lead ingestion: validate, normalise, reject, merge, protect PII, store."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .normalize import build_header_map, normalize_phone, parse_date, parse_decimal, parse_enum
from .pii import PiiProtector


# ---------------------------------------------------------------- row validation (pure)

@dataclass
class RowResult:
    values: dict = field(default_factory=dict)   # field name -> clean value or None
    reason: str | None = None                    # rejection reason, None if valid
    warnings: int = 0                            # invalid optional fields

    @property
    def rejected(self) -> bool:
        return self.reason is not None


def _parse_value(raw: str, spec: dict):
    kind = spec.get("type", "string")
    if kind == "phone":
        return normalize_phone(raw)
    if kind == "date":
        return parse_date(raw)
    if kind == "decimal":
        return parse_decimal(raw)
    if kind == "enum":
        return parse_enum(raw, spec.get("values", []))
    return raw  # string (and any unknown type): the trimmed value


def validate_row(raw_values: dict[str, str | None], schema: dict) -> RowResult:
    """Walk fields in schema order. The first failure is the single reason.

    raw_values: field name -> trimmed raw string, or None if missing/empty.
    """
    result = RowResult()
    for spec in schema["fields"]:
        name = spec["name"]
        raw = raw_values.get(name)

        if raw is None:
            result.values[name] = None
            if spec.get("required") and result.reason is None:
                result.reason = f"missing_required:{name}"
            continue

        parsed = _parse_value(raw, spec)
        result.values[name] = parsed
        if parsed is not None:
            continue

        if spec.get("type") == "phone":
            if result.reason is None:
                result.reason = "invalid_phone"
        elif spec.get("required"):
            # Not defined by the spec (no current schema has it). Decision:
            # a required non-phone field that is present but invalid rejects the row.
            if result.reason is None:
                result.reason = f"invalid_required:{name}"
        else:
            result.warnings += 1

    if result.rejected:
        result.warnings = 0  # warnings only count for rows that are not rejected
    return result


def read_raw_rows(text: str, schema: dict):
    """Yield (row_number, {field: trimmed value or None}) for non-blank rows."""
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader, None)
    if headers is None:
        return
    column_of = build_header_map(headers, schema["fields"])

    row_number = 0
    for cells in reader:
        if all(cell.strip() == "" for cell in cells):
            continue  # blank row: skipped and not counted
        row_number += 1
        raw = {}
        for name, index in column_of.items():
            value = cells[index].strip() if index < len(cells) else ""
            raw[name] = value or None
        yield row_number, raw


# ---------------------------------------------------------------- storage

class LeadIngestor:
    def __init__(self, conn: sqlite3.Connection, schemas: dict[str, dict], pii: PiiProtector):
        self.conn = conn
        self.schemas = schemas
        self.pii = pii

    def ingest_drop(self, drop_dir: Path, ingest_seq_start: int) -> int:
        """Ingest every lead file of one drop. Returns the next free ingest_seq."""
        seq = ingest_seq_start
        leads_root = drop_dir / "leads"
        if not leads_root.is_dir():
            return seq
        for workflow_dir in sorted(p for p in leads_root.iterdir() if p.is_dir()):
            schema = self.schemas.get(workflow_dir.name)
            if schema is None:
                print(f"[leads] skipping {workflow_dir}: no schema for workflow '{workflow_dir.name}'")
                continue
            for csv_path in sorted(workflow_dir.glob("*.csv"), key=lambda p: p.name):
                if self.ingest_file(csv_path, schema, seq):
                    seq += 1
        return seq

    def ingest_file(self, path: Path, schema: dict, ingest_seq: int) -> bool:
        """Ingest one CSV in a single transaction. Returns False if already ingested."""
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if self.conn.execute("SELECT 1 FROM ingested_files WHERE file_sha256 = ?", (sha,)).fetchone():
            print(f"[leads] already ingested, skipping: {path}")
            return False

        workflow_id = schema["workflow_id"]
        batch_id = path.stem
        counters = {"lead_rows_total": 0, "lead_rows_rejected": 0,
                    "duplicate_lead_rows_merged": 0, "field_warnings": 0}
        rejections = []

        self.conn.execute("BEGIN")
        try:
            # Record the file first so rejections can reference it (FK).
            self.conn.execute(
                """INSERT INTO ingested_files (file_sha256, kind, workflow_id, source_path,
                                               ingest_seq, ingested_at)
                   VALUES (?, 'leads', ?, ?, ?, ?)""",
                (sha, workflow_id, str(path), ingest_seq, datetime.now(timezone.utc).isoformat()),
            )
            text = content.decode("utf-8-sig")
            for row_number, raw in read_raw_rows(text, schema):
                counters["lead_rows_total"] += 1
                result = validate_row(raw, schema)
                if result.rejected:
                    counters["lead_rows_rejected"] += 1
                    rejections.append((sha, workflow_id, batch_id, row_number, result.reason))
                    continue
                counters["field_warnings"] += result.warnings
                if self._upsert_lead(schema, batch_id, result.values, ingest_seq):
                    counters["duplicate_lead_rows_merged"] += 1

            self.conn.executemany(
                """INSERT INTO rejections (file_sha256, workflow_id, batch_id, row_number, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                rejections,
            )
            self.conn.execute(
                """UPDATE ingested_files SET lead_rows_total = ?, lead_rows_rejected = ?,
                          duplicate_lead_rows_merged = ?, field_warnings = ?
                   WHERE file_sha256 = ?""",
                (counters["lead_rows_total"], counters["lead_rows_rejected"],
                 counters["duplicate_lead_rows_merged"], counters["field_warnings"], sha),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")  # crash mid-file -> nothing saved, safe to re-run
            raise

        print(f"[leads] {path}: {counters}")
        return True

    def _upsert_lead(self, schema: dict, batch_id: str, values: dict, ingest_seq: int) -> bool:
        """Create or merge a lead. Returns True if it was a merge (duplicate)."""
        workflow_id = schema["workflow_id"]
        phone_field = next(f["name"] for f in schema["fields"] if f.get("type") == "phone")
        ref_field = next(f["name"] for f in schema["fields"] if f.get("reference"))
        pii_fields = [f["name"] for f in schema["fields"] if f.get("pii") and f["name"] != phone_field]
        plain_fields = [f["name"] for f in schema["fields"] if not f.get("pii")]

        phone = values[phone_field]
        phone_hash = self.pii.hash_phone(phone)
        new_pii = {name: values[name] for name in pii_fields}
        new_attrs = {name: values[name] for name in plain_fields}

        existing = self.conn.execute(
            "SELECT attributes, pii_encrypted FROM leads WHERE workflow_id = ? AND phone_hash = ?",
            (workflow_id, phone_hash),
        ).fetchone()

        if existing is None:
            self.conn.execute(
                """INSERT INTO leads (workflow_id, phone_hash, phone_last4, batch_id, external_ref,
                                      pii_encrypted, attributes, created_seq)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (workflow_id, phone_hash, phone[-4:], batch_id, new_attrs[ref_field],
                 self.pii.encrypt_json(new_pii), json.dumps(new_attrs, sort_keys=True), ingest_seq),
            )
            return False

        # Merge: later non-null values overwrite (last write wins). batch_id never changes.
        attrs = json.loads(existing["attributes"])
        attrs.update({k: v for k, v in new_attrs.items() if v is not None})
        pii_values = self.pii.decrypt_json(existing["pii_encrypted"])
        pii_values.update({k: v for k, v in new_pii.items() if v is not None})
        self.conn.execute(
            """UPDATE leads SET attributes = ?, pii_encrypted = ?, external_ref = ?
               WHERE workflow_id = ? AND phone_hash = ?""",
            (json.dumps(attrs, sort_keys=True), self.pii.encrypt_json(pii_values),
             attrs[ref_field], workflow_id, phone_hash),
        )
        return True

    def apply_defaults(self) -> None:
        """Defaults apply after merging, to fields that are still null."""
        self.conn.execute("BEGIN")
        for workflow_id, schema in self.schemas.items():
            defaults = {f["name"]: f["default"] for f in schema["fields"]
                        if "default" in f and not f.get("pii")}
            if not defaults:
                continue
            rows = self.conn.execute(
                "SELECT phone_hash, attributes FROM leads WHERE workflow_id = ?", (workflow_id,)
            ).fetchall()
            for row in rows:
                attrs = json.loads(row["attributes"])
                changed = False
                for name, default in defaults.items():
                    if attrs.get(name) is None:
                        attrs[name] = default
                        changed = True
                if changed:
                    self.conn.execute(
                        "UPDATE leads SET attributes = ? WHERE workflow_id = ? AND phone_hash = ?",
                        (json.dumps(attrs, sort_keys=True), workflow_id, row["phone_hash"]),
                    )
        self.conn.execute("COMMIT")