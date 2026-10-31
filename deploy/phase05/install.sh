#!/usr/bin/env bash
# Stage a verified release. It does not migrate data, switch current, or start units.
set -euo pipefail

role= archive= expected= root=/ apply=0
while (($#)); do
  case "$1" in
    --role) role="${2:?}"; shift 2 ;;
    --archive) archive="${2:?}"; shift 2 ;;
    --sha256) expected="${2:?}"; shift 2 ;;
    --root) root="${2:?}"; shift 2 ;;
    --apply) apply=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$role" == center || "$role" == agent ]] || { printf 'use --role center|agent\n' >&2; exit 2; }
[[ -f "$archive" && "$expected" =~ ^[0-9a-f]{64}$ && "$root" == /* ]] || {
  printf 'use --archive FILE --sha256 HEX [--root DIR] [--apply]\n' >&2; exit 2;
}
if (( ! apply )); then
  printf 'DRY RUN: verify %s, stage %s under %s, install only its unit; no migration, switch or start\n' "$archive" "$role" "$root"
  exit 0
fi
if [[ "$root" == / && "$EUID" -ne 0 ]]; then printf 'root required\n' >&2; exit 1; fi

actual="$(python3 - "$archive" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for block in iter(lambda: f.read(1024 * 1024), b''):
        h.update(block)
print(h.hexdigest())
PY
)"
[[ "$actual" == "$expected" ]] || { printf 'archive SHA-256 mismatch\n' >&2; exit 1; }
python3 - "$archive" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    for member in archive:
        parts = pathlib.PurePosixPath(member.name).parts
        if not parts or parts[0] != 'mhm' or '..' in parts or not (member.isfile() or member.isdir()):
            raise SystemExit('unsafe archive member')
PY
version="$(tar -xOzf "$archive" mhm/VERSION)"
package_arch="$(tar -xOzf "$archive" mhm/ARCH)"
[[ "$version" =~ ^[0-9a-f]{7,40}$ && ( "$package_arch" == amd64 || "$package_arch" == arm64 ) ]] || {
  printf 'invalid release metadata\n' >&2; exit 1;
}
if [[ "$root" == / ]]; then
  case "$(uname -m):$package_arch" in x86_64:amd64|aarch64:arm64) ;; *) printf 'architecture mismatch\n' >&2; exit 1 ;; esac
fi
release="${root%/}/opt/multihost-monitor/releases/$version-$package_arch"
if [[ -e "$release" ]]; then
  [[ -f "$release/ARCHIVE_SHA256" && "$(cat "$release/ARCHIVE_SHA256")" == "$expected" ]] || {
    printf 'different release already staged\n' >&2; exit 1;
  }
else
  install -d -m 0755 "$root/opt/multihost-monitor/releases" "$release"
  tar -xzf "$archive" -C "$release" --strip-components=1
  printf '%s\n' "$expected" > "$release/ARCHIVE_SHA256"
fi

if [[ "$role" == center ]]; then
  if [[ "$root" == / ]]; then
    getent passwd multihost-center >/dev/null || useradd --system --no-create-home --home-dir /var/lib/multihost-monitor --shell /usr/sbin/nologin multihost-center
    install -d -o multihost-center -g multihost-center -m 0700 /var/lib/multihost-monitor /var/lib/multihost-monitor/secrets
    install -d -o root -g root -m 0700 /var/backups/multihost-monitor /etc/multihost-monitor
    python3 -m venv --without-pip "$release/.venv"
    PYTHONPATH="$release/wheelhouse/pip-26.2.2-py3-none-any.whl" \
      "$release/.venv/bin/python" -m pip install --no-index --find-links "$release/wheelhouse" \
      --require-hashes -r "$release/requirements-linux.txt"
  fi
  install -d -m 0755 "$root/etc/systemd/system"
  install -m 0644 "$release/units/multihost-center.service" "$root/etc/systemd/system/multihost-center.service"
else
  if [[ "$root" == / ]]; then
    getent passwd multihost-agent >/dev/null || useradd --system --no-create-home --home-dir /var/lib/multihost-agent --shell /usr/sbin/nologin multihost-agent
    install -d -o root -g multihost-agent -m 0710 /etc/multihost-monitor
  fi
  install -d -m 0755 "$root/usr/local/lib/multihost-monitor" "$root/etc/systemd/system"
  install -m 0755 "$release/agent/monitor-agent" "$root/usr/local/lib/multihost-monitor/monitor-agent"
  install -m 0644 "$release/units/multihost-agent.service" "$root/etc/systemd/system/multihost-agent.service"
fi
printf 'staged %s release %s; activation requires separate approval\n' "$role" "$release"
