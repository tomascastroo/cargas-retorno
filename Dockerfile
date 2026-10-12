# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
# Opcional: CA adicional para redes con proxy de inspección TLS (no queda en la imagen final).
#   docker build --secret id=extra_ca,src=/ruta/ca.crt .
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN pip install --no-cache-dir uv==0.8.17
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim AS runtime
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home cargas
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY data ./data
COPY src ./src
USER cargas
EXPOSE 8000
CMD ["uvicorn", "cargas.main:app", "--host", "0.0.0.0", "--port", "8000"]
