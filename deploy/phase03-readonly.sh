#!/usr/bin/env bash
# Send this script over SSH stdin. No install, restart, network, DNS or file mutations.
set -u
printf 'UTC_TIME\n'; date -u +%FT%TZ
printf 'ARCHITECTURE\n'; uname -m
printf 'KERNEL\n'; uname -r
printf 'OS_RELEASE\n'; cat /etc/os-release
printf 'SYSTEMD\n'; systemctl --version | head -1
printf 'MEMORY_BYTES\n'; free -b
printf 'ROOT_DISK_BYTES\n'; df -B1 /
printf 'ROOT_INODES\n'; df -i /
printf 'RUNNING_SERVICES\n'; systemctl list-units --type=service --state=running --no-pager --plain
printf 'LISTENING_PORTS\n'; sudo -n ss -lntup
printf 'INSTALL_PERMISSIONS\n'; id; sudo -n -l 2>/dev/null | tail -4
printf 'WIREGUARD_INTERFACE_NAMES\n'; if command -v wg >/dev/null; then sudo -n wg show interfaces; else printf 'wg tool not installed\n'; fi
printf 'PYTHON\n'; python3 --version
printf 'TEST_DIRECTORY_COLLISIONS\n'; for task_path in /opt/multihost-monitor /etc/multihost-monitor /var/lib/multihost-monitor; do if test -e "$task_path"; then printf 'exists: %s\n' "$task_path"; else printf 'unused: %s\n' "$task_path"; fi; done
