FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

EXPOSE 8000
CMD ["uv", "run", "--frozen", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
