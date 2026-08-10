# Documentation Map

The active documentation is organized by ownership:

- `architecture/`: module boundaries, dependency rules, and the modularization execution record.
- `product/`: current product behavior and feature design baselines.
- `development/`: implementation notes, engineering plans, acceptance reports, and machine-checked contract data.
- `operations/`: local operations, data publication, packaging, and release procedures.
- `releases/`: published release notes consumed by the release flow.
- `screenshots/`: product images referenced by repository and website documentation.

Start with `architecture/README.md` for code ownership and dependency direction. Use the active owner directories when changing current behavior.

Machine-checked JSON under `development/` is owned by the CLI contract tests. Update the data and its tests together rather than treating it as prose documentation.

On the project Mac, use `operations/windows-parallels-release-qa.md` for the dedicated Windows 11 VM and real Windows pre-release verification workflow.

For prerelease-only local diagnostics, safe multi-bundle analysis, and the final-ZIP canary gate, use
`operations/beta-diagnostics-operations.md`.

For generic alpha/beta/rc preparation, exact-candidate certification, non-Latest publication, stable-feed
isolation, local observation, and supersession, use `operations/desktop-prerelease-operations.md`.
