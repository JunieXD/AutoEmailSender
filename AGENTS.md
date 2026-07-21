# Repository Guidelines

- Python 使用 uv 进行包管理。

## Project Structure & Module Organization

- `frontend/` contains the Vite + React UI. App code is in `frontend/src`, with routes in `pages`, reusable UI in `components/{atoms,molecules,organisms}`, feature logic in `features`, and shared helpers in `lib`.
- `backend/` contains the FastAPI service. `dev_entry.py` starts local development, `desktop_entry.py` is used by the desktop runtime, and `pyproject.toml` / `uv.lock` define Python dependencies.
- `desktop/` contains the Electron shell, preload code, desktop tests, and Windows packaging configuration.
- `website/` contains the VitePress documentation site, public screenshots, and website-specific tests.
- `scripts/` contains release, packaging, icon-generation, and release-note helper scripts.
- `backend/test/`, `frontend/test/`, `frontend/src/**/*.test.*`, `desktop/test/`, and `website/test/` contain the active automated tests.
- `docs/` stores product notes, database notes, implementation notes, release notes, screenshots, and design specs. `data/` holds local runtime data and exports.

## Build, Test, and Development Commands

- `cd frontend && npm install`: install frontend dependencies.
- `cd frontend && npm run dev`: start the Vite dev server on `http://127.0.0.1:5173`.
- `cd frontend && npm run build`: run TypeScript compilation and create the production bundle.
- `cd frontend && npm run lint`: run ESLint across TS/TSX files.
- `cd frontend && npm run test`: run the frontend Vitest suite.
- `cd backend && uv sync`: create or refresh the Python environment.
- `cd backend && uv run python dev_entry.py`: run the FastAPI API locally on `http://127.0.0.1:8010`.
- `cd backend && uv run python -m unittest discover test`: run the backend unittest suite.
- `cd desktop && npm install`: install Electron workspace dependencies.
- `cd desktop && npm run typecheck`: type-check Electron main and preload TypeScript configs.
- `cd desktop && npm run test`: run desktop Vitest tests.
- `cd desktop && npm run dev`: build and launch the Electron app in development mode.
- `cd website && npm install`: install documentation site dependencies.
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
- Desktop and website tests use Vitest. Run their local `npm run test` commands when touching `desktop/` or `website/`.
- For packaging or release changes, run the focused script tests under `scripts/` and the relevant desktop packaging tests.

## Commit & Pull Request Guidelines

- Recent history mixes Chinese summaries with Conventional Commit prefixes. Prefer `type(scope): summary`, for example `feat(frontend): add mentor filter state` or `docs: update database design`.
- Keep each commit focused on one logical change. PRs should explain the change, list verification commands, link related issues, and include UI screenshots when needed.

## Security & Configuration Tips

- Never commit `.env`, API keys, `.venv`, `node_modules`, or generated crawler output.
- When adding configuration, update the corresponding `.env.example` file and keep local-only output under `data/` or ignored test output folders.
