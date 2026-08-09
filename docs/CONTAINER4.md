# Container 4 production deployment

MemoryV4 is Alica-v1 **Container 4**: a single private Tier-3 service reached only by the governed UNIFY Gateway. It is not a public API and it has no host port.

## Required topology and controls

`compose.production.yaml` is the canonical deployment definition. It requires:

- an immutable `sha256:` image reference;
- UID/GID `65532:65532`;
- a read-only root filesystem and bounded `/tmp` tmpfs;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- one persistent application mount, `memory-v4-data-v1:/data`;
- one read-only Compose credential secret under `/run/secrets`;
- the internal `unify_memory-private` network shared only with `unify-core`;
- no `ports` declaration or host/public listener;
- health, restart, CPU, memory, PID, and log-rotation controls.

The backup volume is deliberately **not mounted in the long-running application container**. A hardened, networkless one-shot container performs online SQLite backup and verification against the data volume.
The image declares no Docker `VOLUME`; persistence is explicit in the production
orchestrator so upgrades cannot silently retain or create an undeclared backup mount.

## Build and immutable release

```bash
commit=$(git rev-parse --short=8 HEAD)
docker build --pull --build-arg "VCS_REF=$(git rev-parse HEAD)" \
  -t "memoryv4-core:container4-$commit" .
docker image inspect "memoryv4-core:container4-$commit" --format '{{.Id}}'
```

Use the resulting `sha256:<64 hex>` image ID as `MEMORYV4_IMAGE`. Do not deploy a mutable tag.

Prerequisites:

```bash
docker volume create memory-v4-data-v1
docker volume create memory-v4-backups-v1
# Created by the UNIFY release; it must report Internal=true.
docker network inspect unify_memory-private
```

Deployment environment contains paths and identifiers only, never the token value:

```bash
MEMORYV4_IMAGE=sha256:<64-hex-image-id>
MEMORYV4_API_SCOPE=org:aquiero
MEMORYV4_API_KEY_SECRET_FILE=/opt/unify/secrets/memory-v4-token
MEMORYV4_DATA_VOLUME=memory-v4-data-v1
MEMORYV4_PRIVATE_NETWORK=unify_memory-private
```

Deploy from an immutable release directory and retain explicit `current` and `previous` symlinks. Run `docker compose up -d --wait`, then execute the invariant monitor and production acceptance through UNIFY before moving `current`.

## Scheduled backup and monitoring

Install scripts in `/opt/memory-v4/ops/container4` and units in `/etc/systemd/system`, then enable both timers:

```bash
systemctl daemon-reload
systemctl enable --now memoryv4-backup.timer memoryv4-monitor.timer
systemctl start memoryv4-backup.service memoryv4-monitor.service
systemctl --no-pager status memoryv4-backup.service memoryv4-monitor.service
```

`memoryv4-backup.timer` runs daily with randomized delay. Each backup:

1. resolves the exact running image ID;
2. starts a non-root, read-only, capability-free, networkless one-shot container;
3. performs SQLite online backup under a shared database lease;
4. validates integrity, migrations, FTS consistency, checksum, and manifest;
5. retains the newest 14 generated backup pairs by default.

`memoryv4-monitor.timer` runs every five minutes and fails if health, immutable image pinning, user, root filesystem, capabilities, security options, restart policy, mounts, host-port isolation, private-network membership, or the in-container health endpoint drift from the production contract.

Systemd failures are visible through `systemctl --failed` and the journal. Infrastructure alerting must watch these unit failures; the scripts never report success on partial checks.

## Production acceptance

All checks are mandatory:

```bash
python3 /opt/memory-v4/ops/container4/healthcheck.py
systemctl start memoryv4-backup.service
systemctl is-active memoryv4-backup.timer memoryv4-monitor.timer
docker inspect memory-v4-memory-v4-1
docker network inspect unify_memory-private
```

Also verify through the public UNIFY origin:

- anonymous Memory access is `401`;
- authenticated `/api/v1/memory/status` is ready;
- governed reads succeed;
- oversized Memory input is `422 invalid_request`;
- Gateway envelope overflow is `413 PAYLOAD_TOO_LARGE`;
- both Hermes identities expose the bounded Memory tool schema.

Direct host access to port `8000` must fail because Container 4 publishes no port. MemoryV3 adoption and cutover remain separate, approval-gated work.
