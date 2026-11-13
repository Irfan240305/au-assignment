# AI Usage Disclosure

## 1. AI Tools Used

- **Claude (Anthropic):** Used during assignment planning, implementation, debugging, and documentation.
- **ChatGPT (OpenAI):** Used for an independent review of the implementation, identifying potential bugs, and checking edge cases.

## 2. How I Used AI

I used Claude throughout the development process to understand the assignment requirements, plan the implementation, troubleshoot setup issues, and develop the solution.

Claude generated the initial implementation files under `solution/pipeline/` and `solution/tests/`, along with first drafts of `SUBMISSION.md` and this disclosure. I reviewed and edited the generated material.

I used ChatGPT separately to review the implementation and identify potential correctness and privacy issues.

## 3. My Contributions and Decisions

The following were decisions and activities I handled during the assignment:

- Selected Assignment A after reviewing both assignment options.
- Examined the supplied input data and identified variations in phone number and date formats.
- Planned the implementation stages and checked the plan against the assignment requirements.
- Integrated the generated modules into the project and worked through the implementation one component at a time.
- Ran individual tests and the complete test suite during development.
- Investigated test failures and reviewed the generated code rather than accepting the initial output without verification.
- Ran the supplied small and large datasets and recorded the performance measurements.
- Reviewed the generated reports against the expected output structure and example values.
- Made implementation decisions around SQLite, HMAC-based phone matching, event linking, idempotency, and the append-only ledger.

## 4. Issues Identified During AI-Assisted Development

AI-generated suggestions were not always correct. Some examples:

- The initial implementation plan missed several requirements, including PII protection, idempotency, append-only event storage, testing, and documentation. I revisited the requirements and expanded the plan.
- Initial setup instructions resulted in the repository being cloned inside another folder in OneDrive. I identified the issue and corrected the project location.
- A suggested method for identifying malformed event lines was ineffective because the malformed records also started with `{`. I inspected the actual records and adjusted the approach.
- An initial test command did not work correctly in PowerShell because of escaping issues.
- During test review, I identified weak assertions, including a PII test that did not account for the SQLite WAL file. These were corrected.
- The initial decimal parser removed commas without validating their placement. This could incorrectly convert `1,2,3` into `123.00`. I added comma-format validation and corresponding tests.
- The independent review also raised a question about storing the last four phone digits in plaintext. This was identified as a privacy consideration requiring review against the assignment's PII requirements.

## 5. How I Verified the Solution

I used the following checks to validate the implementation:

- Ran tests during development and the complete test suite.
- Generated the seed-42 dataset and compared report examples against the assignment README.
- Checked the five generated reports, including their headers, column order, sorting, and data quality JSON structure.
- Tested repeated ingestion to verify that previously processed files were skipped.
- Compared end-to-end outputs when drops were processed separately and together.
- Reviewed database constraints and triggers to verify that interaction events could not be updated or deleted.
- Reviewed the implementation in preparation for explaining its design decisions and limitations during a technical discussion.

**I used AI as a development and review aid. I remain responsible for the implementation, testing, technical decisions, and final submission.**