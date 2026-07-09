FROM python:3.13-slim

# Install uv (Astral)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Keep Playwright browsers in a fixed, world-readable location so they are
# usable by the non-root runtime user.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependency layer: only invalidated when project metadata or the lockfile
# change, so `COPY . .` below doesn't bust the dependency cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && uv run --no-sync python -m playwright install --with-deps chromium

# Copy the source and install the project itself
COPY . .
RUN uv sync --frozen --no-dev

# Run as a non-root user
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app /ms-playwright
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

# LINKEDIN_EMAIL and LINKEDIN_PASSWORD are read from the environment at runtime, e.g.:
#   docker run --rm -it -e LINKEDIN_EMAIL=... -e LINKEDIN_PASSWORD=... linkedin-networking-cli
CMD ["linkedin-cli"]
