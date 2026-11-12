---
name: extscope-dist
description: >-
  Operating SOP for extscope-dist (the host-side distribution engine): writes the copy / symlink / md_block items of a plan (and of
  enabled modules) to host targets, including dry-run, scope gate, backup, manifest, verify and rollback. Use it when applying a
  distribution plan, diagnosing apply/rollback rejection codes, or writing a dist.json configuration (allowed_roots / remap / state_dir).
  For declarations and planning use the extscope package-set skill; for container transport use extscope-container.
---

# extscope-dist

The only extscope component that **writes host files** (standard library only; it does not import extscope). The core decides "what to send" and dist only does the "writing"; the two meet only through a JSON plan. For an overview of the package set see `../skills/extscope/SKILL.md`, and for details see `README.md`.

## Minimal flow
```bash
# Global flags (--config / --state-dir / --var) must come BEFORE the subcommand; after it they give "unrecognized arguments"
extscope-dist --config dist.json plan  plan.json                 # dry run, no write
extscope-dist --config dist.json apply plan.json --filter-prefix /ws/x   # or --all
extscope-dist --config dist.json verify   <manifest-id|path>
extscope-dist --config dist.json rollback <manifest-id|path>
```
`dist.json`: `{"allowed_roots":[...], "denied_roots":[...], "remap":[{"from":..,"to":..}], "variables":{..}, "state_dir":.., "modules":{"enable":[..],"policy":{..}}}`. Only these fields are allowed; any extra field gives `bad_config`.
Config lookup: `--config` → `$EXTSCOPE_DIST_CONFIG` → per-user default; **an explicitly specified file that does not exist** is `config_not_found` (it does not silently fall back to the default). state_dir: `--state-dir` → `$EXTSCOPE_DIST_STATE_DIR` → config → per-user default. `--var` overrides config `variables`.

## Authorization link to the core (--receipt)
The receipt from the core's `extscope bind` records "the approved source bytes". Add `--receipt receipt.json` to `plan` / `apply`: every copy / md_block source must be in the receipt with unchanged bytes (file sha256 or `extscope.tree.v1`), otherwise it is rejected before writing (`source_not_bound` / `receipt_source_mismatch`; a preview plan is also checked for `receipt_plan_mismatch`; a bound directory containing a symlink gives `receipt_tree_has_symlink`). **Without `--receipt` there is no binding at all between the core's approval and the actual write.** At bind time, `--allow-root` must cover the source directory.

## Rules (violations are rejected with exit 2, stderr is `{"error":code,"detail":..}`)
1. **fail-closed**: without `allowed_roots` the answer is always `no_allowed_roots`. A target must be inside allowed and outside denied; both the lexical path and the real path of the parent directory are checked (symlink escape → `target_outside_allowed_roots` / `target_denied`).
2. `apply` must be given `--filter-prefix` (repeatable) or `--all`, otherwise `scope_required`. If both are given, `--all` wins and the prefix is ignored.
3. All paths must be absolute and must not contain `..`; an undefined `${NAME}` → `unknown_variable`.
4. A target that already exists and was **not written by dist** → `conflict`; `--force` backs it up first and then replaces it. A module refusal (`module_refused`) **cannot** be overridden by `--force`.
5. `remap` applies only to the **target** (longest prefix); `src` is never remapped. The content of a symlink is `src`, so `src` must be written as the final location.
6. A duplicate (method, target, block_id) → `duplicate_target`. A method from a module that is not enabled → `module_not_enabled` (it is not skipped).

## Reverse sync (a target was modified and must be written back to the source)
- A deployment target is an artifact, not a source. When someone edits a target directly, **do not rollback** (it would discard the edit); follow this order:
  `origin <target path>` to find the source and git repo → `diff <manifest>` to see the differences → `pullback <manifest>` as a dry run → add `--apply` to write back to the source → a person reviews `git diff` and commits → `bind` + `apply` again.
- `pullback` handles only `copy` and `md_block`; `symlink` and module items are listed as `not_reversible`. The write scope is limited by the configuration `source_roots` (fail-closed: not set gives `no_source_roots`, outside the scope gives `source_outside_source_roots`). The write-back must go to the further-upstream repo.
- If the source was also modified after apply → that item is `source_changed`/`both_changed`, overwriting is refused, and the result is `ok:false` (exit 4). Files added at the target are added back to the source and modified ones are updated; **files deleted at the target are only listed in `left_alone` and are not deleted from the source**.
- `origin` returns `repo.root/branch/commit/dirty` and adds the next step: `extscope where --scope-dir <declaration dir> <src>` to look up the scope that declares this source.
- None of these three commands commits to git.

## Concurrency and cleanup
- `apply`/`rollback` take a `flock` on the target's parent directory; if it cannot be acquired, after 30 seconds the result is `lock_timeout` (not a conflict; retry later). `plan` does not lock.
- `extscope-dist prune [--older-than DAYS] [--include-applied] [--apply]` cleans `state_dir`: it lists only by default; `--apply` deletes; by default it covers only rolled-back and orphan backups, and `--include-applied` also covers old manifests fully superseded by newer records (you would lose the backup for "restore to before the first apply"). The latest manifest of each target is never deleted.

## Exit codes
0 success; 2 rejection (the error codes above); 4 drift found by `verify` only. `rollback_incomplete` is 2, not 4.

## Behaviors verified in practice that are easy to trip over (from adversarial review)
- **`plan` and `apply` use the same scope**: `plan --filter-prefix DIR` lists only the items in scope; without a prefix it lists all of them.
- **`sha256` verifies the source for copy / symlink / md_block** (file digest or `tree:` digest); a mismatch → `source_digest_mismatch`.
- **Rollback of an md_block has a drift check**: if the file was modified after apply → `rollback_incomplete` (`--force` restores the whole file to the pre-apply backup and discards later edits). When the same file was applied several times, roll back one by one from newest to oldest. For md_block, `verify` only checks that the block exists and does not compare content.
- **After a successful rollback the manifest is renamed to `*.rolledback.json`**: rolling back / verifying the same id again gives `manifest_not_found`; it is not idempotent.
- **A manifest records only the items actually written in that run**: noop targets (content already identical) are not in that manifest, so verify / rollback with it cannot manage those targets; re-running an unchanged plan gives `applied: 0` (and still writes a manifest with empty entries).
- When copying a directory, dist **follows symlinks inside the source tree** (copying them as real files); the digest takes the link text for a symlink.
- A partial failure automatically restores the items already applied and returns `apply_failed_rolled_back`; exceptions that are not `DistError` are also wrapped in this code.
- If a copy/symlink item in a rollback has a target modified after apply → the whole operation stops and returns `rollback_incomplete` (`--force` overrides). Irreversible module items (hooks) are listed only in `not_undoable` and are not restored.

## Plan input
- `extscope-dist.plan.v1` (native): `method` = copy / symlink / md_block (requires `block_id`), optional `sha256`; copy/symlink/md_block items must not carry `config`. A module item may have only `method` + `config`.
- `extscope.plan-preview.v1` (the output of `extscope plan`, read as plain data): `copy_inject`/`symlink`/`md_block_inject` are applied, `volume_mount` is recorded in `skipped` (container only), items with `enabled:false` are skipped, and other module steps are treated as module items and rejected if not enabled.

## Boundaries with other components
- To change declarations, validate, bind, or query the DAG → `extscope` (read-only).
- To build container mounts or inject credentials → `extscope-container`.
- To scan for plaintext secrets or check layer dependencies → `extscope-tools`.
- chmod / chown / key_inject / env_inject / config_merge / custom_hook / pkg_import → `extscope-modules`; it must be enabled in `modules.enable` and given a policy in `modules.policy.<method>`; a missing policy is rejected.

## Upstream: agent-harness-adapter
The agent-specific "what goes where" is produced as `extscope-dist.plan.v1` by `adapters/project-harness.py --emit-dist-plan` in `../extscope-harness` (an extscope sub-package), and dist only does the writing. When the plan contains `config_merge` items (MCP / trust settings), the dist configuration must have `modules.enable: ["extscope_modules"]`, otherwise `module_not_enabled`.

## Tests
`python3 -m pytest -q extscope-dist/tests` (this includes guard tests: the source must not import extscope and must not hard-code environment paths or fixed `/opt` `/srv` `/var/lib` paths, so do not put the example paths from SKILL/README into `src/`).
