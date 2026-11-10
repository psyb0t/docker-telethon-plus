# Python style

- Target Python 3.14 only (`requires-python >=3.14,<3.15` in `pyproject.toml`). Start modules with `from __future__ import annotations`, as every module in `app/` except the empty `__init__.py` does.
- Line length is 100 for black, isort (profile `black`) and flake8 (`.flake8`, which ignores E203 and W503). Run `make format` before `make lint`.
- `make lint` runs flake8 over `app`, `login.py` and `tests`, and mypy over `app` and `login.py` with `warn_unused_ignores`. Both must pass with no `|| true`; CI runs the same target.
- Type every function. Prefer `Optional`, `Dict` and `List` from `typing` to match the existing code.
- Name magic numbers and strings as module constants with a comment on where the value comes from (see the limits at the top of `app/tools.py` and `app/config.py`).
- Comments explain why. Keep the existing rationale comments; they record incidents and Telegram quirks.
- Log with the module logger (`log = logging.getLogger(__name__)`), never `print`, except in the interactive login and the startup `config error:` exit in `app/__main__.py`.
