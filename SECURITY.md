# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report them privately through GitHub Security Advisories: open the
repository's **Security** tab and choose **Report a vulnerability**
(`https://github.com/onestepfromhell-hashbrowns/extscope/security/advisories/new`).

Include what you found, how to reproduce it, and the version or commit
you tested. You will get an acknowledgement as soon as a maintainer can
look at it. Please allow time for a fix before any public disclosure.

## Scope

This project is a standalone extension-delivery suite. It enforces policy as
described in its README; behavior outside that description is not a supported
security boundary. If you are unsure whether something is in scope, report it
privately anyway.

### Threat model

**Components and what they may do**

| Component | Writes to the filesystem? | Trust assumption |
|---|---|---|
| `extscope` (core: `validate`, `plan`, `bind`, `verify`, `digest`, ...) | No (read-only planning; `init`/`new` print to stdout) | Scope files are untrusted data; the core validates their structure, refuses symlinked scope files, and walks directories without following symlinks. Its output is a *plan*, not an authorization: callers decide whether to act on it. |
| `extscope-dist` (`apply`, `rollback`, `pullback`, `prune`) | Yes | The dist config file is trusted. Writes are fail-closed: with no `allowed_roots` nothing is applied; every target must be inside `allowed_roots` and outside `denied_roots`, checked on both the lexical path and the resolved parent chain. Sources must be inside `source_roots` and must not be symlinks. |
| `extscope-dist` modules | Whatever the module does | Python modules are imported only when listed under `modules.enable` in the trusted config. Enabling a module is equivalent to running its code with your privileges. |
| `install.sh` | Yes, under the chosen prefix and the config/data/cache dirs | Run it from a checkout you trust. `--system --symlink` is refused unless the source tree is root-owned and not group/world-writable, because a root-installed launcher would otherwise run code any user can edit. |
| Container image (`Dockerfile`) | Inside the container only | Runs as the unprivileged `extscope` user (UID 1000). |

**Not defended against**

- A local attacker who can already write inside `allowed_roots` or their parent directories while `apply` runs (time-of-check/time-of-use races on the target path).
- Malicious content in files you distribute: extscope moves and verifies bytes, it does not inspect them.
- A malicious dist config, enabled module, or `PYTHONPATH`/`EXTSCOPE_PYTHON` set by the invoking user (the launchers honour both).

## Secrets

If you find a credential, token or key committed to this repository, report it
the same way and treat it as compromised.
