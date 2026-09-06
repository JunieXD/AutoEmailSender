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
   uv run python ../scripts/data/update_crawl_benchmark.py --json --dry-run
   ```

   Use `--database <absolute-path>` for another computer and `--legacy-xlsx <absolute-path>` only for requested historical data or its initial migration. Preview reads the same existing public JSON as execution. Do not use a new `--output` path as a preview: it would select a different merge baseline.
4. Read `changes.added/updated/retained/removed`, `changed`, and `next_action`. If the requested update has changes, rerun the same arguments without `--dry-run`. An unchanged run preserves the file and its generation time. Errors return `ok: false`, `code` and `next_action` with exit code 2; fix the named input instead of rebuilding or discarding existing history.
5. Review the diff: existing records from other machines remain, same-job enrichment updates retain `recordId`, aliases are correct, and only aggregate fields are published. Enrichment progress uses all candidates as its denominator.
6. Run `uv run python -m unittest test.test_crawl_benchmark_publication` in backend, then `npm run test` and `npm run build` in website. Report changed counts, aliases still needing evidence (if encountered) and verification results. The script does not infer unconfirmed aliases. Commit or publish according to the user's request.

The script opens the database read-only. Do not migrate, edit or publish it. Names, emails, logs, prompts and database paths do not belong in public output.

The output is Schema 3; existing Schema 2/3 JSON is an input: retain other machines' records. After a conflicting remote update, integrate it and rerun the script, rather than selecting one side. Local numeric job IDs alone are not global identities. A schema 1 upgrade needs the primary history database; see the operations guide before rebuilding that baseline.
