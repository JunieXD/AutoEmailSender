---
name: update-crawl-benchmark
description: Safely merge one or more computers' local Auto Email Sender crawl databases into the public website benchmark JSON, normalize confirmed school aliases, verify privacy and data invariants, and run backend and website checks. Use when the user asks to update, upload, refresh, merge, or validate the official 智能抓取效果展示 data from a local auto_email_sender.db or the historical XLSX.
---

# Update Crawl Benchmark Data

## Overview

Update `website/data/crawl-benchmark.json` from a local Auto Email Sender database without exposing mentor-level data. Preserve records published by other computers, update the same crawl task in place after later enrichment, and leave commit and push decisions to the user unless they explicitly request them.

## Safety Rules

- Treat `auto_email_sender.db` as read-only. Never migrate it, edit it, copy it into the repository, or commit it.
- Publish only the aggregate fields produced by `scripts/data/update_crawl_benchmark.py`. Never publish names, emails, API keys, error details, logs, prompts, or raw database rows.
- Preserve unrelated working-tree changes.
- Do not guess a school's canonical name. Apply an alias only when the canonical school and institution are supported by the source URL or confirmed by the user.
- Do not commit, push, open a pull request, or deploy unless the user explicitly requests that action.

## Workflow

1. Inspect the repository status and identify the requested database path. If none is supplied, let the update script use the platform's standard desktop-app data directory.
2. Before generating, confirm the current branch contains the newest remote `website/data/crawl-benchmark.json`. Fetching for comparison is allowed. If the local file is stale, integrate the remote change safely while preserving user edits, then continue.
3. Check `config/crawl-benchmark-aliases.json`. Add only confirmed mappings. Report ambiguous new names instead of silently guessing.
4. From `backend/`, run:

   ```bash
   uv run python ../scripts/data/update_crawl_benchmark.py [--database /absolute/path/to/auto_email_sender.db] [--legacy-xlsx /absolute/path/to/history.xlsx]
   ```

   Import the historical workbook only when requested or on its first migration. The script reads the database in SQLite read-only mode and merges the current machine's records over existing records with the same stable ID.
5. Inspect the generated summary and JSON invariants:

   - `schemaVersion` is `2`.
   - `recordId` values are unique.
   - Existing records from other computers remain present.
   - Known aliases are normalized and uncertain names are reported.
   - No email address, API key, mentor name, log, or database path leaked.
   - All candidate mentors are the enrichment denominator. `enrichmentSucceededCount / candidateCount` is the public progress; candidates without an enrichment task still count toward the denominator.

6. Run the focused verification:

   ```bash
   cd backend
   uv run python -m unittest test.test_crawl_benchmark_publication -v
   cd ../website
   npm run test
   npm run build
   ```

7. Summarize changed record counts, target counts, enrichment examples, alias decisions, and verification results. Stop before any Git publishing action unless explicitly authorized.

## Merge and Conflict Policy

- Schema 2 records are merged by stable ID. A later enrichment run for the same crawl job replaces that job's previous public aggregate rather than adding a duplicate. Import into the mentor library is irrelevant.
- Different computers can reuse local numeric job IDs. The stable ID also incorporates source URL and creation time, so independently created jobs remain separate.
- The existing public JSON is part of the input, not disposable generated output. Never regenerate from only the current machine when a newer remote JSON exists.
- If a push is rejected because another computer published first, update from the remote branch and rerun the publication script against the now-current public JSON. Do not resolve the data conflict by keeping only one side. The local database can regenerate this computer's aggregates after the remote JSON becomes the base.
- Schema 1 database records are not safe to preserve across computers. For the first Schema 2 upgrade, rebuild on the computer holding the primary crawl history; then let other computers merge into that Schema 2 file.

## Name Normalization

- Put university aliases in `universityAliases`.
- Put institution aliases under the canonical university in `schoolAliases`.
- Use full official names, for example `中科院` to `中国科学院大学`, only when the record's source has been verified.
- Rerun the update script after changing aliases; it normalizes both local database records and retained public records without modifying the database.

## Completion Criteria

- The public JSON contains the union of the newest remote data and the current local database.
- A repeated update after later partial or full enrichment keeps the same `recordId` and refreshes counts.
- Backend publication tests, website tests, and website build all pass.
- The final report clearly states that no commit or push was performed unless one was explicitly requested.
