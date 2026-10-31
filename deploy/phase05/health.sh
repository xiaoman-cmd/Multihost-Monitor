#!/usr/bin/env bash
# Read-only health check; no credentials or journal contents are printed.
set -euo pipefail
role="${1:-}"
mode="${2:-}"
if [[ "$role" != center && "$role" != agent ]]; then
  printf 'Usage: %s center|agent [--check]\n' "$0" >&2; exit 2
fi
if [[ "$mode" != --check ]]; then
  printf 'DRY RUN: would check %s systemd state%s\n' "$role" "$( [[ "$role" == center ]] && printf ' and loopback readiness' )"
  exit 0
fi
unit="multihost-$role.service"
systemctl is-active --quiet "$unit"
systemctl show "$unit" --property=ActiveState,SubState,NRestarts,MemoryCurrent,CPUUsageNSec --no-pager
if [[ "$role" == center ]]; then
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18080/api/health/ready >/dev/null
fi
printf '%s health: OK\n' "$role"
