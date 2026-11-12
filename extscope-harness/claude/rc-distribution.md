# Claude rc skill distribution flow

> This file records Claude Code's skill distribution and rc sync behavior. Paths always use `${NAME}` placeholders; the actual host values come from the runtime configuration (supplied by the deployment environment) and are not written into this package.

## 1. Distribution flow (extscope-dist)

1. Generate a plan with `extscope plan --scope <scope file> --entry <name>` (contains no host paths).
2. Do a dry run with `extscope-dist --config <dist config> plan <plan>`; confirm nothing exceeds `allowed_roots` and there are no conflicts.
3. On a conflict, first find out why. `--force` backs up and replaces unmanaged existing entries, and may be used only after explicit user approval.
4. Apply: `extscope-dist --config <dist config> --state-dir ${STATE_DIR} --var SKILLS_ROOT=${SKILLS_ROOT} apply <plan> --all`.
5. Verify: `extscope-dist ... verify <manifest>`.
6. Roll back: `extscope-dist ... rollback <manifest>`.

Keep one `dist-<platform>.json` per account, and always set `SKILLS_ROOT` explicitly with `--var` rather than relying on a config default.

## 2. Sync source (Claude Code account cloud sync)

- Mechanism: Claude Code's account cloud sync (noted in the changelog for the 2.1.x series).
- Behavior: after logging in, the skills and plugins enabled on the web side are synced one way into `${CLAUDE_CONFIG_DIR}/skills/synced/` and `plugins/synced/`.
- Naming: `<organization_uuid>_<account_uuid>` (`<org>_unbound` for an unbound account).
- `.bucket-<same name>`: a marker file identifying that bucket.
- `.last-complete-round`: the checkpoint of the most recent complete sync.

### Disabling sync (verified 2026-11-02)

- It is **not** a `claude config set` subcommand (2.1.312 has no such subcommand; it would be sent to the model as a conversation).
- The correct way is to add two keys to `${CLAUDE_CONFIG_DIR}/settings.json` (noted in the changelog):

```json
{
  "syncClaudeAiSkills": false,
  "syncClaudeAiPlugins": false
}
```

### Effect of deleting the sync directory (to be verified)

- While sync is still on, deleting `.last-complete-round` may trigger a full re-download.
- After sync is turned off, deleted skills are not downloaded again (tested 2026-11-02: they did not reappear after deletion).

## 3. Observed state so far (tested)

- 2026-11-02: the two disable keys were added to settings.json on three rc claude accounts (backups in a dated `claude-sync-off-20261102/` directory).
- `synced/` keeps only skill-creator, docs and deep-research. What actually exists per account:
  - account 1: docs, deep-research (no skill-creator)
  - account 2: skill-creator, docs, deep-research
  - account 3: docs (no skill-creator or deep-research)
- The list of other deleted synced skills is in `*.synced-before.txt` in the same backup directory.
- After planting this package's extscope skill, `extscope/SKILL.md` is readable under each skills root.

## 4. Exclusion list (not loaded by rc)

agent-inbox, handoff-workflow, ministep, agy-rc, plus docx, pptx, xlsx, pdf, google-workspace, chrome-browser, computer-use, built-in-browser, the hindsight series, agent-harness-adapter, agent-hygiene.

(docs, deep-research and skill-creator were changed to "keep" by the user on 2026-11-02.)

## Related

- Scope declarations and package locations: see the main extscope README.
- This file contains no account or host paths.
