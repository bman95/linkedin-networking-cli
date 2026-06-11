FROM python:3.13-slim

# Install uv (Astral)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy the project into the image
COPY . .

# Install dependencies (no dev extras) and the Chromium browser with system deps
RUN uv sync \
    && uv run python -m playwright install --with-deps chromium

# LINKEDIN_EMAIL and LINKEDIN_PASSWORD are read from the environment at runtime, e.g.:
#   docker run --rm -it -e LINKEDIN_EMAIL=... -e LINKEDIN_PASSWORD=... linkedin-networking-cli
CMD ["uv", "run", "linkedin_cli.py"]
