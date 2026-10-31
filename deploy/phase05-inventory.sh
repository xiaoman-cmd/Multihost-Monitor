#!/usr/bin/env bash
# Read-only inventory; never print configuration bodies or process arguments.
set -u

printf 'UTC_TIME '; date -u +%FT%TZ
printf 'ARCH '; uname -m
printf 'KERNEL '; uname -r
printf 'OS\n'; grep -E '^(NAME|VERSION_ID|PRETTY_NAME)=' /etc/os-release
printf 'MEMORY_KIB\n'; awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print}' /proc/meminfo
printf 'ROOT_DISK_BYTES\n'; df -B1 /
printf 'ROOT_INODES\n'; df -i /
printf 'TOP_PROCESS_RSS_KIB_CPU_PERCENT\n'; ps -eo pid,comm,rss,pcpu --sort=-rss | sed -n '1,31p'
printf 'LISTENING_PORTS\n'; sudo -n ss -lntup | awk 'NR == 1 {print; next} {print $1, $2, $5, $7}'
printf 'RUNNING_SERVICES\n'; systemctl list-units --type=service --state=running --no-pager --plain --no-legend | awk '{print $1, $3, $4}'
printf 'FAILED_SERVICES\n'; systemctl --failed --type=service --no-pager --plain --no-legend | awk '{print $1, $3, $4}'
printf 'SYSTEMD_VERSION\n'; systemctl --version | sed -n '1p'
printf 'DEPENDENCIES\n'
python3 --version 2>&1
python3 -c 'import sqlite3, ssl; print("SQLite", sqlite3.sqlite_version); print("Python OpenSSL", ssl.OPENSSL_VERSION)' 2>&1
openssl version 2>&1
printf 'HTTPS_OUTBOUND\n'; curl --silent --show-error --output /dev/null --write-out 'status=%{http_code} tls_verify=%{ssl_verify_result}\n' --connect-timeout 5 --max-time 15 https://example.com/ 2>&1
printf 'INSTALL_PATH_COLLISIONS\n'
for path in /opt/multihost-monitor /etc/multihost-monitor /var/lib/multihost-monitor /usr/local/lib/multihost-monitor; do
  if test -e "$path"; then printf 'exists %s\n' "$path"; else printf 'unused %s\n' "$path"; fi
done
if command -v caddy >/dev/null 2>&1; then
  printf 'CADDY_VERSION\n'; caddy version 2>&1
  printf 'CADDY_CONFIG_FILES\n'; sudo -n find /etc/caddy -maxdepth 3 -type f -printf '%p\n' 2>&1
  printf 'CADDYFILE_SHA256\n'; sudo -n sha256sum /etc/caddy/Caddyfile 2>&1
  printf 'CADDY_SITE_LABELS_ONLY\n'
  sudo -n awk '/^[A-Za-z0-9.:,*_-][A-Za-z0-9.:, _*-]*\{$/ {print}' /etc/caddy/Caddyfile 2>&1
fi
if command -v nginx >/dev/null 2>&1; then
  printf 'NGINX_VERSION\n'; nginx -v 2>&1
  printf 'NGINX_SITE_LAYOUT\n'; sudo -n find /etc/nginx/sites-enabled -maxdepth 1 -mindepth 1 -printf '%f -> %l\n' 2>&1
  printf 'NGINX_MAIN_CONFIG_SHA256\n'; sudo -n sha256sum /etc/nginx/nginx.conf 2>&1
fi
