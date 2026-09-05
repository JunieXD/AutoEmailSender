# Repository Guidelines

- Use Node.js 24, Python 3.12 and `uv`. Prefix shell commands with `rtk` (`rtk proxy` for unsupported commands).
- Install Python dependencies with `uv sync --dev`; use `npm ci` in Node workspaces. Use `npm install` when changing dependencies and update both package files.
- If `.codegraph/` exists, use `codegraph explore "<file, symbol or question>"` before searching for code. Do not create an index unless requested.

## Layout

- `backend/app/modules/`: domain logic; `api/` and `services/`: remaining HTTP adapters and shared services. Migrations: `backend/alembic/versions/`.
- `frontend/src/`: React UI, feature logic and API clients.
- `desktop/`: Electron main/preload and packaging. `cli/`: Agent API client. `contracts/`: shared protocols.
- `website/`: VitePress site. `scripts/`: build, release, quality and data tools. Documentation starts at `docs/README.md`.

## Changes

Follow nearby code style: UTF-8, Python 4-space indentation, TypeScript 2-space indentation and frontend `@/` imports.
Validate external inputs at their boundary; use the resulting types internally. Add abstractions, compatibility branches and retries only for a concrete requirement.
Test observable behavior and meaningful failure cases. Avoid tests that duplicate implementation, freeze prose or directory layouts, or merely repeat type checking.
Update existing documentation when behavior changes; routine work does not need a new plan or acceptance report.
Schema changes need an Alembic revision and migration coverage. Keep secrets, local databases, generated crawl data and dependencies out of Git; update `.env.example` for configuration changes.
Prefer focused `type(scope): summary` commits. PRs should explain behavior and validation.

## Development and verification

Run commands from the relevant workspace:

| Workspace | Development | Verification |
| --- | --- | --- |
| backend | `uv run python dev_entry.py` | `uv run python -m unittest test.test_<module>` |
| frontend | `npm run dev` | `npm run lint`, `npm run test -- <file>`, `npm run build` |
| desktop | `npm run dev` (requires frontend dev server) | `npm run typecheck`, `npm run test` |
| cli | `uv run auto-email-sender --help` | `uv run python -m unittest discover test` |
| website | `npm run docs:dev` | `npm run test`, `npm run build` |

Apply migrations with `cd backend && uv run alembic upgrade head` before using an existing development database.
For a full test run: `rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py`.
For release and packaging work, use `.codex/skills/auto-email-sender-release/SKILL.md` and the relevant guides under `docs/operations/`.

<!-- ZVEC_GREP_START -->
## zvec-grep

Choose the evidence source before the retrieval mode.

### Workspace evidence
- Use the current workspace as the evidence source when the user asks about local material, prior context establishes it as relevant, or the question concerns how the current project works—even if the workspace is not mentioned explicitly.
- A workspace may contain any mix of code, documents, configuration, and data.
- Do not use workspace retrieval for unrelated open-world questions, current external facts, or web content that does not depend on local evidence.

### Retrieval routing
- When an exact word, phrase, name, date, identifier, filename, path, configuration key, error message, source fragment, literal, or regex is known and locating its occurrences is sufficient, use `zvec_grep_rg` when it is listed by the current host; otherwise native Grep or `rg`.
- Use `zvec_grep_search` when wording or location is unknown, or when the answer requires semantic, conceptual, fuzzy, or paraphrase discovery; relationships, chronology, causality, architecture, or data or control flow; or comparison or synthesis across files, sections, or documents.
- For a mixed task with exact anchors that still requires relationships or cross-file synthesis, call `zvec_grep_search` with the concept and anchors, then use `zvec_grep_rg` when it is listed by the current host; otherwise native Grep or `rg` for focused follow-up.
- When no sufficient exact anchor is available and the user asks whether conceptually related material exists locally, make at most one focused `zvec_grep_search` probe using the question plus distinctive names, dates, or terms. This probe does not apply to exact quotations, configuration keys, filenames, regexes, or exhaustive occurrence requests. Continue only when results are relevant; otherwise stop and report that the indexed workspace did not establish the answer.
- Before broad file reads or delegating workspace discovery, use the appropriate search route. Do not delegate solely to locate material, and stop when the evidence is sufficient.

### Search evidence
- Search results include bounded source snippets. Treat a sufficient snippet as already-read evidence, and read a cited file only when a required detail falls outside the snippet.

### Freshness and index lifecycle
- Pass a daemon-visible absolute `root` on every zvec-grep workspace call.
- Read `freshness` and `background_refresh` from search results without a status preflight.
- When results are `served_from_current_index`, use them when sufficient instead of waiting for the background refresh.
- If the index is missing but exact or regex lookup can answer the task, use `zvec_grep_rg` when it is listed by the current host; otherwise native Grep or `rg`.
- Creating, rebuilding, or dropping a persistent index requires an explicit user request or authorization; never do so silently.

<!-- ZVEC_GREP_END -->
