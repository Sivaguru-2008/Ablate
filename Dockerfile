# Ablate release gate as a Cloud Run Job (see deploy/cloud_run_job.sh).
# Runs `python -m ablate.cloud_job` once and uploads the report to Cloud Storage.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir "uv>=0.5"
WORKDIR /app

# Dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock README.md THIRD_PARTY.md ./
RUN uv sync --frozen --no-dev --extra cloud --no-install-project

COPY ablate ./ablate
COPY configs ./configs
COPY scenarios ./scenarios
RUN uv sync --frozen --no-dev --extra cloud

# Non-root at runtime.
RUN useradd --create-home ablate && chown -R ablate /app
USER ablate

ENTRYPOINT ["/app/.venv/bin/python", "-m", "ablate.cloud_job"]
