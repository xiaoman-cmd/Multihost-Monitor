#!/usr/bin/env bash
# Remove only this project's unit and Agent executable; preserve data and secrets.
set -euo pipefail
role="${1:-}"
mode="${2:-}"
if [[ "$role" != center && "$role" != agent ]]; then
  printf 'Usage: %s center|agent [--apply]\n' "$0" >&2; exit 2
fi
unit="multihost-$role.service"
if [[ "$mode" != --apply ]]; then
  printf 'DRY RUN: disable and stop %s, remove its unit%s; preserve DB, config, backups and users\n' \
    "$unit" "$( [[ "$role" == agent ]] && printf ' and Agent executable' )"
  exit 0
fi
[[ "$EUID" -eq 0 ]] || { printf 'root required\n' >&2; exit 1; }
systemctl disable --now "$unit"
rm -f "/etc/systemd/system/$unit"
if [[ "$role" == agent ]]; then rm -f /usr/local/lib/multihost-monitor/monitor-agent; fi
systemctl daemon-reload
printf 'uninstalled %s unit; data, config, backups and users retained\n' "$role"
