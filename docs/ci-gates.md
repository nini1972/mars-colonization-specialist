# CI Gates

Phase 0 requires all gates below to pass:

1. Lint gate: `python -m ruff check .`
2. Type gate: `python -m mypy src tests`
3. Test gate: `python -m pytest -q`
4. Reproducibility gate: deterministic seed test must pass

No phase transition is allowed while any gate is failing.
