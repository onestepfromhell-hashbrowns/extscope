# Contributing

Thanks for your interest. `extscope` is a standalone, dependency-free suite of
Python packages for declarative extension and toolchain delivery; most design
documents are written in Traditional Chinese, but issues and pull requests in
English are welcome.

## Workflow

1. Open an issue first for anything larger than a small fix, so the scope can be
   agreed before you write code.
2. Fork, create a branch, and keep each pull request focused on one change.
3. Run the tests before you push (see below). New behavior needs a test.
4. Do not commit secrets, tokens, real hostnames, personal e-mail addresses or
   absolute home paths. Use clearly fake placeholders in fixtures and examples.
5. By contributing you agree that your contribution is licensed under the MIT
   License in `LICENSE`.

## Tests

```
PYTHONPATH=src python3 -m pytest -q
```

Each sub-package (`extscope-dist`, `extscope-modules`, `extscope-container`, `extscope-tools`, `extscope-harness`) also has its own `tests/` directory. Only the Python standard library and `pytest` are needed.

Security problems should be reported privately; see `SECURITY.md`.
