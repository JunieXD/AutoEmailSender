# Documentation Map

The active documentation is organized by ownership:

- `architecture/`: module boundaries, dependency rules, and the modularization execution record.
- `product/`: current product behavior and feature design baselines.
- `development/`: implementation notes, engineering plans, acceptance reports, and machine-checked contract data.
- `operations/`: local operations, data publication, packaging, and release procedures.
- `releases/`: published release notes consumed by the release flow.
- `screenshots/`: product images referenced by repository and website documentation.
- `archive/`: historical plans and specifications that are no longer active sources of truth.

Start with `architecture/README.md` for code ownership and dependency direction. Use active documents when changing current behavior; archived documents preserve the context and paths that existed when they were written.

Machine-checked JSON under `development/` is owned by the CLI contract tests. Update the data and its tests together rather than treating it as prose documentation.
