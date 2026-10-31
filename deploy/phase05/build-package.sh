#!/usr/bin/env bash
# Local only. Build inputs are explicit so secrets and development files stay out.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ "${1:-}" != --apply ]]; then
  printf 'DRY RUN: package built web, server source, locked wheels, units and static Agent for amd64/arm64\n'
  exit 0
fi
test -f "$repo/web/dist/index.html"
test -f "$repo/deploy/phase05/requirements-linux.txt"
version="$(git -C "$repo" rev-parse --short=12 HEAD)"
for arch in amd64 arm64; do
  binary="$repo/agent/bin/monitor-agent-linux-$arch"
  wheels="$repo/deploy/phase05/dist/wheelhouse-$arch"
  test -x "$binary"
  test -f "$wheels/pip-26.2.2-py3-none-any.whl"
  stage="$repo/deploy/phase05/dist/stage-$arch"
  rm -rf "$stage"
  install -d "$stage/mhm/agent" "$stage/mhm/units" "$stage/mhm/scripts" "$stage/mhm/web" "$stage/mhm/server"
  cp -R "$repo/server/app" "$repo/server/migrations" "$stage/mhm/server/"
  cp -R "$repo/web/dist" "$stage/mhm/web/"
  cp -R "$wheels" "$stage/mhm/wheelhouse"
  cp "$repo/alembic.ini" "$stage/mhm/"
  cp "$repo/.env.example" "$stage/mhm/server.env.example"
  cp "$repo/agent/config.example.json" "$stage/mhm/agent/config.example.json"
  cp "$repo/deploy/phase05/requirements-linux.txt" "$stage/mhm/"
  cp "$binary" "$stage/mhm/agent/monitor-agent"
  cp "$repo/deploy/phase05/multihost-center.service" "$stage/mhm/units/"
  cp "$repo/agent/packaging/systemd/monitor-agent.service" "$stage/mhm/units/multihost-agent.service"
  cp "$repo/deploy/phase05/install.sh" "$repo/deploy/phase05/preflight.sh" \
    "$repo/deploy/phase05/health.sh" "$repo/deploy/phase05/rollback.sh" \
    "$repo/deploy/phase05/uninstall.sh" "$repo/deploy/phase05/backup.py" "$stage/mhm/scripts/"
  printf '%s\n' "$version" > "$stage/mhm/VERSION"
  printf '%s\n' "$arch" > "$stage/mhm/ARCH"
  find "$stage/mhm" -name __pycache__ -type d -prune -exec rm -rf {} +
  archive="$repo/deploy/phase05/dist/mhm-phase05-$version-$arch.tar.gz"
  COPYFILE_DISABLE=1 tar -czf "$archive" -C "$stage" mhm
  python3 - "$archive" <<'PY'
import hashlib, pathlib, sys
p = pathlib.Path(sys.argv[1])
digest = hashlib.sha256(p.read_bytes()).hexdigest()
p.with_suffix(p.suffix + '.sha256').write_text(digest + '\n')
print(p.name, digest)
PY
done
