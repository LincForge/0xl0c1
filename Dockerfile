FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 LOCI_HOST=0.0.0.0 LOCI_PORT=8130
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY server.py db.py schema.sql schema.postgres.sql viewer.html ./
EXPOSE 8130
CMD ["uv", "run", "--no-dev", "python", "server.py"]
