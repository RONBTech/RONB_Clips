#!/usr/bin/env bash
set -euo pipefail

client="$(basename "$0")"
case "$client" in
  pg_dump|psql) ;;
  *) echo "Call this script as pg_dump or psql." >&2; exit 2 ;;
esac

# Keep the client tools on the same pinned PostgreSQL image as the service.
image="postgres:16-alpine@sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d"
args=(--rm --interactive --network host --entrypoint "$client")
if [[ -n "${PGPASSWORD:-}" ]]; then
  args+=(--env PGPASSWORD)
fi
exec docker run "${args[@]}" "$image" "$@"
