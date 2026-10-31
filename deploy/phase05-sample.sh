#!/usr/bin/env bash
# Read-only 30-minute resource series; run over SSH with stdout saved locally.
set -euo pipefail

for ((sample = 0; sample <= 60; sample++)); do
  printf 'BEGIN %s %s\n' "$(date -u +%FT%TZ)" "$(date +%s)"
  awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print}' /proc/meminfo
  sed -n '1p' /proc/stat
  cat /proc/loadavg
  ps -eo pid,comm,rss,pcpu --sort=-rss | sed -n '1,21p'
  printf 'END\n'
  if ((sample < 60)); then sleep 30; fi
done
