#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Read-only: does each installed package copy match this repo? exit 0 = identical, 1 = drift.
# usage: tools/check-installed.sh [PREFIX]   (default /usr/local; lib layout = install.sh --copy)
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="${1:-/usr/local}/lib"
rc=0
check() {   # $1 label, $2 repo dir, $3 installed dir
    if [ ! -d "$3" ]; then echo "MISSING  $1 ($3)"; rc=1; return; fi
    out="$(diff -rq -x __pycache__ "$2" "$3" 2>&1)" && echo "ok       $1" || { echo "DRIFT    $1"; echo "$out" | sed 's/^/           /'; rc=1; }
}
for d in adapters manifests skills spec; do check "extscope-harness/$d" "$REPO/extscope-harness/$d" "$LIB/extscope-harness/$d"; done
for pkg in extscope extscope-container extscope-dist extscope-modules extscope-tools; do
    [ "$pkg" = extscope ] && src="$REPO/src" || src="$REPO/$pkg/src"
    check "$pkg" "$src" "$LIB/$pkg/src"
done
exit $rc
