#!/usr/bin/env bash
# Explicit rollback of this project's own unit and data; never touch other services.
set -euo pipefail
role= previous= backup= env_backup= unit_backup= config_backup= apply=0
while (($#)); do
  case "$1" in
    --role) role="${2:?}"; shift 2 ;;
    --previous) previous="${2:?}"; shift 2 ;;
    --db-backup) backup="${2:?}"; shift 2 ;;
    --env-backup) env_backup="${2:?}"; shift 2 ;;
    --unit-backup) unit_backup="${2:?}"; shift 2 ;;
    --config-backup) config_backup="${2:?}"; shift 2 ;;
    --apply) apply=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$role" == center || "$role" == agent ]] || { printf 'use --role center|agent\n' >&2; exit 2; }
[[ -e "$previous" ]] || { printf 'use --previous EXISTING_PATH\n' >&2; exit 2; }
[[ -f "$unit_backup" ]] || { printf 'use --unit-backup EXISTING_FILE\n' >&2; exit 2; }
if [[ "$role" == center ]]; then
  [[ -f "$backup" && -f "$env_backup" ]] || { printf 'use --db-backup and --env-backup EXISTING_FILES\n' >&2; exit 2; }
else
  [[ -f "$config_backup" ]] || { printf 'use --config-backup EXISTING_FILE\n' >&2; exit 2; }
fi
if (( ! apply )); then
  printf 'DRY RUN: stop only multihost-%s, restore %s%s, restart only that unit\n' \
    "$role" "$previous" "$( [[ "$role" == center ]] && printf ' and database backup' )"
  exit 0
fi
[[ "$EUID" -eq 0 ]] || { printf 'root required\n' >&2; exit 1; }
resolved="$(realpath -e "$previous")"
unit_backup="$(realpath -e "$unit_backup")"
[[ "$unit_backup" == /var/backups/multihost-monitor/* ]] || { printf 'unit backup path invalid\n' >&2; exit 1; }
if [[ "$role" == center ]]; then
  [[ "$resolved" == /opt/multihost-monitor/releases/* && -x "$resolved/.venv/bin/uvicorn" ]] || {
    printf 'previous center release invalid\n' >&2; exit 1;
  }
  backup="$(realpath -e "$backup")"
  env_backup="$(realpath -e "$env_backup")"
  [[ "$backup" == /var/backups/multihost-monitor/* && "$env_backup" == /var/backups/multihost-monitor/* ]] || {
    printf 'backup path invalid\n' >&2; exit 1;
  }
  python3 - "$backup" <<'PY'
import sqlite3, sys
db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
db.close()
PY
  systemctl stop multihost-center.service
  failed="/var/backups/multihost-monitor/failed-$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -o root -g root -m 0700 "$failed"
  if [[ -f /etc/multihost-monitor/server.env ]]; then cp -p /etc/multihost-monitor/server.env "$failed/"; fi
  if [[ -f /etc/systemd/system/multihost-center.service ]]; then cp -p /etc/systemd/system/multihost-center.service "$failed/"; fi
  for name in monitor.db monitor.db-wal monitor.db-shm; do
    if [[ -e "/var/lib/multihost-monitor/$name" ]]; then mv "/var/lib/multihost-monitor/$name" "$failed/"; fi
  done
  install -o multihost-center -g multihost-center -m 0600 "$backup" /var/lib/multihost-monitor/monitor.db
  install -o root -g root -m 0600 "$env_backup" /etc/multihost-monitor/server.env
  install -o root -g root -m 0644 "$unit_backup" /etc/systemd/system/multihost-center.service
  ln -s "$resolved" /opt/multihost-monitor/current.next
  mv -Tf /opt/multihost-monitor/current.next /opt/multihost-monitor/current
  systemctl daemon-reload
  systemctl start multihost-center.service
else
  [[ "$resolved" == /var/backups/multihost-monitor/* && -f "$resolved" ]] || {
    printf 'previous Agent binary path invalid\n' >&2; exit 1;
  }
  config_backup="$(realpath -e "$config_backup")"
  [[ "$config_backup" == /var/backups/multihost-monitor/* ]] || { printf 'config backup path invalid\n' >&2; exit 1; }
  systemctl stop multihost-agent.service
  failed="/var/backups/multihost-monitor/failed-agent-$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -o root -g root -m 0700 "$failed"
  if [[ -f /usr/local/lib/multihost-monitor/monitor-agent ]]; then cp -p /usr/local/lib/multihost-monitor/monitor-agent "$failed/"; fi
  if [[ -f /etc/multihost-monitor/config.json ]]; then cp -p /etc/multihost-monitor/config.json "$failed/"; fi
  if [[ -f /etc/systemd/system/multihost-agent.service ]]; then cp -p /etc/systemd/system/multihost-agent.service "$failed/"; fi
  install -m 0755 "$resolved" /usr/local/lib/multihost-monitor/monitor-agent
  install -o root -g multihost-agent -m 0640 "$config_backup" /etc/multihost-monitor/config.json
  install -o root -g root -m 0644 "$unit_backup" /etc/systemd/system/multihost-agent.service
  systemctl daemon-reload
  systemctl start multihost-agent.service
fi
printf 'rolled back %s; run health checks before GO\n' "$role"
