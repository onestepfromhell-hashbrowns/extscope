# ExtScope on Windows

Native Windows support for the standalone ExtScope core. The Python core is
unchanged: `extscope.config.platform_paths` already maps Windows locations
(`%ProgramData%\extscope` for system mode, `%APPDATA%` / `%LOCALAPPDATA%` for user mode).
This directory only adds the install/launch layer.

| File | Purpose |
| --- | --- |
| `extscope.cmd` | Launcher; sets `PYTHONPATH` to the library and runs `py -3 -m extscope` |
| `extscope-dist.cmd` | Launcher for the independent distribution module |
| `install.ps1` | Installer (user/system, copy/symlink, uninstall, dry-run, PATH update) |

Requires Python >= 3.11 (`py` launcher or `python` on PATH). No other dependencies.

```powershell
# preview, then install for the current user
.\windows\install.ps1 -DryRun
.\windows\install.ps1

# system-wide (elevated PowerShell)
.\windows\install.ps1 -Mode system

# development: link src instead of copying (Developer Mode or admin)
.\windows\install.ps1 -Symlink

# remove (config/data preserved)
.\windows\install.ps1 -Uninstall
```

In-tree use without installing: `windows\extscope.cmd validate my.extscope.json`
(falls back to `..\src`).

Paths: install prefix is `%LOCALAPPDATA%\Programs\extscope` (user) or
`%ProgramFiles%\extscope` (system).

Note: the Linux `install.sh` / `bin/extscope` launcher is unchanged. Container
features (Podman/Docker `Dockerfile`) work on Windows through Docker Desktop/WSL2 as before.
