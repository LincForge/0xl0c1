set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List the available build and validation commands.
default:
    @just --list

# Verify the committed dependency lock.
lock:
    uv lock --check

# Install the locked development environment.
sync:
    uv sync --frozen

# Check source formatting without modifying files.
format:
    uv run --frozen ruff format --check server.py db.py tests

# Run the configured static lint rules.
lint:
    uv run --frozen ruff check server.py db.py tests

# Type-check the application boundary in strict mode.
typecheck:
    uv run --frozen mypy server.py db.py

# Run tests with a validated, bounded xdist worker limit. Extra pytest arguments are forwarded.
test *args:
    @workers="${PYTEST_XDIST_AUTO_NUM_WORKERS-6}"; \
    if [[ ! "$workers" =~ ^[1-9][0-9]*$ ]]; then \
        echo "PYTEST_XDIST_AUTO_NUM_WORKERS must be a positive integer (got '${workers}')" >&2; \
        exit 2; \
    fi; \
    if (( workers > 6 )); then workers=6; fi; \
    PYTEST_XDIST_AUTO_NUM_WORKERS="$workers" uv run --frozen pytest -q -n auto {{args}}

# Run the same test selection serially. Extra pytest arguments are forwarded.
test-serial *args:
    uv run --frozen pytest -q -n 0 {{args}}

# Run lock, lint, format, type, and bounded parallel test gates in order.
check:
    just lock
    just lint
    just format
    just typecheck
    just test

# Container build and smoke checks are added by the container-validation bead.
