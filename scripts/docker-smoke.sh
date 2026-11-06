#!/usr/bin/env bash
# Smoke test of the remote image (issue #95): the image starts with both
# commands, as non-root, and holds no credential files.
#
# gcm-settings may stop on missing configuration or keep running. gcm-mcp must
# stop with a configuration error when unconfigured, and start and answer 401 on
# `/mcp` without a token when configured. What fails the test: a broken
# installation (ImportError, ModuleNotFoundError), a missing entry point, or a
# run as root.
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

# gcm-settings: stops on missing configuration (any exit code) or keeps running.
echo "== python -m google_chat_mcp.settings"
out=$(timeout 30 docker run --rm "$image" google_chat_mcp.settings 2>&1)
code=$?
printf '%s\n' "$out" | tail -n 15
echo "-- exit code $code (124 = still running at the timeout, i.e. started)"
if printf '%s' "$out" | grep -Eq 'ImportError|ModuleNotFoundError|No module named'; then
  echo "FAIL: google_chat_mcp.settings does not start"; fail=1
fi

# gcm-mcp: without configuration it must stop with a configuration error.
echo "== python -m google_chat_mcp.remote (no configuration)"
out=$(timeout 30 docker run --rm "$image" google_chat_mcp.remote 2>&1)
code=$?
printf '%s\n' "$out" | tail -n 5
if [ "$code" = 0 ] || [ "$code" = 124 ] || ! printf '%s' "$out" | grep -q 'Configuration error'; then
  echo "FAIL: google_chat_mcp.remote did not stop with a configuration error"; fail=1
fi

# gcm-mcp with fake configuration: it starts (no Firestore is contacted) and
# `/mcp` without a token answers 401.
echo "== python -m google_chat_mcp.remote (fake configuration)"
key=$(docker run --rm --entrypoint python "$image" -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
name=gcm-smoke-$$
docker run -d --rm --name "$name" -p 127.0.0.1:18080:8080 \
  -e BASE_URL=https://gcm-mcp.example.com -e GOOGLE_CLIENT_ID=smoke.apps.googleusercontent.com \
  -e GOOGLE_CLIENT_SECRET=smoke-secret -e OAUTH_JWT_SIGNING_KEY=0123456789abcdef0123456789abcdef \
  -e OAUTH_FERNET_KEYS="$key" -e SETTINGS_BASE_URL=https://settings.example.com \
  -e ALLOWED_DOMAINS=example.com -e GOOGLE_CLOUD_PROJECT=smoke \
  -e FIRESTORE_EMULATOR_HOST=127.0.0.1:1 "$image" google_chat_mcp.remote >/dev/null
status=000
for _ in $(seq 1 30); do
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Host: gcm-mcp.example.com' \
    -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:18080/mcp)
  [ "$status" != 000 ] && break
  sleep 1
done
docker logs "$name" 2>&1 | tail -n 10
docker stop "$name" >/dev/null 2>&1
echo "-- /mcp without a token: HTTP $status"
if [ "$status" != 401 ]; then
  echo "FAIL: /mcp without a token did not answer 401"; fail=1
fi

exit $fail
