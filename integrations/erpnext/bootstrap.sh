#!/usr/bin/env bash
set -euo pipefail
: "${ERPNEXT_DB_PASSWORD:?Set ERPNEXT_DB_PASSWORD in your shell. See README.md.}"
: "${ERPNEXT_ADMIN_PASSWORD:?Set ERPNEXT_ADMIN_PASSWORD in your shell. See README.md.}"
sandbox_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\n' 'Starting ERPNext. First site creation can take several minutes.'
docker compose -f "$sandbox_dir/compose.yaml" up -d
docker compose -f "$sandbox_dir/compose.yaml" run --rm -T bootstrap
printf 'Ready: http://localhost:%s/desk/selling/fitit-overview (user: Administrator)\n' "${ERPNEXT_HOST_PORT:-8080}"
