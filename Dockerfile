FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config/intake.example.yaml ./config/intake.example.yaml
COPY config/active-context.example.yaml ./config/active-context.example.yaml
COPY fixtures ./fixtures
COPY migrations ./migrations

RUN pip install --no-cache-dir uv && uv pip install --system .

ENTRYPOINT ["intake"]
