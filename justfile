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

# Build the reproducible local application image.
build:
    docker build --tag loci:local --file Dockerfile .

# Start the local image, verify its health endpoint, and always remove it.
smoke: build
    @container="loci-smoke-$$"; \
    cleanup() { docker rm --force "$container" >/dev/null 2>&1 || true; }; \
    trap cleanup EXIT INT TERM; \
    docker run --detach --name "$container" \
        --env LOCI_PATH_TOKEN=smoke-test-token \
        --env LOCI_DB=/tmp/loci-smoke.db \
        --publish 127.0.0.1::8130 \
        loci:local >/dev/null; \
    port="$(docker port "$container" 8130/tcp | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p')"; \
    test -n "$port"; \
    for _ in $(seq 1 40); do \
        response="$(curl --fail --silent --show-error "http://127.0.0.1:$port/health" 2>/dev/null || true)"; \
        if grep --quiet '"ok"[[:space:]]*:[[:space:]]*true' <<<"$response"; then exit 0; fi; \
        sleep 0.25; \
    done; \
    docker logs "$container" >&2; \
    echo "container health check did not return ok: true" >&2; \
    exit 1
