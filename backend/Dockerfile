FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_CACHE_DIR=/opt/app/backend/.runtime/uv
WORKDIR /opt/app
RUN pip install --no-cache-dir uv==0.13.8
COPY pyproject.toml uv.lock README.md ./
COPY backend/src backend/src
RUN uv sync --locked
COPY backend /opt/app/backend
RUN useradd --uid 10001 --create-home accordtrace && chown -R accordtrace:accordtrace /opt/app
USER accordtrace
ENV PATH="/opt/app/.venv/bin:$PATH" ACCORDTRACE_BACKEND_ROOT=/opt/app/backend
CMD ["uvicorn", "accordtrace_backend.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
