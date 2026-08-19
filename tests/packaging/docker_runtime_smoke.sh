#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

image_name=${1:?usage: docker_runtime_smoke.sh IMAGE}
container_name="aeso-mcp-runtime-smoke-${RANDOM}-$$"
port="${MCP_SMOKE_PORT:-18080}"
response_file=$(mktemp)

cleanup() {
  docker rm --force "$container_name" >/dev/null 2>&1 || true
  rm -f "$response_file"
}
trap cleanup EXIT

docker run --detach \
  --name "$container_name" \
  --env AESO_API_KEY=container-smoke-key \
  --publish "127.0.0.1:${port}:8000" \
  "$image_name" >/dev/null

for _ in $(seq 1 30); do
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${port}/readyz" || true)
  if [[ "$status" == "200" ]]; then
    break
  fi
  sleep 1
done

curl --fail-with-body --silent --show-error \
  --output "$response_file" \
  --request POST "http://127.0.0.1:${port}/mcp" \
  --header 'content-type: application/json' \
  --header 'accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"docker-smoke","version":"0"}}}'

grep --quiet '"serverInfo"' "$response_file"
echo "Docker runtime MCP initialize smoke passed"
