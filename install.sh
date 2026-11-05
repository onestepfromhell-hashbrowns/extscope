#!/usr/bin/env bash
# ==============================================================================
# ExtScope Universal Portable Installer (FHS 3.0 & XDG Compliant)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="0.1.0a6"

MODE=""           # "system" or "user" (auto-detected if empty)
INSTALL_TYPE="copy" # "copy" or "symlink"
PREFIX=""
DRY_RUN=false
UNINSTALL=false

usage() {
    cat <<EOF
ExtScope Installer (v${VERSION})

Usage:
  ./install.sh [OPTIONS]

Options:
  --system        Install system-wide to /usr/local (requires root/sudo)
  --user          Install to current user's ~/.local (no root required)
  --prefix=DIR    Explicit install prefix (e.g. /usr, /usr/local, ~/.local)
  --symlink       Install via symlinks to source repo (for local development)
  --copy          Install by copying self-contained files (default, portable)
  --uninstall     Remove ExtScope from the target installation prefix
  --dry-run       Preview installation actions without making any changes
  -h, --help      Show this help message

FHS Layout (System / Root):
  Binary:         <prefix>/bin/extscope
  Library:        <prefix>/lib/extscope/src
  Config:         /etc/extscope
  State/Data:     /var/lib/extscope/canonical
  Cache:          /var/cache/extscope
  Runtime:        /run/extscope

XDG Layout (User / Non-root):
  Binary:         ~/.local/bin/extscope
  Library:        ~/.local/lib/extscope/src
  Config:         ~/.config/extscope
  State/Data:     ~/.local/share/extscope/canonical
  Cache:          ~/.cache/extscope
  Runtime:        /run/user/<UID>/extscope (or ~/.local/state/extscope/run)
EOF
    exit 0
}

# Parse CLI arguments
for arg in "$@"; do
    case "$arg" in
        --system)
            MODE="system"
            ;;
        --user)
            MODE="user"
            ;;
        --prefix=*)
            PREFIX="${arg#*=}"
            ;;
        --symlink)
            INSTALL_TYPE="symlink"
            ;;
        --copy)
            INSTALL_TYPE="copy"
            ;;
        --uninstall)
            UNINSTALL=true
            ;;
        --dry-run)
            DRY_RUN=true
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Error: Unknown argument '$arg'" >&2
            echo "Run './install.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done

# Check Python 3 version
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "Error: python3 is required but not found in PATH." >&2
    exit 1
fi

PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.minor)')"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "Error: ExtScope requires Python >= 3.11, found Python $PY_VER." >&2
    exit 1
fi

# Auto-detect Mode if not explicitly set
if [ -z "$MODE" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        MODE="system"
    else
        MODE="user"
    fi
fi

# Determine prefix and target directories
if [ -n "$PREFIX" ]; then
    TARGET_PREFIX="$PREFIX"
elif [ "$MODE" = "system" ]; then
    TARGET_PREFIX="/usr/local"
else
    TARGET_PREFIX="${HOME}/.local"
fi

BIN_DIR="${TARGET_PREFIX}/bin"
LIB_DIR="${TARGET_PREFIX}/lib/extscope"

if [ "$MODE" = "system" ]; then
    CONF_DIR="/etc/extscope"
    DATA_DIR="/var/lib/extscope/canonical"
    CACHE_DIR="/var/cache/extscope"
    RUN_DIR="/run/extscope"
else
    CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/extscope"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/extscope/canonical"
    CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/extscope"
    if [ -d "/run/user/$(id -u)" ]; then
        RUN_DIR="/run/user/$(id -u)/extscope"
    else
        RUN_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/extscope/run"
    fi
fi

run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

# --- UNINSTALL MODE ---
if [ "$UNINSTALL" = true ]; then
    echo "Uninstalling ExtScope from ${TARGET_PREFIX}..."
    run_cmd rm -f "${BIN_DIR}/extscope"
    run_cmd rm -rf "${LIB_DIR}"
    run_cmd rm -f "${BIN_DIR}/extscope-dist"
    run_cmd rm -rf "${TARGET_PREFIX}/lib/extscope-dist" "${TARGET_PREFIX}/lib/extscope-modules" "${TARGET_PREFIX}/lib/extscope-container" "${TARGET_PREFIX}/lib/extscope-tools" "${TARGET_PREFIX}/lib/extscope-harness"
    run_cmd rm -f "${BIN_DIR}/extscope-container" "${BIN_DIR}/extscope-tools" "${BIN_DIR}/extscope-harness"
    echo "✅ ExtScope binary and libraries removed from ${TARGET_PREFIX}."
    echo "Note: Configuration (${CONF_DIR}) and data (${DATA_DIR}) were preserved."
    exit 0
fi

# --- INSTALL MODE ---
echo "=================================================="
echo "ExtScope Portable Installer (v${VERSION})"
echo "Mode:         ${MODE}"
echo "Install Type: ${INSTALL_TYPE}"
echo "Binary:       ${BIN_DIR}/extscope"
echo "Library:      ${LIB_DIR}"
echo "Config:       ${CONF_DIR}"
echo "Data:         ${DATA_DIR}"
echo "Python:       ${PYTHON_BIN} (${PY_VER})"
echo "=================================================="

# Check permissions
if [ "$MODE" = "system" ] && [ "$DRY_RUN" = false ] && [ "$(id -u)" -ne 0 ]; then
    echo "Error: System-wide installation requires root or sudo." >&2
    echo "Please rerun with: sudo ./install.sh --system" >&2
    exit 1
fi

# A root-installed launcher that points into a tree other users can edit is a privilege escalation.
if [ "$MODE" = "system" ] && [ "$INSTALL_TYPE" = "symlink" ]; then
    for d in "${SCRIPT_DIR}" "${SCRIPT_DIR}/src"; do
        if ! "$PYTHON_BIN" -c 'import os,sys; s=os.stat(sys.argv[1]); sys.exit(0 if s.st_uid == 0 and not s.st_mode & 0o022 else 1)' "$d"; then
            echo "Error: --system --symlink needs a root-owned source tree that is not group/world-writable (${d})." >&2
            echo "Use the default --copy for system installs." >&2
            exit 1
        fi
    done
fi

# 1. Create target directories
# RUN_DIR (/run is tmpfs) is created by the service at runtime, not at install time (FHS).
run_cmd mkdir -p "${BIN_DIR}" "${LIB_DIR}" "${CONF_DIR}" "${DATA_DIR}" "${CACHE_DIR}"

# 2. Deploy library files
if [ "$INSTALL_TYPE" = "symlink" ]; then
    echo "Linking source modules to ${LIB_DIR}/src..."
    run_cmd rm -rf "${LIB_DIR}/src"
    run_cmd ln -sfn "${SCRIPT_DIR}/src" "${LIB_DIR}/src"
else
    echo "Copying source modules to ${LIB_DIR}/src..."
    run_cmd rm -rf "${LIB_DIR}/src"
    run_cmd cp -a "${SCRIPT_DIR}/src" "${LIB_DIR}/src"
fi

# 3. Create standalone launcher script in BIN_DIR
LAUNCHER="${BIN_DIR}/extscope"
echo "Generating launcher at ${LAUNCHER}..."

if [ "$DRY_RUN" = false ]; then
    rm -f "${LAUNCHER}"
    cat <<'LAUNCHER_EOF' > "${LAUNCHER}"
#!/usr/bin/env bash
# ==============================================================================
# ExtScope CLI Gateway (Strict Native Engine - No Legacy Fallback)
# Legacy subcommands return OBSOLETE error.
# ==============================================================================
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
EXTSCOPE_LIB="$(dirname "$(dirname "$SELF")")/lib/extscope/src"
NATIVE_CMDS=("validate" "digest" "plugin-plan" "plan" "bind" "verify" "dag" "query" "index" "resolve" "where" "init" "new" "pkg")

cmd="${1:-}"

# Check for version
if [ "$cmd" = "--version" ] || [ "$cmd" = "-v" ]; then
    echo "extscope 0.1.0a6 (native core: ${EXTSCOPE_LIB})"
    exit 0
fi

# Check for global help
if [ "$cmd" = "--help" ] || [ "$cmd" = "-h" ] || [ -z "$cmd" ]; then
    cat << 'HELP_EOF'
ExtScope CLI (Standalone Native FHS 3.0 Core)
Version: 0.1.0a6

Available Subcommands:
  validate       Structural validation of scope declarations
  digest         Read directory identity (extscope.tree.v1)
  plugin-plan    Preview pinned plugin plans
  plan           Flatten and preview binding plan
  bind           Generate canonical bindings with allowed roots
  verify         Verify bindings against receipt
  dag            Dependency graph (compact|mermaid|json|markdown)
  query          Item provenance lookup
  index          List scopes under explicit files/directories
  resolve        Find scope files by uuid or name
  where          Which scope declares a source path, and its git repo (read-only)
  init | new     Print a valid scope template (writes nothing)
  pkg            list | doctor | override-check on an explicit package root

Note: Legacy subcommands (pkg, apply-host, lint, dag, flatten, run, hub, gate)
are not part of this core. Distribution (apply/rollback) is the separate
extscope-dist command.
HELP_EOF
    exit 0
fi

# Determine if the requested subcommand belongs to Native Core
for n in "${NATIVE_CMDS[@]}"; do
    if [ "$cmd" = "$n" ]; then
        if [ -d "$EXTSCOPE_LIB" ]; then
            export PYTHONPATH="${EXTSCOPE_LIB}${PYTHONPATH:+:$PYTHONPATH}"
        fi
        export EXTSCOPE_MODE="${EXTSCOPE_MODE:-__MODE__}"
        exec "${EXTSCOPE_PYTHON:-python3}" -m extscope "$@"
    fi
done

# Any legacy or unknown subcommand is strictly rejected as obsolete
echo "Error: Subcommand '${cmd}' is obsolete." >&2
echo "File distribution is provided by the separate extscope-dist command." >&2
echo "Native extscope supports: validate, digest, plugin-plan, plan, bind, verify, dag, query, index, resolve, where, init, pkg." >&2
exit 2
LAUNCHER_EOF
    sed -i "s/__MODE__/${MODE}/" "${LAUNCHER}"
    chmod 755 "${LAUNCHER}"
fi

# 4. extscope-dist (independent module; shares no code with the core)
DIST_LIB="${TARGET_PREFIX}/lib/extscope-dist"
if [ -d "${SCRIPT_DIR}/extscope-dist/src" ]; then
    echo "Installing extscope-dist to ${DIST_LIB}..."
    run_cmd mkdir -p "${DIST_LIB}"
    run_cmd rm -rf "${DIST_LIB}/src"
    if [ "$INSTALL_TYPE" = "symlink" ]; then
        run_cmd ln -sfn "${SCRIPT_DIR}/extscope-dist/src" "${DIST_LIB}/src"
    else
        run_cmd cp -a "${SCRIPT_DIR}/extscope-dist/src" "${DIST_LIB}/src"
    fi
    if [ "$DRY_RUN" = false ]; then
        rm -f "${BIN_DIR}/extscope-dist"
        cat > "${BIN_DIR}/extscope-dist" <<'DIST_EOF'
#!/usr/bin/env bash
set -euo pipefail
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(dirname "$(dirname "$SELF")")/lib"
export PYTHONPATH="${ROOT}/extscope-dist/src:${ROOT}/extscope-modules/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${EXTSCOPE_PYTHON:-python3}" -m extscope_dist "$@"
DIST_EOF
        chmod 755 "${BIN_DIR}/extscope-dist"
    fi
fi

# 5. extscope-modules (optional dist modules; only active when a dist config enables them)
MOD_LIB="${TARGET_PREFIX}/lib/extscope-modules"
if [ -d "${SCRIPT_DIR}/extscope-modules/src" ]; then
    echo "Installing extscope-modules to ${MOD_LIB}..."
    run_cmd mkdir -p "${MOD_LIB}"
    run_cmd rm -rf "${MOD_LIB}/src"
    if [ "$INSTALL_TYPE" = "symlink" ]; then
        run_cmd ln -sfn "${SCRIPT_DIR}/extscope-modules/src" "${MOD_LIB}/src"
    else
        run_cmd cp -a "${SCRIPT_DIR}/extscope-modules/src" "${MOD_LIB}/src"
    fi
fi

# 6. extscope-container and extscope-tools (independent CLIs; each has its own library dir)
install_cli() {   # $1 = package dir name, $2 = python module
    local pkg="$1" mod="$2" lib="${TARGET_PREFIX}/lib/$1"
    [ -d "${SCRIPT_DIR}/${pkg}/src" ] || return 0
    echo "Installing ${pkg} to ${lib}..."
    run_cmd mkdir -p "${lib}"
    run_cmd rm -rf "${lib}/src"
    if [ "$INSTALL_TYPE" = "symlink" ]; then
        run_cmd ln -sfn "${SCRIPT_DIR}/${pkg}/src" "${lib}/src"
    else
        run_cmd cp -a "${SCRIPT_DIR}/${pkg}/src" "${lib}/src"
    fi
    if [ "$DRY_RUN" = false ]; then
        rm -f "${BIN_DIR}/${pkg}"
        cat > "${BIN_DIR}/${pkg}" <<CLI_EOF
#!/usr/bin/env bash
set -euo pipefail
SELF="\$(readlink -f "\${BASH_SOURCE[0]}")"
export PYTHONPATH="\$(dirname "\$(dirname "\$SELF")")/lib/${pkg}/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "\${EXTSCOPE_PYTHON:-python3}" -m ${mod} "\$@"
CLI_EOF
        chmod 755 "${BIN_DIR}/${pkg}"
    fi
}
install_cli extscope-container extscope_container
install_cli extscope-tools extscope_tools

# 7. extscope-harness (agent-specific planner; scripts, not a Python package). It writes nothing
#    when run with --emit-dist-plan; extscope-dist does the writing.
HARNESS_LIB="${TARGET_PREFIX}/lib/extscope-harness"
if [ -d "${SCRIPT_DIR}/extscope-harness/adapters" ]; then
    echo "Installing extscope-harness to ${HARNESS_LIB}..."
    run_cmd rm -rf "${HARNESS_LIB}"
    run_cmd mkdir -p "${HARNESS_LIB}"
    for part in adapters manifests skills spec README.md; do
        if [ "$INSTALL_TYPE" = "symlink" ]; then
            run_cmd ln -sfn "${SCRIPT_DIR}/extscope-harness/${part}" "${HARNESS_LIB}/${part}"
        else
            run_cmd cp -a "${SCRIPT_DIR}/extscope-harness/${part}" "${HARNESS_LIB}/${part}"
        fi
    done
    if [ "$DRY_RUN" = false ]; then
        rm -f "${BIN_DIR}/extscope-harness"
        cat > "${BIN_DIR}/extscope-harness" <<'HARNESS_EOF'
#!/usr/bin/env bash
set -euo pipefail
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(dirname "$(dirname "$SELF")")/lib/extscope-harness"
exec "${EXTSCOPE_PYTHON:-python3}" "${ROOT}/adapters/project-harness.py" --manifests-dir "${ROOT}/manifests" "$@"
HARNESS_EOF
        chmod 755 "${BIN_DIR}/extscope-harness"
    fi
fi

# Copies carry no bytecode from the source tree (stale or owned by another user).
if [ "$DRY_RUN" = false ] && [ "$INSTALL_TYPE" = "copy" ]; then
    find "${TARGET_PREFIX}/lib" -maxdepth 4 -path "${TARGET_PREFIX}/lib/extscope*" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi

echo ""
echo "✅ ExtScope successfully installed!"
echo "Verify installation:"
echo "  ${LAUNCHER} --version"
echo ""

if [ "$DRY_RUN" = false ]; then
    "${LAUNCHER}" --version || true
fi
