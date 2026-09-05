---
name: update-crawl-benchmark
description: Safely merge one or more computers' local Auto Email Sender crawl databases into the public website benchmark JSON, normalize confirmed school aliases, verify privacy and data invariants, and run backend and website checks. Use when the user asks to update, upload, refresh, merge, or validate the official 智能抓取效果展示 data from a local auto_email_sender.db or the historical XLSX.
---

# Update Crawl Benchmark Data

Merge local crawl aggregates into `website/data/crawl-benchmark.json`. Read `docs/operations/crawl-benchmark-publication.md` when changing publication behavior or resolving a schema, merge or privacy issue.

1. Identify the requested database; omitting it uses the desktop app's standard data directory. Bring the public JSON up to date with the remote version while preserving unrelated edits.
2. Check `config/crawl-benchmark-aliases.json`. Add university/school aliases only when supported by the source or confirmed by the user.
3. From `backend/`, run:

   ```bash
   uv run python ../scripts/data/update_crawl_benchmark.py [--database /absolute/path/auto_email_sender.db] [--legacy-xlsx /absolute/path/history.xlsx]
   ```

   Use the historical workbook only when requested or during its initial migration.
4. Review the diff: existing records from other machines remain, same-job enrichment updates retain `recordId`, aliases are correct, and only aggregate fields are published. Enrichment progress uses all candidates as its denominator.
5. Run `uv run python -m unittest test.test_crawl_benchmark_publication` in backend, then `npm run test` and `npm run build` in website. Report changed counts, unresolved aliases and verification results. Commit or publish according to the user's request.

The script opens the database read-only. Do not migrate, edit or publish it. Names, emails, logs, prompts and database paths do not belong in public output.

The existing schema 2 JSON is an input: retain other machines' records. After a conflicting remote update, integrate it and rerun the script, rather than selecting one side. Local numeric job IDs alone are not global identities. A schema 1 upgrade needs the primary history database; see the operations guide before rebuilding that baseline.
