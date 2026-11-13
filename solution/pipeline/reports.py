"""Report builder: link the ledger to leads and derive every metric.

Everything here is recomputed from the database on each run, so the output
always reflects everything ingested so far, regardless of drop order.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .normalize import from_utc_string, ist_date, to_ist_iso
from .pii import mask_phone

LeadKey = tuple[str, str]  # (workflow_id, phone_hash)

OVERRIDE = "disposition.override"
DISPOSITION_TYPES = {"disposition.set", OVERRIDE}


# ---------------------------------------------------------------- data shapes

@dataclass
class Event:
    event_id: str
    event_type: str | None
    workflow_id: str | None
    call_id: str | None
    occurred_at: str | None  # ISO UTC string; sorts correctly as text
    phone_hash: str | None
    disposition_code: str | None

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.occurred_at or "", self.event_id)


@dataclass
class Call:
    call_id: str
    lead: LeadKey
    initiated_at: datetime
    connected: bool
    telephony_failed: bool
    disposition: str | None


@dataclass
class Catalogue:
    known: set[tuple[str, str]] = field(default_factory=set)
    terminal: set[tuple[str, str]] = field(default_factory=set)
    success: set[tuple[str, str]] = field(default_factory=set)


# ---------------------------------------------------------------- pure logic

def link_events(events: list[Event], lead_keys: set[LeadKey]) -> tuple[list[tuple[Event, LeadKey]], int]:
    """Attach each event to a lead. Returns (linked, orphan_count).

    Normal events link by (workflow_id, phone_hash). Overrides have no phone and
    link through call_id to the lead of that call's other (linked) events.
    """
    ordered = sorted(events, key=lambda e: e.sort_key)
    linked: list[tuple[Event, LeadKey]] = []
    call_lead: dict[str, LeadKey] = {}
    orphans = 0

    for event in ordered:
        if event.event_type == OVERRIDE:
            continue
        key = (event.workflow_id, event.phone_hash)
        if event.phone_hash and key in lead_keys:
            linked.append((event, key))
            if event.call_id is not None:
                call_lead.setdefault(event.call_id, key)
        else:
            orphans += 1

    for event in ordered:
        if event.event_type != OVERRIDE:
            continue
        key = call_lead.get(event.call_id) if event.call_id is not None else None
        if key is None:
            orphans += 1
        else:
            linked.append((event, key))

    return linked, orphans


def effective_disposition(disposition_events: list[Event]) -> str | None:
    """Latest override by occurred_at; else latest set; else None.

    Expects only events whose code is known. Ties on occurred_at are broken by
    event_id so the result is deterministic.
    """
    overrides = [e for e in disposition_events if e.event_type == OVERRIDE]
    if overrides:
        return max(overrides, key=lambda e: e.sort_key).disposition_code
    sets = [e for e in disposition_events if e.event_type == "disposition.set"]
    if sets:
        return max(sets, key=lambda e: e.sort_key).disposition_code
    return None


def classify_bucket(calls: list[Call], workflow_id: str, catalogue: Catalogue) -> str:
    """First match wins."""
    if not calls:
        return "yet_to_reach_out"
    if any(c.disposition and (workflow_id, c.disposition) in catalogue.terminal for c in calls):
        return "completed"
    if all(c.telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def build_calls(linked: list[tuple[Event, LeadKey]], catalogue: Catalogue) -> tuple[list[Call], int]:
    """Group linked events into calls. Returns (calls, unknown_disposition_count)."""
    by_call: dict[str, list[Event]] = defaultdict(list)
    lead_of_call: dict[str, LeadKey] = {}
    unknown = 0

    for event, lead in linked:
        if event.event_type in DISPOSITION_TYPES and (lead[0], event.disposition_code) not in catalogue.known:
            unknown += 1
            continue  # counted and ignored
        if event.call_id is None:
            continue
        by_call[event.call_id].append(event)
        lead_of_call.setdefault(event.call_id, lead)

    calls = []
    for call_id, events in by_call.items():
        initiated = [e for e in events if e.event_type == "call.initiated" and e.occurred_at]
        if not initiated:
            continue  # a call is counted only if its call.initiated is present
        first = min(initiated, key=lambda e: e.sort_key)
        types = {e.event_type for e in events}
        calls.append(Call(
            call_id=call_id,
            lead=lead_of_call[call_id],
            initiated_at=from_utc_string(first.occurred_at),
            connected="call.connected" in types,
            telephony_failed="call.telephony_failed" in types,
            disposition=effective_disposition([e for e in events if e.event_type in DISPOSITION_TYPES]),
        ))
    return calls, unknown


# ---------------------------------------------------------------- report generation

def _load(conn: sqlite3.Connection):
    leads = {
        (r["workflow_id"], r["phone_hash"]): r
        for r in conn.execute("SELECT workflow_id, phone_hash, phone_last4, batch_id, external_ref FROM leads")
    }
    events = [
        Event(r["event_id"], r["event_type"], r["workflow_id"], r["call_id"],
              r["occurred_at_utc"], r["phone_hash"], r["disposition_code"])
        for r in conn.execute(
            """SELECT event_id, event_type, workflow_id, call_id, occurred_at_utc,
                      phone_hash, disposition_code FROM events""")
    ]
    catalogue = Catalogue()
    for r in conn.execute("SELECT workflow_id, code, is_terminal, is_success FROM dispositions"):
        key = (r["workflow_id"], r["code"])
        catalogue.known.add(key)
        if r["is_terminal"]:
            catalogue.terminal.add(key)
        if r["is_success"]:
            catalogue.success.add(key)
    return leads, events, catalogue


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _bool(value: bool) -> str:
    return "true" if value else "false"


def generate_reports(conn: sqlite3.Connection, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    leads, events, catalogue = _load(conn)

    linked, orphan_count = link_events(events, set(leads))
    calls, unknown_count = build_calls(linked, catalogue)

    calls_by_lead: dict[LeadKey, list[Call]] = defaultdict(list)
    for call in calls:
        calls_by_lead[call.lead].append(call)

    def is_success(workflow_id: str, code: str | None) -> bool:
        return code is not None and (workflow_id, code) in catalogue.success

    # ---- lead_report.csv + per-lead buckets
    lead_rows = []
    bucket_of: dict[LeadKey, str] = {}
    for key, lead in leads.items():
        workflow_id = key[0]
        lead_calls = sorted(calls_by_lead.get(key, []), key=lambda c: (c.initiated_at, c.call_id))
        bucket = classify_bucket(lead_calls, workflow_id, catalogue)
        bucket_of[key] = bucket
        with_disposition = [c for c in lead_calls if c.disposition]
        lead_rows.append([
            workflow_id,
            lead["external_ref"],
            lead["batch_id"],
            mask_phone(lead["phone_last4"]),
            bucket,
            len(lead_calls),
            sum(c.connected for c in lead_calls),
            to_ist_iso(lead_calls[-1].initiated_at) if lead_calls else "",
            with_disposition[-1].disposition if with_disposition else "",
            _bool(any(is_success(workflow_id, c.disposition) for c in lead_calls)),
        ])
    lead_rows.sort(key=lambda r: (r[0], r[1]))
    _write_csv(out_dir / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"],
               lead_rows)

    # ---- batch_summary.csv
    bucket_names = ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]
    batches: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(bucket_names, 0))
    for key, lead in leads.items():
        batches[(key[0], lead["batch_id"])][bucket_of[key]] += 1
    batch_rows = [
        [wf, batch, sum(counts.values()), *(counts[b] for b in bucket_names)]
        for (wf, batch), counts in sorted(batches.items())
    ]
    _write_csv(out_dir / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *bucket_names], batch_rows)

    # ---- daily_overview.csv
    daily: dict[tuple[str, str], dict] = defaultdict(lambda: {"total": 0, "connected": 0, "success": set()})
    for call in calls:
        workflow_id = call.lead[0]
        day = daily[(workflow_id, ist_date(call.initiated_at))]
        day["total"] += 1
        day["connected"] += call.connected
        if is_success(workflow_id, call.disposition):
            day["success"].add(call.lead)
    daily_rows = [
        [wf, date, d["total"], d["connected"], f"{d['connected'] / d['total']:.4f}", len(d["success"])]
        for (wf, date), d in sorted(daily.items())
    ]
    _write_csv(out_dir / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               daily_rows)

    # ---- rejections.csv
    rejection_rows = [
        [r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]]
        for r in conn.execute(
            "SELECT workflow_id, batch_id, row_number, reason FROM rejections "
            "ORDER BY workflow_id, batch_id, row_number")
    ]
    _write_csv(out_dir / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"], rejection_rows)

    # ---- data_quality.json
    totals = conn.execute(
        """SELECT COALESCE(SUM(lead_rows_total), 0)            AS lead_rows_total,
                  COALESCE(SUM(lead_rows_rejected), 0)         AS lead_rows_rejected,
                  COALESCE(SUM(duplicate_lead_rows_merged), 0) AS duplicate_lead_rows_merged,
                  COALESCE(SUM(field_warnings), 0)             AS field_warnings,
                  COALESCE(SUM(events_total), 0)               AS events_total,
                  COALESCE(SUM(malformed_events), 0)           AS malformed_events,
                  COALESCE(SUM(duplicate_events_dropped), 0)   AS duplicate_events_dropped
           FROM ingested_files""").fetchone()
    by_reason = {
        r["reason"]: r["n"]
        for r in conn.execute("SELECT reason, COUNT(*) AS n FROM rejections GROUP BY reason ORDER BY reason")
    }
    quality = {
        "lead_rows_total": totals["lead_rows_total"],
        "lead_rows_rejected": totals["lead_rows_rejected"],
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": totals["duplicate_lead_rows_merged"],
        "field_warnings": totals["field_warnings"],
        "events_total": totals["events_total"],
        "malformed_events": totals["malformed_events"],
        "duplicate_events_dropped": totals["duplicate_events_dropped"],
        "orphan_events": orphan_count,
        "unknown_disposition_events": unknown_count,
    }
    (out_dir / "data_quality.json").write_text(json.dumps(quality, indent=2) + "\n", encoding="utf-8")
    return quality