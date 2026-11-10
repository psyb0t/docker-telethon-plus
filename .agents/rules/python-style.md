# Python style

- Target Python 3.14 only (`requires-python >=3.14,<3.15` in `pyproject.toml`). Start modules with `from __future__ import annotations`, as every module in `app/` except the empty `__init__.py` does.
- Line length is 100 for black, isort (profile `black`) and flake8 (`.flake8`, which ignores E203 and W503). Run `make format` before `make lint`.
- `make lint` runs flake8 over `app`, `login.py` and `tests`, and mypy over `app` and `login.py` with `warn_unused_ignores`. CI runs the same target.
- Use `Optional`, `Dict` and `List` from `typing` to match the existing code.
- The module logger is named `log`. `print` appears only in the interactive login and the startup `config error:` exit in `app/__main__.py`.
