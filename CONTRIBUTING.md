# Contributing

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff check . && pytest
```

Rules: Python 3.9+, standard library only at runtime, OS-specific code only in `src/tokens_doctor/platform.py`
(and the notifier backends in `notify.py`), no network access, no telemetry. New logic comes with a test.
