# Repository Guidelines

- Use Node.js 24 (the CI baseline), Python 3.12, and `uv`.
- Manage Python dependencies with `uv`. For Node workspaces, use `npm ci` for clean installs from committed lockfiles; use `npm install` only when intentionally changing dependencies, and commit both `package.json` and `package-lock.json`.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

## Project Structure & Module Organization

- `frontend/` contains the Vite + React UI. App code is in `frontend/src`, with routes in `pages`, reusable UI in `components/{atoms,molecules,organisms}`, feature logic in `features`, and shared helpers in `lib`.
- `backend/` contains the FastAPI service. Application code is organized under `app/{api,services,models,schemas,core,agents}`, Alembic migrations live under `alembic/`, `dev_entry.py` starts local development, and `desktop_entry.py` is used by the desktop runtime.
- `desktop/` contains the Electron shell, preload code, desktop tests, and Windows/macOS packaging configuration.
- `website/` contains the VitePress documentation site, public screenshots, and website-specific tests.
- `scripts/` contains release, packaging, icon-generation, and release-note helper scripts.
- `backend/test/`, `frontend/test/`, `frontend/src/**/*.test.*`, `desktop/test/`, and `website/test/` contain the active automated tests.
- `docs/` is owner-based: active guidance lives under `architecture/`, `product/`, `development/`, and `operations/`; published notes live under `releases/`, historical plans under `archive/`, and images under `screenshots/`. Start at `docs/README.md`. `data/` holds local runtime data and exports.

## Build, Test, and Development Commands

- `cd frontend && npm ci`: install frontend dependencies from the lockfile.
- `cd frontend && npm run dev`: start the Vite dev server on `http://127.0.0.1:5173`.
- `cd frontend && npm run build`: run TypeScript compilation and create the production bundle.
- `cd frontend && npm run lint`: run ESLint across TS/TSX files.
- `cd frontend && npm run test`: run the frontend Vitest suite.
- `cd backend && uv sync --dev`: create or refresh the Python environment, including development and packaging tools.
- `cd backend && uv run alembic upgrade head`: apply database migrations before running the web app against an existing data directory.
- `cd backend && uv run python dev_entry.py`: run the FastAPI API locally on `http://127.0.0.1:8010`.
- `cd backend && uv run python -m unittest discover test`: run the backend unittest suite.
- `pwsh -NoProfile -File scripts/install-backend-playwright.ps1`: from the repository root, sync backend dependencies and install the packaged Playwright Chromium runtime used by crawler and desktop builds. On POSIX without PowerShell, run `cd backend && PLAYWRIGHT_BROWSERS_PATH=ms-playwright uv run python -m playwright install --only-shell chromium` after `uv sync --dev`.
- `cd desktop && npm ci`: install Electron workspace dependencies from the lockfile.
- `cd desktop && npm run typecheck`: type-check Electron main and preload TypeScript configs.
- `cd desktop && npm run test`: run desktop Vitest tests.
- `cd desktop && npm run dev`: build and launch Electron after the frontend Vite server is running; Electron starts the backend automatically.
- `cd website && npm ci`: install documentation site dependencies from the lockfile.
- `cd website && npm run docs:dev`: start the VitePress documentation server on `127.0.0.1`.
- `cd website && npm run build`: build the documentation site.
- `cd website && npm run test`: run website Vitest tests.

## Coding Style & Naming Conventions

- Use UTF-8 for source files, Markdown, and terminal output to avoid Chinese text corruption.
- TypeScript and TSX follow the existing React + Vite style: 2-space indentation, PascalCase component files such as `HomePage.tsx`, camelCase utilities/hooks such as `useMentorFilters.ts`, and `@/` imports for `frontend/src`.
- Python uses 4-space indentation, snake_case module names, and explicit typing for FastAPI handlers and support code where practical.
- Electron code in `desktop/src` uses TypeScript modules and keeps main-process, preload, and platform integration logic separated.

## Testing Guidelines

- Frontend tests use Vitest with node and jsdom projects. Run `npm run lint` plus the relevant `npm run test`, `npm run test:node`, or `npm run test:dom` command for touched UI and client logic.
- Backend tests use `unittest` with `test_*.py` naming under `backend/test`. Keep new tests deterministic and avoid live-network dependencies unless they are clearly experimental.
- Database model or schema changes must include an Alembic revision under `backend/alembic/versions` and tests covering migration or schema behavior.
- Desktop and website tests use Vitest. For desktop changes, run `npm run typecheck` and `npm run test`; for website changes, run `npm run test` and `npm run build`.
- For packaging or release changes on POSIX, run `cd frontend && npm run test:release-notes`, `bash scripts/release/prepare-release.test.sh`, `bash scripts/release/release-script.test.sh`, and the relevant packaging tests (`cd desktop && npm run test -- packaging.test.ts` and `cd frontend && npm run test -- desktopPackaging.test.ts`). On Windows, run the corresponding `.ps1` tests under `scripts/release/` with `pwsh -NoProfile -File`.

## Commit & Pull Request Guidelines

- Recent history mixes Chinese summaries with Conventional Commit prefixes. Prefer `type(scope): summary`, for example `feat(frontend): add mentor filter state` or `docs: update database design`.
- Keep each commit focused on one logical change. PRs should explain the change, list verification commands, link related issues, and include UI screenshots when needed.

## Security & Configuration Tips

- Never commit `.env`, API keys, `.venv`, `node_modules`, or generated crawler output.
- When adding configuration, update the corresponding `.env.example` file and keep local-only output under `data/` or ignored test output folders.
