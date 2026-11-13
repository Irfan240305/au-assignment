"""CLI.

    python -m solution.pipeline ingest data/drop_1 [data/drop_2 ...]
    python -m solution.pipeline report --out reports/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .db import (DEFAULT_SCHEMAS_DIR, KeyMismatchError, check_key_fingerprint, connect,
                 load_dispositions, load_workflow_schemas, next_ingest_seq)
from .events import EventIngestor
from .leads import LeadIngestor
from .pii import PiiProtector, load_keys
from .reports import generate_reports


def _open(args):
    keys = load_keys(Path(args.env_file))
    conn = connect(Path(args.db))
    check_key_fingerprint(conn, keys)
    schemas_dir = Path(args.schemas)
    load_dispositions(conn, schemas_dir)
    return conn, PiiProtector(keys), load_workflow_schemas(schemas_dir)


def cmd_ingest(args) -> None:
    conn, pii, schemas = _open(args)
    lead_ingestor = LeadIngestor(conn, schemas, pii)
    event_ingestor = EventIngestor(conn, pii)

    for drop in args.drops:  # drops are processed in the order given
        drop_dir = Path(drop)
        if not drop_dir.is_dir():
            sys.exit(f"Drop folder not found: {drop_dir}")
        seq = next_ingest_seq(conn)
        seq = lead_ingestor.ingest_drop(drop_dir, seq)
        event_ingestor.ingest_drop(drop_dir, seq)

    lead_ingestor.apply_defaults()
    conn.close()


def cmd_report(args) -> None:
    conn, _, _ = _open(args)
    quality = generate_reports(conn, Path(args.out))
    conn.close()
    print(f"[report] wrote 5 files to {args.out}")
    print(f"[report] orphans={quality['orphan_events']} unknown_dispositions={quality['unknown_disposition_events']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m solution.pipeline",
                                     description="Interaction ledger & reporting pipeline")
    parser.add_argument("--db", default="au.db", help="SQLite database file (default: au.db)")
    parser.add_argument("--schemas", default=str(DEFAULT_SCHEMAS_DIR), help="schemas folder")
    parser.add_argument("--env-file", default=".env", help="file holding PII keys (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest one or more drop folders, in order")
    ingest.add_argument("drops", nargs="+")
    ingest.set_defaults(func=cmd_ingest)

    report = sub.add_parser("report", help="write the 5 report files")
    report.add_argument("--out", required=True)
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        args.func(args)
    except KeyMismatchError as error:
        sys.exit(f"Error: {error}")
    print(f"[done] {args.command} in {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()