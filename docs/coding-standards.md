# Coding Standards

## Language and style

- Python 3.12 only.
- Use type hints for all public functions.
- Keep modules single-purpose and small.

## Quality gates

- Ruff lint must pass.
- Mypy strict mode must pass.
- Tests must pass before merge.

## Architecture guardrail

- Add subsystem capabilities via specialist modules.
- Avoid embedding domain logic directly into orchestrator flow control.
