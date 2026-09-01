#!/bin/sh
# Production Fly deploy entry point. Do not add caller-controlled build flags:
# the app-scoped token cannot create or operate Fly remote-builder apps.
set -eu

if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
    echo "deploy_fly_ci: refused outside GitHub Actions" >&2
    exit 64
fi

if [ "$#" -ne 0 ]; then
    echo "deploy_fly_ci: arguments are forbidden" >&2
    exit 64
fi

if [ -z "${FLY_API_TOKEN:-}" ]; then
    echo "deploy_fly_ci: FLY_API_TOKEN is required" >&2
    exit 64
fi

if ! docker info >/dev/null 2>&1; then
    echo "deploy_fly_ci: GitHub runner Docker daemon is unavailable" >&2
    exit 69
fi

exec flyctl deploy --local-only --ha=false
