#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Project Harness Dispatcher — generic in-container agent projection dispatcher.

Design:
- Python 3 standard library only (zero external dependencies).
- Reads the neutral asset directory given by --assets-root (skills/ rules/ fragments/ mcp/) and projects it according to the target harness manifest. The consumer supplies the directory; the tool assumes no default location.
- Fully idempotent: safe to re-run, repairs broken links.
"""

import argparse
import filecmp
import json
import os
import re
import shutil
import stat
import tempfile
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox_probe import ProbeReason, ProbeResult, probe_sandbox_access  # noqa: E402


class Tier(str, Enum):
    """Tier actually used by tiered dispatch; replaces unchecked string constants."""

    TIER1_SYMLINK = "tier1_symlink"
    TIER2_MATERIALIZED = "tier2_materialized"


def expand_path(template: str, workspace: Path, user_home: Path) -> Path:
    """Manifest paths are relative to the home dir (`~/x`) or the project dir (`./x`) and nothing else.

    The two roots come from --user-home / --workspace at run time; a manifest never names a host path.
    """
    if not isinstance(template, str):
        raise ValueError("manifest path must be a string")
    for token, root in (("~", user_home), (".", workspace)):
        if template == token or template == token + "/":
            return Path(root)
        if template.startswith(token + "/"):
            rel = template[2:]
            if ".." in rel.split("/") or rel.startswith("/"):
                break
            return Path(root) / rel
    raise ValueError(f"manifest path must be '~/<path>' or './<path>', got {template!r}")


def log(msg: str, verbose: bool = False, is_verbose_msg: bool = False) -> None:
    if is_verbose_msg and not verbose:
        return
    print(f"[Harness-Dispatcher] {msg}", file=sys.stderr)


def ensure_symlink(src: Path, dst: Path, verbose: bool = False) -> bool:
    """Ensure a valid symlink dst -> src exists."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        try:
            current_target = dst.resolve()
            if current_target == src.resolve() or str(os.readlink(dst)) == str(src):
                return False  # already present and correct
        except Exception:
            pass
        dst.unlink()
    elif dst.exists():
        log(f"warning: {dst} exists and is not a symlink; not replacing it to avoid data loss", verbose)
        return False

    os.symlink(str(src), str(dst))
    log(f"created symlink: {dst} -> {src}", verbose, is_verbose_msg=True)
    return True


def _normalize_permissions(root: Path) -> None:
    """Ensure dereferenced directories/files are at least 0755/0644 so low-privilege sandbox processes can read them."""
    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        try:
            current = stat.S_IMODE(dir_path.stat().st_mode)
            dir_path.chmod(current | 0o755)
        except OSError:
            pass
        for name in filenames:
            file_path = dir_path / name
            try:
                current = stat.S_IMODE(file_path.stat().st_mode)
                file_path.chmod(current | 0o644)
            except OSError:
                pass


def _trees_equal(a: Path, b: Path) -> bool:
    """Recursively compare two directories for equivalence (used for idempotency: equivalent means no write is needed)."""
    if not a.exists() or not b.exists():
        return False
    comparison = filecmp.dircmp(a, b)
    if comparison.left_only or comparison.right_only or comparison.diff_files or comparison.funny_files:
        return False
    for sub in comparison.common_dirs:
        if not _trees_equal(a / sub, b / sub):
            return False
    return True


def _assert_no_symlink_loop(src: Path) -> None:
    """Refuse a source tree where a symlink leads back into a directory already being copied
    (self or mutual loops); copytree would recurse forever. Links that point outside the tree
    are followed on purpose (C-01): the source is operator-owned. Keep in step with
    extscope_dist.engine._assert_tree_contained, which also refuses links that leave the tree."""
    def walk(path: str, chain: tuple) -> None:
        real = os.path.realpath(path)
        if real in chain:
            raise SystemExit(f"error: symlink_loop: {path}")
        chain = chain + (real,)
        for name in sorted(os.listdir(path)):
            child = os.path.join(path, name)
            if os.path.isdir(child):
                walk(child, chain)

    walk(str(src), ())


def ensure_materialized_copy(src: Path, dst: Path, verbose: bool = False) -> bool:
    """C-01: materialized copy (dereferenced).

    Uses `shutil.copytree(symlinks=False)` to resolve every nested symlink in src and write the result to
    dst, so the target is plain files from the sandbox's point of view. Idempotent: identical content is not rewritten.
    """
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists() and not dst.is_dir():
        log(f"warning: {dst} exists and is not a directory; skipping materialized copy to avoid data loss", verbose)
        return False

    if dst.exists() and _trees_equal(src, dst):
        return False

    _assert_no_symlink_loop(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)
    _normalize_permissions(dst)
    log(f"materialized copy: {dst} <- {src}", verbose, is_verbose_msg=True)
    return True


def dispatch_tiered(
    src: Path,
    dst: Path,
    allowed_prefixes: list[str],
    verbose: bool = False,
    fallback_target_dir: Path | None = None,
) -> Tier:
    """C-02: tiered dispatch.

    Tier 1 (default): distribute as a symlink at `dst` (the project workspace `.agents/`).
    Probe: call sandbox_probe to check that the sandboxed process can really read the target. `allowed_prefixes`
    must contain only paths the sandboxed process can actually see (normally the workspace itself); it must not include
    canonical_root: canonical assets live outside the workspace, which is exactly what the probe is meant to detect;
    listing it as an allowed prefix would make every legitimate but out-of-bounds Tier 1 symlink count
    as visible, so the Tier 2 downgrade could never trigger (see spec TRUTH-2).
    Tier 2 (specialized): when the probe is blocked, fall back to a materialized copy:
      - option 1 (default): overwrite `dst` in place; the path does not change.
      - option 2: if the manifest provides `fallback_target_dir` (e.g. `~/.codex/skills`),
        also copy into that directory for agents that read from it.
    """
    ensure_symlink(src, dst, verbose)
    probe = probe_sandbox_access(dst, allowed_prefixes)
    if probe.ok:
        return Tier.TIER1_SYMLINK

    log(f"probe blocked ({probe.reason}); falling back to materialized copy: {dst}", verbose)
    ensure_materialized_copy(src, dst, verbose)

    if fallback_target_dir is not None:
        fallback_dst = fallback_target_dir / src.name
        ensure_materialized_copy(src, fallback_dst, verbose)

    return Tier.TIER2_MATERIALIZED


def inject_md_block(dst_file: Path, block_id: str, content: str, verbose: bool = False) -> bool:
    """Idempotently inject a block delimited by sentinel comments into a markdown file."""
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    open_tag = f"<!-- block: {block_id} -->"
    close_tag = f"<!-- /block: {block_id} -->"
    block_text = f"{open_tag}\n{content.strip()}\n{close_tag}\n"

    existing = ""
    if dst_file.exists():
        existing = dst_file.read_text(encoding="utf-8")

    pattern = re.compile(rf"{re.escape(open_tag)}.*?{re.escape(close_tag)}\n?", re.DOTALL)
    if pattern.search(existing):
        new_content = pattern.sub(block_text, existing)
    else:
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        new_content = existing.rstrip("\n") + ("\n" if existing else "") + separator + block_text

    if new_content != existing:
        dst_file.write_text(new_content, encoding="utf-8")
        log(f"updated markdown block [{block_id}] in {dst_file}", verbose, is_verbose_msg=True)
        return True
    return False


def ensure_codex_trust(config_file: Path, trusted_paths: list[str], verbose: bool = False) -> None:
    """Ensure the Codex config.toml marks the given path as trust_level = 'trusted'."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""

    modified = False
    for path in trusted_paths:
        section = f'[projects."{path}"]'
        if section not in content:
            block = f'\n{section}\ntrust_level = "trusted"\n'
            content += block
            modified = True
            log(f"added Codex trusted path to {config_file}: {path}", verbose)

    if modified:
        config_file.write_text(content, encoding="utf-8")


def ensure_extra_dirs_registration(config_file: Path, extra_dirs: list[str], verbose: bool = False) -> bool:
    """C-03: active registration in the agent config (scheme 3).

    For agents that support extra scan paths, incrementally inject a top-level `extra_dirs`
    array into their config without touching existing keys; before writing, validate with `tomllib` (read-only parser) that the result is
    still valid TOML. If validation fails, abort the write and report an error so the user's config is never corrupted.
    """
    import tomllib

    config_file.parent.mkdir(parents=True, exist_ok=True)
    content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""

    existing_dirs: list[str] = []
    pattern = re.compile(r'^extra_dirs\s*=\s*\[(.*?)\]', re.MULTILINE | re.DOTALL)
    match = pattern.search(content)
    if match:
        existing_dirs = re.findall(r'"([^"]*)"', match.group(1))

    missing = [d for d in extra_dirs if d not in existing_dirs]
    if not missing:
        return False

    merged = existing_dirs + missing
    array_literal = "[" + ", ".join(f'"{d}"' for d in merged) + "]"
    line = f"extra_dirs = {array_literal}\n"

    if match:
        new_content = pattern.sub(line.rstrip("\n"), content, count=1)
    else:
        # a top-level key must come before the first section header, otherwise TOML assigns it to that section.
        first_section = re.search(r"(?m)^\[", content)
        if first_section is None:
            new_content = content.rstrip("\n") + ("\n" if content else "") + line
        else:
            insert_at = first_section.start()
            new_content = content[:insert_at] + line + content[insert_at:]

    try:
        tomllib.loads(new_content)
    except tomllib.TOMLDecodeError as e:
        log(f"error: registering extra_dirs would produce invalid TOML; not writing {config_file}: {e}", verbose)
        return False

    config_file.write_text(new_content, encoding="utf-8")
    log(f"registered extra_dirs in {config_file}: {missing}", verbose)
    return True


def _project_skills(
    cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, tiered: bool, verbose: bool
) -> None:
    mode = cfg.get("mode", "symlink_children")
    target_dir = expand_path(cfg["target_dir"], workspace, user_home)
    target_dir.mkdir(parents=True, exist_ok=True)

    fallback_target_dir = None
    if cfg.get("fallback_target_dir"):
        fallback_target_dir = expand_path(cfg["fallback_target_dir"], workspace, user_home)
        fallback_target_dir.mkdir(parents=True, exist_ok=True)

    extra_dirs_cfg = cfg.get("extra_dirs_registration")

    canonical_skills = canonical_root / "skills"
    if not canonical_skills.exists():
        return

    for skill_path in canonical_skills.iterdir():
        if not skill_path.is_dir():
            continue
        dst_skill = target_dir / skill_path.name
        if mode == "materialized_copy":
            # The manifest asks for it explicitly: materialize directly (skip Tier 1 and the probe).
            ensure_materialized_copy(skill_path, dst_skill, verbose)
        elif tiered:
            # allowed_prefixes holds only the workspace: canonical_root lives outside it,
            # which is the out-of-bounds case the probe must detect, so it is not pre-allowed (see the dispatch_tiered docstring).
            tier = dispatch_tiered(skill_path, dst_skill, [str(workspace)], verbose, fallback_target_dir)
            if tier == Tier.TIER2_MATERIALIZED and extra_dirs_cfg:
                config_file = expand_path(extra_dirs_cfg["config_file"], workspace, user_home)
                ensure_extra_dirs_registration(config_file, [str(target_dir)], verbose)
        else:
            # Existing default path: unchanged passive symlink distribution.
            ensure_symlink(skill_path, dst_skill, verbose)


def _project_rules(cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool) -> None:
    mode = cfg.get("mode", "symlink_children")
    canonical_rules = canonical_root / "rules"
    if not canonical_rules.exists():
        return

    if mode == "symlink_children":
        target_dir = expand_path(cfg["target_dir"], workspace, user_home)
        target_dir.mkdir(parents=True, exist_ok=True)
        for rule_path in canonical_rules.iterdir():
            if rule_path.is_file():
                ensure_symlink(rule_path, target_dir / rule_path.name, verbose)

    elif mode == "md_block_append":
        target_file = expand_path(cfg["target_file"], workspace, user_home)
        for rule_path in canonical_rules.iterdir():
            if rule_path.is_file():
                content = rule_path.read_text(encoding="utf-8")
                inject_md_block(target_file, f"rule-{rule_path.stem}", content, verbose)


def _project_fragments(cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool) -> None:
    target_file = expand_path(cfg["target_file"], workspace, user_home)
    canonical_fragments = canonical_root / "fragments"
    if not canonical_fragments.exists():
        return

    for frag_path in canonical_fragments.iterdir():
        if frag_path.is_file():
            content = frag_path.read_text(encoding="utf-8")
            inject_md_block(target_file, frag_path.stem, content, verbose)


def _expand_mcp_val(val, workspace: Path, user_home: Path):
    """Recursively expand the /workspace and /root placeholders in MCP fields."""
    if isinstance(val, str):
        return val.replace("/workspace", str(workspace)).replace("/root", str(user_home))
    elif isinstance(val, list):
        return [_expand_mcp_val(x, workspace, user_home) for x in val]
    elif isinstance(val, dict):
        return {k: _expand_mcp_val(v, workspace, user_home) for k, v in val.items()}
    return val


def _load_canonical_mcp_servers(canonical_mcp: Path, workspace: Path, user_home: Path) -> dict[str, dict]:
    """Read every MCP server definition from canonical/mcp/ and normalize to {server_name: server_def}."""
    servers: dict[str, dict] = {}
    if not canonical_mcp.exists():
        return servers

    for json_file in sorted(canonical_mcp.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError("invalid canonical MCP JSON") from None
        if not isinstance(data, dict):
            raise ValueError("invalid canonical MCP JSON object")

        expanded = _expand_mcp_val(data, workspace, user_home)
        if "mcpServers" in expanded and isinstance(expanded["mcpServers"], dict):
            for name, s_def in expanded["mcpServers"].items():
                if isinstance(s_def, dict):
                    servers[name] = s_def
        elif "command" in expanded:
            name = expanded.pop("name", None) or json_file.stem
            servers[name] = expanded
        else:
            for k, v in expanded.items():
                if not k.startswith(("$", "_")) and isinstance(v, dict) and "command" in v:
                    servers[k] = v
    return servers


def _merge_json_mcp(target_file: Path, servers: dict[str, dict], verbose: bool = False, *, check_only: bool = False) -> bool:
    """Merge MCP servers into the target JSON config (json_merge)."""
    try:
        st = target_file.lstat()
    except FileNotFoundError:
        st = None

    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise ValueError("Target file is invalid: must be a regular file owned by current user and not a symlink")
        orig_mode = stat.S_IMODE(st.st_mode)
        try:
            fd = os.open(target_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                        or (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino)):
                    raise ValueError("target changed before read")
                chunks = bytearray()
                limit = 4 * 1024 * 1024
                while len(chunks) <= limit:
                    block = os.read(fd, min(65536, limit + 1 - len(chunks)))
                    if not block:
                        break
                    chunks.extend(block)
                if len(chunks) > limit:
                    raise ValueError("target JSON exceeds size limit")
                content = chunks.decode("utf-8")
            finally:
                os.close(fd)
        except Exception:
            raise ValueError("Failed to read target JSON file") from None
        if not content.strip():
            raise ValueError("Target JSON file is empty")
        try:
            loaded = json.loads(content)
        except Exception:
            raise ValueError("Failed to parse target JSON file") from None
        if not isinstance(loaded, dict):
            raise ValueError("Target JSON root must be an object")
        if "mcpServers" in loaded and not isinstance(loaded["mcpServers"], dict):
            raise ValueError("Target JSON mcpServers must be an object")
        existing_data = loaded
    else:
        orig_mode = 0o600
        existing_data = {}

    if "mcpServers" not in existing_data:
        existing_data["mcpServers"] = {}

    mcp_servers = existing_data["mcpServers"]
    modified = False
    for name, s_def in servers.items():
        if name not in mcp_servers or mcp_servers[name] != s_def:
            mcp_servers[name] = s_def
            modified = True

    if check_only:
        return modified or st is None
    if not modified and st is not None:
        return False

    target_file.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(existing_data, indent=2, ensure_ascii=False) + "\n"

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_file.parent,
            delete=False,
        ) as tf:
            temp_path = Path(tf.name)
            tf.write(new_text)
            tf.flush()
            os.fchmod(tf.fileno(), orig_mode)
            os.fsync(tf.fileno())

        if st is None:
            # No-clobber publication if another writer created the missing target.
            os.link(temp_path, target_file)
            temp_path.unlink()
        else:
            current = target_file.lstat()
            fingerprint = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                         value.st_mtime_ns, value.st_ctime_ns, value.st_mode)
            if fingerprint(current) != fingerprint(st):
                raise ValueError("Target changed before publication")
            os.replace(temp_path, target_file)
        temp_path = None

        dir_fd = os.open(target_file.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

    log(f"merged MCP config into {target_file}", verbose, is_verbose_msg=True)
    return True


def _format_codex_mcp_section(name: str, s_def: dict) -> str:
    """Format one MCP server as a Codex TOML block."""
    lines = [f'[mcp_servers."{name}"]']
    if "command" in s_def:
        lines.append(f'command = {json.dumps(s_def["command"], ensure_ascii=False)}')
    if "args" in s_def and isinstance(s_def["args"], list):
        lines.append(f'args = {json.dumps(s_def["args"], ensure_ascii=False)}')
    if "env" in s_def and isinstance(s_def["env"], dict):
        env_pairs = [
            f'{json.dumps(k, ensure_ascii=False)} = {json.dumps(str(v), ensure_ascii=False)}'
            for k, v in s_def["env"].items()
        ]
        lines.append(f'env = {{ {", ".join(env_pairs)} }}')
    if "cwd" in s_def:
        lines.append(f'cwd = {json.dumps(s_def["cwd"], ensure_ascii=False)}')
    return "\n".join(lines) + "\n"


def _merge_toml_mcp(target_file: Path, servers: dict[str, dict], verbose: bool = False) -> bool:
    """Merge MCP servers into the target TOML config (toml_mcp_merge)."""
    import tomllib

    target_file.parent.mkdir(parents=True, exist_ok=True)
    content = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    initial_content = content

    for name, s_def in servers.items():
        block = _format_codex_mcp_section(name, s_def)
        pattern = re.compile(
            rf'(?m)^\[mcp_servers\.(?:"{re.escape(name)}"|{re.escape(name)})\]\s*\n.*?(?=\n\[|\Z)',
            re.DOTALL,
        )
        if pattern.search(content):
            content = pattern.sub(block.rstrip("\n") + "\n", content)
        else:
            sep = "\n\n" if content.strip() else ""
            content = content.rstrip("\n") + sep + block

    if content != initial_content:
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as e:
            log(f"error: MCP TOML merge would produce invalid TOML; not writing {target_file}: {e}", verbose)
            return False

        target_file.write_text(content, encoding="utf-8")
        log(f"merged MCP TOML config into {target_file}", verbose, is_verbose_msg=True)
        return True
    return False


def _project_mcp(
    cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool
) -> None:
    """C-05: dynamic merge projection of MCP tool config."""
    canonical_mcp = canonical_root / "mcp"
    if not canonical_mcp.exists():
        return

    servers = _load_canonical_mcp_servers(canonical_mcp, workspace, user_home)
    if not servers:
        return

    mode = cfg.get("mode", "json_merge")
    target_file = expand_path(cfg["target_file"], workspace, user_home)

    if mode == "json_merge":
        _merge_json_mcp(target_file, servers, verbose)
    elif mode == "toml_mcp_merge":
        _merge_toml_mcp(target_file, servers, verbose)
    else:
        log(f"warning: unsupported MCP projection mode [{mode}] for {target_file}", verbose)


def _run_runtime_bootstrap(bootstrap: dict, workspace: Path, user_home: Path, verbose: bool, extra_trust=()) -> None:
    b_cfg = bootstrap.get("ensure_workspace_trust")
    if not b_cfg or not b_cfg.get("enabled", False):
        return

    cfg_file = expand_path(b_cfg["config_file"], workspace, user_home)
    trusted_paths = [str(expand_path(t, workspace, user_home)) for t in b_cfg.get("trusted_paths", ["./"])] + list(extra_trust)
    ensure_codex_trust(cfg_file, trusted_paths, verbose)


def apply_harness(
    harness_id: str,
    manifest: dict,
    canonical_root: Path,
    workspace: Path,
    user_home: Path,
    verbose: bool = False,
    extra_trust=(),
) -> None:
    preflight_harness(manifest, canonical_root, workspace, user_home)
    log(f"applying harness [{harness_id}] ({manifest.get('name')})...", verbose)

    projections = manifest.get("projections", {})
    tiered = manifest.get("tiered", False)

    if "skills" in projections:
        _project_skills(projections["skills"], canonical_root, workspace, user_home, tiered, verbose)

    if "rules" in projections:
        _project_rules(projections["rules"], canonical_root, workspace, user_home, verbose)

    if "fragments" in projections:
        _project_fragments(projections["fragments"], canonical_root, workspace, user_home, verbose)

    if "mcp" in projections:
        _project_mcp(projections["mcp"], canonical_root, workspace, user_home, verbose)

    _run_runtime_bootstrap(manifest.get("runtime_bootstrap", {}), workspace, user_home, verbose, extra_trust)

    log(f"harness [{harness_id}] projection done.", verbose)


def verify_harness(manifest: dict, workspace: Path, user_home: Path, verbose: bool = False) -> list[ProbeResult]:
    """C-04 / T-04: fail-closed gate.

    Runs only when the manifest declares `"tiered": true` (backward compatible: older manifests are unaffected).
    Re-probes every projected skill and returns all failing ProbeResults. `allowed_prefixes`
    holds only the workspace, for the same reason as in `dispatch_tiered`: canonical_root is the out-of-bounds case
    the probe must detect and cannot be pre-allowed, so this function neither needs nor accepts a canonical_root argument.
    """
    if not manifest.get("tiered", False):
        return []

    projections = manifest.get("projections", {})
    if "skills" not in projections:
        return []

    cfg = projections["skills"]
    target_dir = expand_path(cfg["target_dir"], workspace, user_home)
    if not target_dir.exists():
        # Fail closed: if the target directory should exist after projection but does not (apply_harness always
        # runs mkdir for the skills section), treat it as a projection failure rather than "nothing to verify", to avoid a false success.
        return [ProbeResult(ok=False, reason=ProbeReason.TARGET_NOT_FOUND, path=str(target_dir))]

    failures: list[ProbeResult] = []
    allowed_prefixes = [str(workspace)]
    for skill_dir in target_dir.iterdir():
        if skill_dir.is_dir() or skill_dir.is_symlink():
            result = probe_sandbox_access(skill_dir, allowed_prefixes)
            if not result.ok:
                failures.append(result)
                log(f"fail-closed probe failed: {skill_dir} ({result.reason})", verbose)

    return failures


def preflight_harness(manifest: dict, canonical_root: Path, workspace: Path, user_home: Path) -> None:
    """Validate required input and existing config before any home mutation."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("projections"), dict):
        raise ValueError("invalid harness manifest")
    if not canonical_root.is_dir():
        raise ValueError("canonical asset root is missing")
    projections = manifest["projections"]
    for cfg_ in projections.values():
        for key in ("target_dir", "target_file", "fallback_target_dir"):
            if isinstance(cfg_, dict) and key in cfg_:
                expand_path(cfg_[key], workspace, user_home)
        if isinstance(cfg_, dict) and "extra_dirs_registration" in cfg_:
            expand_path(cfg_["extra_dirs_registration"].get("config_file"), workspace, user_home)
    boot = (manifest.get("runtime_bootstrap") or {}).get("ensure_workspace_trust")
    if boot:
        expand_path(boot.get("config_file"), workspace, user_home)
        for t_ in boot.get("trusted_paths", ["./"]):
            expand_path(t_, workspace, user_home)
    known = {"skills", "rules", "fragments", "mcp"}
    if not projections or set(projections) - known:
        raise ValueError("unsupported or empty harness projections")
    required = manifest.get("required_assets", [])
    if not isinstance(required, list) or any(kind not in known for kind in required):
        raise ValueError("invalid required assets")
    if any(kind not in projections for kind in required):
        raise ValueError("required projection is not configured")
    present = set()
    for kind, config in projections.items():
        if not isinstance(config, dict):
            raise ValueError("invalid projection config")
        modes = {"skills": {"symlink_children", "materialized_copy"},
                 "rules": {"symlink_children", "md_block_append"},
                 "fragments": {"md_block_append"},
                 "mcp": {"json_merge", "toml_mcp_merge"}}
        default_mode = {"skills": "symlink_children", "rules": "symlink_children",
                        "fragments": "md_block_append", "mcp": "json_merge"}[kind]
        if config.get("mode", default_mode) not in modes[kind]:
            raise ValueError("unsupported projection mode")
        target_key = "target_dir" if kind == "skills" or (kind == "rules" and config.get("mode", default_mode) == "symlink_children") else "target_file"
        if not isinstance(config.get(target_key), str) or not config[target_key]:
            raise ValueError("missing projection target")
        asset_dir = canonical_root / kind
        if asset_dir.is_dir():
            if kind == "skills":
                found = any(p.is_dir() and (p / "SKILL.md").is_file() for p in asset_dir.iterdir())
            else:
                found = any(p.is_file() for p in asset_dir.glob("*.json" if kind == "mcp" else "*.md"))
            if found:
                present.add(kind)
        if kind in required and kind not in present:
            raise ValueError("required canonical assets are missing")
        if kind == "mcp":
            for source in sorted(asset_dir.glob("*.json")) if asset_dir.is_dir() else ():
                try:
                    doc = json.loads(source.read_text(encoding="utf-8"))
                except Exception:
                    raise ValueError("invalid canonical MCP JSON") from None
                if not isinstance(doc, dict) or ("mcpServers" in doc and not isinstance(doc["mcpServers"], dict)):
                    raise ValueError("invalid canonical MCP JSON object")
            servers = _load_canonical_mcp_servers(asset_dir, workspace, user_home)
            if not servers:
                present.discard("mcp")
            if "mcp" in required and not servers:
                raise ValueError("required MCP servers are missing")
            mode = config.get("mode", "json_merge")
            if mode not in ("json_merge", "toml_mcp_merge") or "target_file" not in config:
                raise ValueError("invalid MCP projection config")
            target = expand_path(config["target_file"], workspace, user_home)
            if mode == "json_merge":
                _merge_json_mcp(target, servers, check_only=True)
            elif target.exists() or target.is_symlink():
                import tomllib
                st = target.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                    raise ValueError("invalid MCP TOML target")
                try:
                    tomllib.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    raise ValueError("invalid existing MCP TOML") from None
    if not present:
        raise ValueError("canonical contains no usable assets for this harness")


DIST_PLAN_SCHEMA = "extscope-dist.plan.v1"


def plan_harness(manifest: dict, canonical_root: Path, workspace: Path, user_home: Path, extra_trust=()) -> tuple[list[dict], list[str]]:
    """Decide what goes where, without writing anything.

    Returns (items, residual). `items` are extscope-dist plan items for everything a
    copy / symlink / md_block / config_merge (extscope-modules) can express; dist must
    have modules.enable = ["extscope_modules"] or it refuses them. `residual` names what
    still needs agent-specific logic (sandbox-probe tiering, other bootstrap steps);
    the adapter's own apply path still owns those.
    """
    projections = manifest["projections"]
    canonical_root = Path(os.path.abspath(canonical_root))
    items: list[dict] = []
    residual: list[str] = []

    def target(template: str) -> str:
        return os.path.abspath(expand_path(template, workspace, user_home))

    def block(src: Path, dst: str, block_id: str) -> None:
        items.append({"method": "md_block", "src": str(src), "target": dst, "block_id": block_id})

    skills = projections.get("skills")
    if skills is not None:
        if manifest.get("tiered", False) or skills.get("fallback_target_dir") or skills.get("extra_dirs_registration"):
            residual.append("skills: tiered/fallback/extra_dirs needs a sandbox probe")
        else:
            method = "copy" if skills.get("mode", "symlink_children") == "materialized_copy" else "symlink"
            root = canonical_root / "skills"
            for path in sorted(root.iterdir()) if root.is_dir() else ():
                if path.is_dir():
                    items.append({"method": method, "src": str(path),
                                  "target": os.path.join(target(skills["target_dir"]), path.name)})

    rules = projections.get("rules")
    if rules is not None:
        root = canonical_root / "rules"
        files = [f for f in sorted(root.iterdir()) if f.is_file()] if root.is_dir() else []
        if rules.get("mode", "symlink_children") == "md_block_append":
            for f in files:
                block(f, target(rules["target_file"]), f"rule-{f.stem}")
        else:
            for f in files:
                items.append({"method": "symlink", "src": str(f),
                              "target": os.path.join(target(rules["target_dir"]), f.name)})

    fragments = projections.get("fragments")
    if fragments is not None:
        root = canonical_root / "fragments"
        for f in sorted(root.iterdir()) if root.is_dir() else ():
            if f.is_file():
                block(f, target(fragments["target_file"]), f.stem)

    mcp = projections.get("mcp")
    if mcp is not None:
        servers = _load_canonical_mcp_servers(canonical_root / "mcp", workspace, user_home)
        mode = mcp.get("mode", "json_merge")
        if servers and mode == "json_merge":
            items.append({"method": "config_merge", "config": {
                "target": target(mcp["target_file"]), "format": "json", "key": "mcpServers", "entries": servers}})
        elif servers and mode == "toml_mcp_merge":
            keep = ("command", "args", "env", "cwd")
            items.append({"method": "config_merge", "config": {
                "target": target(mcp["target_file"]), "format": "toml", "table": "mcp_servers",
                "entries": {n: {k: d[k] for k in keep if k in d} for n, d in servers.items()}}})

    bootstrap = dict(manifest.get("runtime_bootstrap") or {})
    trust = bootstrap.pop("ensure_workspace_trust", None)
    if trust and trust.get("enabled", False):
        items.append({"method": "config_merge", "config": {
            "target": target(trust["config_file"]), "format": "toml", "table": "projects", "on_exist": "keep",
            "entries": {p: {"trust_level": "trusted"} for p in
                        [str(expand_path(t, workspace, user_home)) for t in trust.get("trusted_paths", ["./"])] + list(extra_trust)}}})
    if bootstrap:
        residual.append("runtime_bootstrap: " + ",".join(sorted(bootstrap)))
    return items, residual


def _item_key(it: dict) -> tuple:
    if it["method"] == "config_merge":
        c = it["config"]
        return (it["method"], c["target"], c.get("key") or c["table"], "")
    return (it["method"], it["target"], it.get("block_id", ""))


def emit_dist_plan(prepared, canonical_root: Path, workspace: Path, user_home: Path, extra_trust=()) -> tuple[dict, list[str]]:
    items: list[dict] = []
    residual: list[str] = []
    seen = set()
    for h_id, manifest in prepared:
        got, left = plan_harness(manifest, canonical_root, workspace, user_home, extra_trust)
        residual += [f"{h_id}: {r}" for r in left]
        for it in got:
            key = _item_key(it)
            if key in seen:
                prior = next(x for x in items if _item_key(x) == key)
                if prior != it:
                    raise ValueError(f"harnesses disagree on {_item_key(it)[1]}")
                continue
            seen.add(key)
            items.append(it)
    return {"schema": DIST_PLAN_SCHEMA, "items": items}, residual


def main() -> int:
    parser = argparse.ArgumentParser(description="ExtScope Project Harness Dispatcher")
    parser.add_argument("--harness", required=True, help="target harness ids (comma separated, e.g. antigravity,codex)")
    parser.add_argument("--assets-root", "--canonical-root", dest="assets_root", required=True,
                        help="neutral asset dir with skills/ rules/ fragments/ mcp/ (consumer-owned; --canonical-root is a deprecated alias)")
    parser.add_argument("--manifests-dir", default="", help="harness manifests directory")
    parser.add_argument("--workspace", required=True, help="project dir; manifest paths `./x` resolve under it")
    parser.add_argument("--user-home", required=True, help="home dir; manifest paths `~/x` resolve under it")
    parser.add_argument("--trust-path", action="append", default=[],
                        help="absolute extra path to trust (codex); repeatable. Manifests only trust `./`")
    parser.add_argument("--verbose", action="store_true", help="print verbose debug output")
    parser.add_argument("--emit-dist-plan", action="store_true",
                        help="write nothing; print an extscope-dist.plan.v1 JSON to stdout for extscope-dist to apply")
    parser.add_argument("--allow-residual", action="store_true",
                        help="with --emit-dist-plan: exit 0 even if some projections cannot be expressed as a dist plan")
    args = parser.parse_args()

    canonical_root = Path(args.assets_root)
    extra_trust = list(args.trust_path)
    if any(not os.path.isabs(t) for t in extra_trust):
        log("--trust-path must be absolute", args.verbose)
        return 2
    workspace = Path(args.workspace)
    user_home = Path(args.user_home)

    manifests_dir = Path(args.manifests_dir) if args.manifests_dir else Path(__file__).resolve().parent.parent / "manifests"
    if not manifests_dir.is_dir():
        log("required manifests directory is missing", args.verbose)
        return 2

    target_harnesses = [h.strip().lower() for h in args.harness.split(",")]
    prepared = []
    try:
        if not target_harnesses or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", h) for h in target_harnesses):
            raise ValueError("invalid harness id")
        if len(set(target_harnesses)) != len(target_harnesses):
            raise ValueError("duplicate harness id")
        for h_id in target_harnesses:
            manifest_file = manifests_dir / f"harness-{h_id}.json"
            if not manifest_file.is_file():
                raise ValueError("required harness manifest is missing")
            with manifest_file.open(encoding="utf-8") as f:
                manifest = json.load(f)
            preflight_harness(manifest, canonical_root, workspace, user_home)
            prepared.append((h_id, manifest))
    except Exception:
        log("Harness preflight failed; no projections applied", args.verbose)
        return 2

    if args.emit_dist_plan:
        try:
            plan, residual = emit_dist_plan(prepared, canonical_root, workspace, user_home, extra_trust)
        except Exception as e:
            log(f"emit-dist-plan failed: {e}", args.verbose)
            return 2
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        for r in residual:
            log(f"not expressible as dist plan (adapter still owns it): {r}")
        return 0 if args.allow_residual or not residual else 3

    for h_id, manifest in prepared:
        try:
            apply_harness(h_id, manifest, canonical_root, workspace, user_home, args.verbose, extra_trust)

            failures = verify_harness(manifest, workspace, user_home, args.verbose)
            if failures:
                diagnostic = {
                    "harness": h_id,
                    "status": "fail_closed",
                    "failures": [asdict(f) for f in failures],
                }
                print(json.dumps(diagnostic, ensure_ascii=False), file=sys.stderr)
                return 2
        except Exception as e:
            log(f"applying harness [{h_id}] failed: {e}", args.verbose)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
