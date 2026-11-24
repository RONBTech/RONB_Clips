#!/usr/bin/env bash
set -euo pipefail

if [[ "${CI:-}" != "true" ]]; then
  echo "This cleanup test runs only in CI." >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "CI must create a disposable .env before running the Compose smoke test." >&2
  exit 2
fi

project="ronbclips-smoke-${GITHUB_RUN_ID:-local}"
export COMPOSE_PROJECT_NAME="$project"
cleanup() {
  docker compose down --volumes --remove-orphans || true
}
trap cleanup EXIT

docker compose up --build --detach --wait --wait-timeout 240
services="$(docker compose ps --services --status running)"
grep -Fxq app <<<"$services"
grep -Fxq db <<<"$services"
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
docker volume inspect "${project}_data" >/dev/null
docker volume inspect "${project}_pgdata" >/dev/null
echo "PASS Compose services, health endpoint, and persistent volumes"
