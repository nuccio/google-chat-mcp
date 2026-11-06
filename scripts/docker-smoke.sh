#!/usr/bin/env bash
# Smoke test of the remote image (issue #95): the image starts with both
# commands, as non-root, and holds no credential files.
#
# A command may stop on missing configuration or "not implemented yet" (any
# exit code), or keep running until the timeout (it started). What fails the
# test: a broken installation (ImportError, ModuleNotFoundError of a
# dependency or of the package), a missing `python -m` entry point once the
# service exists, or a run as root.
#
# Usage: scripts/docker-smoke.sh [IMAGE]   (default: builds gcm-remote:smoke)
set -u

image="${1:-}"
if [ -z "$image" ]; then
  image=gcm-remote:smoke
  docker build -t "$image" . || exit 1
fi

fail=0

uid=$(docker run --rm --entrypoint id "$image" -u)
if [ "$uid" = 0 ]; then
  echo "FAIL: the image runs as root"; fail=1
fi

if ! docker run --rm --entrypoint python "$image" -c \
  "import google_chat_mcp, nicegui, google.cloud.firestore, fastmcp"; then
  echo "FAIL: the installed dependencies cannot be imported"; fail=1
fi

if docker run --rm --entrypoint find "$image" /app /home -name 'token.json' \
  -o -name 'client_secret*.json' -o -name '.env' | grep -q .; then
  echo "FAIL: credential files in the image"; fail=1
fi

for module in google_chat_mcp.settings google_chat_mcp.remote; do
  echo "== python -m $module"
  out=$(timeout 30 docker run --rm "$image" "$module" 2>&1)
  code=$?
  printf '%s\n' "$out" | tail -n 15
  echo "-- exit code $code (124 = still running at the timeout, i.e. started)"
  if printf '%s' "$out" | grep -Eq 'ImportError|ModuleNotFoundError|No module named'; then
    # Until the service lands (#94, #80) the package has no __main__.py: that
    # is the only missing module tolerated, and only for that reason.
    if printf '%s' "$out" | grep -q "'$module' is a package and cannot be directly executed"; then
      echo "NOTE: $module has no entry point yet (stub), tolerated"
    else
      echo "FAIL: $module does not start"; fail=1
    fi
  fi
done

exit $fail
