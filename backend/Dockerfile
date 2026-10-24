# Widget UI: Next.js static export (ADR 0015), served by the core at /fitit/ui/.
FROM node:22-slim AS ui
WORKDIR /ui
COPY --from=frontend package.json package-lock.json ./
RUN npm ci
COPY --from=frontend . .
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dev group included: `make check` runs pytest in this image.
RUN uv sync --frozen
COPY . .
COPY --from=ui /ui/out static/ui

EXPOSE 8000
CMD ["uv", "run", "--frozen", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
