#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

image_name=${1:?usage: docker_runtime_smoke.sh IMAGE}
container_name="aeso-mcp-runtime-smoke-${RANDOM}-$$"
port="${MCP_SMOKE_PORT:-18080}"
bearer_token="container-smoke-bearer-token"

cleanup() {
  docker rm --force "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach \
  --name "$container_name" \
  --env AESO_API_KEY=container-smoke-key \
  --env AESO_MCP_HTTP_BEARER_TOKEN="$bearer_token" \
  --publish "127.0.0.1:${port}:8000" \
  "$image_name" >/dev/null

for _ in $(seq 1 30); do
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${port}/readyz" || true)
  if [[ "$status" == "200" ]]; then
    break
  fi
  sleep 1
done

if [[ "$status" != "200" ]]; then
  echo "Docker runtime readiness probe failed (HTTP ${status})." >&2
  exit 1
fi

unauthenticated_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST "http://127.0.0.1:${port}/mcp" \
  --header 'content-type: application/json' \
  --header 'accept: application/json, text/event-stream' \
  --data '{}')
if [[ "$unauthenticated_status" != "401" ]]; then
  echo "Expected unauthenticated MCP request to return 401; got ${unauthenticated_status}." >&2
  exit 1
fi

docker exec --interactive "$container_name" python - <<'PY'
import asyncio
import os

from fastmcp import Client


async def smoke() -> None:
    token = os.environ["AESO_MCP_HTTP_BEARER_TOKEN"]
    for mode, protocol in (("auto", "2026-07-28"), ("legacy", "2025-11-25")):
        async with Client("http://127.0.0.1:8000/mcp", auth=token, mode=mode) as client:
            tools = await client.list_tools()
            assert client.protocol_version == protocol
            assert any(tool.name == "get_market_snapshot" for tool in tools)


asyncio.run(smoke())
print("Docker runtime authenticated MCP modern/legacy negotiation smoke passed")
PY
