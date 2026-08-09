#!/usr/bin/env bash
set -euo pipefail

container="${MEMORYV4_CONTAINER:-memory-v4-memory-v4-1}"
data_volume="${MEMORYV4_DATA_VOLUME:-memory-v4-data-v1}"
backup_volume="${MEMORYV4_BACKUP_VOLUME:-memory-v4-backups-v1}"
retention="${MEMORYV4_BACKUP_RETENTION:-14}"

if ! [[ "$retention" =~ ^[1-9][0-9]*$ ]]; then
  echo "MEMORYV4_BACKUP_RETENTION must be a positive integer" >&2
  exit 2
fi

image_id="$(docker inspect "$container" --format '{{.Image}}')"
if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "running MemoryV4 image is not digest pinned" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/backups/container4-${stamp}.sqlite3"

run=(
  docker run --rm
  --user 65532:65532
  --read-only
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --network none
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=0700,uid=65532,gid=65532
  --volume "${data_volume}:/data"
  --volume "${backup_volume}:/backups"
  --entrypoint python
  "$image_id"
)

"${run[@]}" -m app.recovery backup \
  --database /data/memoryv4.sqlite3 \
  --output "$backup" >/dev/null
"${run[@]}" -m app.recovery verify --backup "$backup" >/dev/null

docker run --rm \
  --user 65532:65532 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --network none \
  --env "RETENTION=$retention" \
  --volume "${backup_volume}:/backups" \
  --entrypoint python \
  "$image_id" -c '
from pathlib import Path
import os
retention = int(os.environ["RETENTION"])
backups = sorted(Path("/backups").glob("container4-*.sqlite3"), key=lambda p: p.stat().st_mtime)
for backup in backups[:-retention]:
    backup.unlink()
    backup.with_name(backup.name + ".manifest.json").unlink(missing_ok=True)
' >/dev/null

printf 'memoryv4_backup=PASS artifact=%s retention=%s image=%s\n' "$backup" "$retention" "$image_id"
