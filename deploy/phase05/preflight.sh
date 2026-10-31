#!/usr/bin/env bash
# Read-only checks. --check is explicit so a bare invocation is a dry run.
set -euo pipefail

role="${1:-}"
mode="${2:-}"
root="${3:-/}"
if [[ "$role" != center && "$role" != agent ]]; then
  printf 'Usage: %s center|agent [--check [root]]\n' "$0" >&2; exit 2
fi
if [[ "$mode" != --check ]]; then
  printf 'DRY RUN: would check %s files, permissions, architecture and dependencies under %s\n' "$role" "$root"
  exit 0
fi
[[ -d "$root" ]] || { printf 'missing root\n' >&2; exit 1; }
if [[ "$role" == center ]]; then
  test -f "$root/etc/systemd/system/multihost-center.service"
  test -L "$root/opt/multihost-monitor/current"
  if [[ "$root" == / ]]; then
    getent passwd multihost-center >/dev/null
    test -f /etc/multihost-monitor/server.env
    test "$(stat -c '%a %u' /etc/multihost-monitor/server.env)" = '600 0'
    test -x /opt/multihost-monitor/current/.venv/bin/uvicorn
    test -d /var/lib/multihost-monitor
    test "$(stat -c '%a %u' /var/lib/multihost-monitor)" = "700 $(id -u multihost-center)"
    python3 -c 'import sqlite3,sys; assert sys.version_info >= (3,12) and sqlite3.sqlite_version_info >= (3,35)'
    grep -qx 'MHM_DATABASE_URL=sqlite:////var/lib/multihost-monitor/monitor.db' /etc/multihost-monitor/server.env
    grep -qx 'MHM_COOKIE_SECURE=true' /etc/multihost-monitor/server.env
    grep -q '^MHM_ORIGIN=https://' /etc/multihost-monitor/server.env
  fi
else
  test -f "$root/etc/systemd/system/multihost-agent.service"
  test -x "$root/usr/local/lib/multihost-monitor/monitor-agent"
  if [[ "$root" == / ]]; then
    getent passwd multihost-agent >/dev/null
    test -f /etc/multihost-monitor/config.json
    test -f /etc/multihost-monitor/agent.token
    test "$(stat -c '%a %u' /etc/multihost-monitor/agent.token)" = "600 $(id -u multihost-agent)"
    test "$(stat -c '%a %u %g' /etc/multihost-monitor/config.json)" = "640 0 $(id -g multihost-agent)"
    test "$(stat -c '%a %u %g' /etc/multihost-monitor)" = "710 0 $(id -g multihost-agent)"
    python3 -c 'import json; c=json.load(open("/etc/multihost-monitor/config.json")); assert c["server_url"].startswith("https://") and c["token_file"] == "/etc/multihost-monitor/agent.token"'
  fi
fi
printf '%s preflight: OK (read-only)\n' "$role"
