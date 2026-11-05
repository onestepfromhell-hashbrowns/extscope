"""Read-only host skill projections; other asset renderers remain explicit work."""
from collections.abc import Mapping
from pathlib import PurePosixPath
import os
import re
import stat
from extscope.digest import hash_tree_fd
from extscope.plugin_api import (TargetOperation, SourceRequirement, PluginPlan,
                                validate_path)

def plan_skills(config, context) -> tuple[TargetOperation, ...]:
    req = {'canonical_generation', 'canonical_sha256', 'harness', 'target_home', 'asset_kinds'}
    if not isinstance(config, Mapping) or set(config.keys()) != req:
        raise ValueError("Invalid config mapping or keys")
    if config['harness'] not in {'codex-host', 'claude-host', 'antigravity-host'}:
        raise ValueError("Unsupported harness")
    ak = config['asset_kinds']
    if not isinstance(ak, (tuple, list)) or list(ak) != ['skills']:
        raise ValueError("asset_kinds must be ['skills']")
    sha = config['canonical_sha256']
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise ValueError("canonical_sha256 must be 64 lowercase hex characters")
    gen_p = PurePosixPath(validate_path(config['canonical_generation']))
    home_p = PurePosixPath(validate_path(config['target_home']))
    if '.agents' in home_p.parts:
        raise ValueError(".agents forbidden in target_home")
    gen_fd = context.sources.open_source(str(gen_p))
    try:
        if hash_tree_fd(gen_fd) != sha:
            raise ValueError("canonical_sha256 mismatch")
    finally:
        os.close(gen_fd)
    skills_p = gen_p / 'skills'
    skills_fd = context.sources.open_source(str(skills_p))
    try:
        names = sorted(os.listdir(skills_fd))
        if not names:
            raise ValueError("Empty skills directory")
        ops = []
        name_re = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
        for name in names:
            if not name_re.fullmatch(name):
                raise ValueError(f"Invalid skill name: {name}")
            asset_fd = context.sources.open_source(str(skills_p / name))
            try:
                st = os.fstat(asset_fd)
                if not stat.S_ISDIR(st.st_mode):
                    raise ValueError(f"Skill {name} is not a directory")
                mst = os.stat('SKILL.md', dir_fd=asset_fd, follow_symlinks=False)
                if not (stat.S_ISREG(mst.st_mode) and mst.st_size > 0):
                    raise ValueError(f"{name}/SKILL.md must be a non-empty regular file")
                ops.append(TargetOperation(
                    operation='symlink',
                    source=str(skills_p / name),
                    target=str(home_p / 'skills' / name),
                    source_sha256=hash_tree_fd(asset_fd),
                    source_kind='directory',
                    source_mode=stat.S_IMODE(st.st_mode),
                    algorithm='extscope.tree.v1'
                ))
            finally:
                os.close(asset_fd)
        return tuple(ops)
    finally:
        os.close(skills_fd)


class Plugin:
    api_version = 1
    schema_version = 1

    def plan(self, config, context):
        operations = plan_skills(config, context)
        dependency = SourceRequirement(str(validate_path(config['canonical_generation'])),
                                       'directory', 'extscope.tree.v1', config['canonical_sha256'])
        return PluginPlan(operations, (dependency,))
