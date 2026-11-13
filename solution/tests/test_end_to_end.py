"""Runs the whole pipeline on freshly generated data (seed 7)."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from solution.pipeline.__main__ import main

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "assignment-a-data" / "tools" / "generate_data.py"
FILES = ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    subprocess.run([sys.executable, str(GENERATOR), "--seed", "7", "--scale", "small", "--out", str(out)],
                   check=True, capture_output=True)
    return out


def run(tmp, name, *args):
    main(["--db", str(tmp / f"{name}.db"), "--env-file", str(tmp / ".env"), *args])


def read_all(folder: Path) -> dict[str, str]:
    return {f: (folder / f).read_text(encoding="utf-8") for f in FILES}


def test_incremental_rerun_and_all_at_once_match(data_dir, tmp_path):
    d1, d2 = str(data_dir / "drop_1"), str(data_dir / "drop_2")

    run(tmp_path, "inc", "ingest", d1)
    run(tmp_path, "inc", "ingest", d2)
    run(tmp_path, "inc", "report", "--out", str(tmp_path / "inc"))

    run(tmp_path, "inc", "ingest", d1, d2)  # re-ingest everything: must change nothing
    run(tmp_path, "inc", "report", "--out", str(tmp_path / "rerun"))

    run(tmp_path, "all", "ingest", d1, d2)  # fresh DB, both drops in one go
    run(tmp_path, "all", "report", "--out", str(tmp_path / "all"))

    baseline = read_all(tmp_path / "inc")
    assert read_all(tmp_path / "rerun") == baseline
    assert read_all(tmp_path / "all") == baseline


def test_consistency_invariants_and_late_linking(data_dir, tmp_path):
    run(tmp_path, "db", "ingest", str(data_dir / "drop_1"))
    run(tmp_path, "db", "report", "--out", str(tmp_path / "mid"))
    run(tmp_path, "db", "ingest", str(data_dir / "drop_2"))
    run(tmp_path, "db", "report", "--out", str(tmp_path / "final"))

    mid = json.loads((tmp_path / "mid" / "data_quality.json").read_text())
    q = json.loads((tmp_path / "final" / "data_quality.json").read_text())
    lead_rows = (tmp_path / "final" / "lead_report.csv").read_text().strip().splitlines()[1:]

    # every lead row is exactly one of: a lead, a rejection, a merge
    assert len(lead_rows) + q["lead_rows_rejected"] + q["duplicate_lead_rows_merged"] == q["lead_rows_total"]
    assert sum(q["rejections_by_reason"].values()) == q["lead_rows_rejected"]

    conn = sqlite3.connect(tmp_path / "db.db")
    stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert q["events_total"] == q["malformed_events"] + q["duplicate_events_dropped"] + stored
    # events that were orphans after drop 1 got linked once their leads arrived
    assert q["orphan_events"] < mid["orphan_events"]
    conn.close()


def test_pii_not_stored_in_plaintext(data_dir, tmp_path):
    run(tmp_path, "pii", "ingest", str(data_dir / "drop_1"))
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("pii.db*"))  # include WAL/SHM files
    first_lead = (data_dir / "drop_1" / "leads" / "motor_renewal" / "batch_001.csv").read_text(
        encoding="utf-8-sig").splitlines()[1].split(",")
    name, phone = first_lead[0], first_lead[1]
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    assert name.encode() not in raw
    assert digits.encode() not in raw


def test_ledger_is_append_only(data_dir, tmp_path):
    run(tmp_path, "ledger", "ingest", str(data_dir / "drop_1"))
    conn = sqlite3.connect(tmp_path / "ledger.db")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET event_type = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events")
    conn.close()