# Assignment A — Interaction Ledger & Reporting Pipeline

## Candidate Details
- **Name:** Irfan Ahmed
- **Assignment:** A — Data
- **Time spent:** Approximately 7 hours

## 1. Running the Project

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Generate the sample data:

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

Ingest both drops:

```bash
python -m solution.pipeline ingest data/drop_1
python -m solution.pipeline ingest data/drop_2
```

Generate the reports:

```bash
python -m solution.pipeline report --out reports/
```

Run the tests:

```bash
python -m pytest solution
```

The application creates a local `.env` file for PII keys if they are not already configured. The file is git-ignored. The same keys must be retained when reopening an existing database.

Additional CLI options include `--db`, `--schemas`, and `--env-file`. Multiple drop directories can also be passed to a single `ingest` command.

## 2. Implementation Overview

I separated ingestion from reporting.

During ingestion, the application reads the input files, normalizes values, validates records, handles duplicates, and stores the results in SQLite.

During reporting, it links the stored events to leads and calculates the required metrics.

```text
Lead CSV files
      |
      v
Header mapping and validation
      |
      +------> Rejected rows
      |
      v
Create or merge leads
      |
      v
SQLite

Event JSONL files
      |
      v
Parse and deduplicate
      |
      v
Append-only event ledger

SQLite
   |
   v
Link events to leads
   |
   v
Calculate calls, dispositions and buckets
   |
   v
Generate five reports
```

### Database design

I used SQLite because it keeps the project easy to run without requiring a separate database service.

The main tables are:

- `leads`: Stores leads using `(workflow_id, phone_hash)` as the identity.
- `events`: Stores valid interaction events using `event_id` as the unique key.
- `ingested_files`: Tracks processed files and their ingestion counters.
- `rejections`: Stores rejected rows without personal information.
- `dispositions`: Stores the disposition catalogue.
- `meta`: Stores the PII key fingerprint.

The event table has database triggers that prevent updates and deletes.

### Lead processing

CSV headers are normalized and matched against schema field names and aliases. Unrecognized columns are ignored.

The pipeline validates phone numbers, dates, monetary values, and enum fields. Invalid required values result in rejected rows, while invalid optional values become null and contribute to the warning count.

Duplicate leads are identified using the workflow ID and normalized phone number. Later non-null values overwrite earlier values, while the original batch ID is retained.

### Event processing

Events are parsed from JSONL files. Malformed records and duplicate event IDs are counted and skipped.

I chose to store events before linking them to leads. This handles cases where an event arrives before its lead record. The relationship is resolved when reports are generated, so the same event can become linkable after a later drop is ingested.

Disposition overrides are associated with calls through `call_id`, without modifying the original event records.

### PII handling

Phone matching uses HMAC-SHA256 with a secret key. This avoids storing the complete phone number in plaintext while allowing consistent matching between lead and event records.

Customer names are encrypted using Fernet. Keys are loaded from environment variables or the local `.env` file.

The database also stores a key fingerprint to prevent accidental use of different keys with existing data.

## 3. Reporting

The pipeline generates the following five files:

| Report | Contents |
|---|---|
| `batch_summary.csv` | Lead counts grouped by workflow, batch and bucket |
| `daily_overview.csv` | Daily call volume, connected calls and success counts |
| `lead_report.csv` | Lead-level call history, bucket and disposition |
| `rejections.csv` | Rejected lead rows and validation reasons |
| `data_quality.json` | Ingestion counters and data quality metrics |

Event timestamps are stored in UTC. Reporting dates and timestamps use Asia/Kolkata time.

## 4. Important Design Decisions

| Decision | Reason |
|---|---|
| SQLite | Simple setup and no external database dependency |
| HMAC-SHA256 for phone matching | Prevents storing full phone numbers in plaintext |
| Report-time event linking | Supports events arriving before their leads |
| File content hashing | Prevents the same file from being ingested twice |
| Append-only event ledger | Preserves the original interaction history |
| Decimal for monetary values | Avoids floating-point rounding issues |
| First matching CSV column wins | Provides deterministic header mapping |
| Event ID as tie-breaker | Makes timestamp ties deterministic |

One observation from the supplied data was that the same phone number appeared in different formats, such as `91-86915-45226` and `08691545226`. Normalization was therefore important for identifying duplicate leads correctly.

## 5. Testing

The test suite contains **63 tests** covering:

- Phone number normalization and invalid formats.
- Date parsing and impossible dates.
- Currency prefixes, decimal parsing and comma placement.
- Header aliases and normalization.
- Required fields, optional-field warnings and rejected rows.
- Duplicate lead merging.
- Event deduplication and orphan handling.
- Disposition overrides and lead bucket classification.
- Timestamp conversion and timezone handling.
- End-to-end ingestion and report generation.
- Re-ingestion and append-only event constraints.

The end-to-end tests also compare the generated outputs when drops are processed separately and together.

**Latest reported test result: 63 passed.**

## 6. Results

For the seed-42 small dataset:

| Metric | Result |
|---|---:|
| Lead rows processed | 2,057 |
| Unique leads | 1,892 |
| Rejected rows | 108 |
| Duplicate lead rows merged | 57 |
| Field warnings | 122 |
| Event lines processed | 8,745 |
| Malformed events | 8 |
| Duplicate events dropped | 237 |
| Orphan events | 462 |
| Unknown disposition events | 18 |

Lead bucket distribution:

| Bucket | Count |
|---|---:|
| Completed | 816 |
| In progress — dialed | 526 |
| In progress — telephony failed | 38 |
| Yet to reach out | 512 |

I also checked the seed-42 reports against the expected output structure, including column order, sorting, and the data quality JSON keys.

## 7. Performance

Measurements on my Windows laptop using Python 3.13:

| Dataset | Operation | Time |
|---|---|---:|
| Small | First drop ingestion | 0.45 s |
| Small | Second drop ingestion | 0.28 s |
| Small | Report generation | 0.11 s |
| Large | Ingestion | 150 s |
| Large | Re-ingestion | 1 s |

Large dataset ingestion did not meet the two-minute stretch target.

The areas I would investigate first are repeated duplicate lookups and encryption overhead. I would also move more report aggregation into SQL rather than calculating everything in Python.

## 8. Limitations and Future Improvements

The current implementation does not handle simultaneous ingestion processes or key rotation.

Other improvements I would make with more time:

1. Optimize database writes and duplicate lookups.
2. Move report aggregation into SQL.
3. Add a CLI command to inspect previously ingested files.
4. Add a production deployment plan using PostgreSQL and a dedicated key manager.
5. Add monitoring for unusually high rejection and orphan-event counts.

Overall, the implementation prioritizes correct ingestion, repeatability, data privacy, and report consistency for the supplied assignment dataset.