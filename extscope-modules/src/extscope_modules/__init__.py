"""Optional modules for extscope-dist. Imports nothing from extscope or extscope_dist:
dist loads this package by name (config: modules.enable) and talks to it through the
duck-typed protocol documented in extscope_dist/modreg.py."""
from .chmod import Chmod
from .chown import Chown
from .config_merge import ConfigMerge
from .custom_hook import CustomHook
from .env_inject import EnvInject
from .pkg_import import PkgImport
from .key_inject import KeyInject

__version__ = "0.1.0a6"
MODULES = {"chmod": Chmod(), "chown": Chown(), "key_inject": KeyInject(),
           "env_inject": EnvInject(), "config_merge": ConfigMerge(), "custom_hook": CustomHook(),
           "pkg_import": PkgImport()}
