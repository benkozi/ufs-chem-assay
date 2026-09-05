# CI toolchain image: Python + uv + the frozen dependency set + pre-baked
# pre-commit hook environments. Source is NOT baked — CI and local runs
# bind-mount the checkout at /repo and run against the /opt/venv here:
#
#   docker buildx build --load -t ufs-chem-assay:dev .
#   docker run --rm -v "$PWD":/repo -w /repo ufs-chem-assay:dev \
#     sh -c 'git config --global --add safe.directory /repo \
#            && uv sync --frozen && uv run pre-commit run --all-files'
#
# The build context is allowlisted to three files (see .dockerignore), so
# .env and other per-machine state can never enter the image.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# git: required by pre-commit and semantic-release at run time.
# g++: cartopy ships no CPython 3.14 wheels yet and builds its extension
# from source during `uv sync`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git g++ \
    && rm -rf /var/lib/apt/lists/*

# The venv lives outside any bind mount; the run-time `uv sync --frozen`
# from /repo targets it via UV_PROJECT_ENVIRONMENT (always install, in case
# the checkout's lock drifted from the cached image layers).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PRE_COMMIT_HOME=/opt/pre-commit \
    UV_LINK_MODE=copy

WORKDIR /app

# --no-install-project: the project is a package (console script) but the
# context carries no source; CI's in-repo `uv sync --frozen` installs it.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project

# Bake the isolated hook environments (pre-commit-hooks,
# conventional-pre-commit) so CI jobs need no network for pre-commit; the
# throwaway `git init` only satisfies pre-commit's in-a-repo requirement.
COPY .pre-commit-config.yaml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    git init -q . \
    && uv run --no-sync pre-commit install-hooks \
    && rm -rf .git
