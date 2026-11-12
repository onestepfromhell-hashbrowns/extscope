# Claude Code settings and compact (agent harness reference)

> This file records the settings surface of Claude Code 2.1.312, for use by an agent harness (hook, skill, rule, workflow). Paths always use `${NAME}` placeholders.
> The verification level is marked on every item: **[string-confirmed]** only the name was found in the executable; **[tested]** behavior was confirmed on an rc account; **[unconfirmed]** no evidence yet.

## 1. Executable and settings root

- Executable: `${CLAUDE_BIN}` (2.1.312). If started through a wrapper script, the final call is the same executable.
- Settings root: `${CLAUDE_CONFIG_DIR}`. For an rc account it is `${RC_ROOT}/<acct>`; when unset it is `~/.claude`.
- The `$HOME` of an rc process is `${RC_ROOT}/<acct>/home`, separate from `CLAUDE_CONFIG_DIR`.

## 2. File locations

| File | Purpose | Location |
|---|---|---|
| `settings.json` | behavior settings, `env`, sync switches | `${CLAUDE_CONFIG_DIR}/settings.json`; project level `.claude/settings.json` |
| `.claude.json` | global UI, trust list, etc. (presumed) | `${CLAUDE_CONFIG_DIR}/.claude.json` **[unconfirmed]** |
| `CLAUDE.md` | instructions loaded automatically for each working directory | the working directory and `${CLAUDE_CONFIG_DIR}` |
| `skills/` | skills directory; `synced/` is the account cloud sync | `${CLAUDE_CONFIG_DIR}/skills/` |

## 3. Sync switches [tested]

- Keys: `syncClaudeAiSkills`, `syncClaudeAiPlugins`, written in `settings.json`; the value `false` turns them off.
- 2.1.312 has **no** `claude config set` subcommand; that form would be sent to the model as a conversation.
- On 2026-11-02 both keys were written on three rc accounts (backups kept in a dated `claude-sync-off-20261102/` directory).
- After they are turned off, skills deleted inside `synced/` are not downloaded again.

## 4. Compact settings

| Name | Location | Effect (per the string description) | Verification |
|---|---|---|---|
| `DISABLE_AUTO_COMPACT` | `env` in `settings.json` | turns off automatic compact | [string-confirmed] |
| `DISABLE_COMPACT` | `env` in `settings.json` | turns off automatic compact and disables manual `/compact` | [string-confirmed] |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | `env` in `settings.json` | adjusts the automatic compact trigger percentage (example: `"80"`) | [string-confirmed]; a comment says it is ignored in fork scenarios |
| `autoCompactEnabled` | presumably `~/.claude.json` | boolean, described as "Automatically compact conversation when context fills" | [string-confirmed]; **owning file unconfirmed** |

Example (for reference only; not yet applied to rc):

```json
{
  "env": {
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80"
  }
}
```

**How to confirm which file owns `autoCompactEnabled`**: toggle it once in `/config`, then compare `settings.json` and `.claude.json` before and after.

## 5. Hook, skill, rule, workflow (the harness surface an agent needs)

- **hook**: the executable contains the `hooks` key and the event names `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop` **[string-confirmed]**. The structure and actual behavior have not been tested on rc.
- **skill**: `${CLAUDE_CONFIG_DIR}/skills/<name>/SKILL.md`, planted by extscope-dist (see `rc-distribution.md`).
- **rule**: `CLAUDE.md`; written by agent-harness-adapter using the `md_block` mode.
- **workflow**: there is currently no verified workflow config file format. To be added once the harness spec is settled.

## 6. To be confirmed

1. The exact file of `autoCompactEnabled`.
2. The complete hook structure in `settings.json`, and whether the harness needs project-level settings.
3. Whether the `env` variables above actually take effect in an rc process (needs testing on an rc account).
