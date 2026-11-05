# Changelog

All notable changes to the `extscope` suite are documented in this file.

## [0.1.0a6] - 2026-11-05

### Added
- **Reverse Sync & Origin Inspection**: Added `extscope-dist` reverse sync (`diff` / `pullback` / `origin`) and core `extscope where` subcommand.
- **Canonical Release Assembly**: Added `extscope-tools` canonical release `assemble` with `--allow-untracked` content-ID recording for git-less sources.
- **Packaging & Community Files**: Added unified `0.1.0a6` release version across all sub-packages (`extscope`, `extscope-dist`, `extscope-modules`, `extscope-container`, `extscope-tools`, `agent-harness-adapter`, `extscope-plugin-trap-fixture`), complete `pyproject.toml` metadata, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, and GitHub Actions CI workflow.
- **Container Hardening**: Added `HEALTHCHECK` and `README.md` build context copy to `Dockerfile`.

### Changed
- Standardized `$schema` identifiers to `urn:extscope:schema:*` and aligned all path examples with portable FHS/XDG conventions.
