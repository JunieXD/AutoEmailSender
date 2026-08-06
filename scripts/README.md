# Script Ownership

Script implementations and their focused tests are grouped by capability:

- `build/`: backend, CLI, Playwright, and Sparkle build preparation.
- `packaging/`: Electron packaging hooks and release asset assembly.
- `quality/`: repository audits and structural checks.
- `data/`: explicit data publication utilities.
- `release/`: release notes, version checks, and release orchestration.

The root directory only exposes stable commands already used by developer documentation:

- `build-backend.{sh,ps1}`
- `build-cli.{sh,ps1}`
- `install-backend-playwright.ps1`
- `prepare-release.{sh,ps1}`
- `release.{sh,ps1}`

These files are thin argument-forwarding wrappers. Repository automation and implementation tests should call the owner path directly so ownership changes remain visible.
