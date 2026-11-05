from pathlib import Path
Path('/tmp/extscope-trap-fixture-imported').write_text('unexpected plugin import')
class Plugin:
    api_version=1
    schema_version=1
