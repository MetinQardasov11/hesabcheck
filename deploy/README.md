# HesabCheck production

- Host: `178.105.97.126`
- URL: `https://history.testgrelo.online/api/docs/`
- Root: `/root/hesabcheck`
- Source: `/root/hesabcheck/current` → `releases/<commit SHA>`
- Runtime configuration: `/root/hesabcheck/.env` (root-only, never committed)
- Docker Compose project: `hesabcheck`; services: `web`, `db`
- Database: dedicated PostgreSQL 17 volume; not published to a host port
- Uploads: `/root/hesabcheck/media`; only authenticated Django download endpoints

## Deployment path

`master` push → PostgreSQL tests, schema/migration/production checks → Docker image build on GitHub runner → SHA-labelled image and source archive over SSH → pre-deploy DB backup → migrations → health-checked replacement → HTTPS check of deployed commit.

Pull requests run tests only. The `production` GitHub environment links to the API. Actions are pinned to commits; deployments are serial. Secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` are encrypted repository Actions secrets. The SSH key has `restrict,command="/root/hesabcheck/bin/receive"` in root's authorized_keys. It cannot open a normal SSH shell, forward ports or run arbitrary SSH commands. **It can deploy repository code as a privileged operation**: protect repository write access accordingly.

The root password, Gemini key, database password and Django secret are not stored in Actions. Known-host checking uses the host key recorded on the initial authenticated server connection. Container application code runs as UID 10001 with a read-only root filesystem and limited CPU/RAM.

## Initial provisioning

Create `/root/hesabcheck/{bin,releases,backups,media}`, make media owned by 10001:10001, store production values using `production.env.example` as the template, and install `receive.sh` at `bin/receive` (mode 700). Keep `.env` mode 600. Add the **public** deploy key to `authorized_keys` with the forced command above and `restrict`.

This server already has a shared Docker nginx and a valid automatically renewed Let's Encrypt certificate. `configure_proxy.py` changes only the HTTPS `history.testgrelo.online` virtual host in `/root/hospital-appointment-demo/nginx.conf`, preserving the file inode, making a backup, running nginx validation and reloading. The existing HTTP → HTTPS redirect and ACME challenge location remain. The old domain backend is no longer reachable through this hostname; its containers/data remain untouched. nginx resolves `hesabcheck-web` through Docker DNS after container replacement.

This proxy configuration is specific to this server. If the shared nginx is replaced or its Docker network is renamed, update `compose.yaml` and `configure_proxy.py` accordingly.

## Operations

Connect with your normal server admin SSH credential, then:

```bash
cd /root/hesabcheck
export RELEASE_SHA=$(cat current-sha)
docker compose --env-file .env -f current/deploy/compose.yaml ps
docker compose --env-file .env -f current/deploy/compose.yaml logs --tail=100 web
docker compose --env-file .env -f current/deploy/compose.yaml exec web python manage.py createsuperuser
```

`/health/` checks the database connection and returns the deployed commit SHA. `/` opens Swagger; `/api/cases/` requires authentication. `/api/auth/token/` accepts an existing user's username/password. No public signup is enabled.

The current design processes AI synchronously (up to 660-second proxy/worker timeout) with one worker/four threads and bounded memory on a resource-constrained shared server. It is not a high-throughput architecture. Heavy concurrent extraction should move to a task queue and a larger worker host. No real Gemini requests run in CI.

## Backups and recovery

`/etc/cron.d/hesabcheck-backup` runs `current/deploy/backup.sh` at 03:17 server time daily. Each backup includes a PostgreSQL custom-format dump, media archive, root-only environment file and release SHA. Pre-deploy dumps are also created. Daily and pre-deploy backups are kept for 14 days on **the same server**; they do not protect against complete server/disk loss. Add off-server storage separately.

Verify/run a backup:

```bash
bash /root/hesabcheck/current/deploy/backup.sh
ls -lt /root/hesabcheck/backups
```

Application health failure automatically attempts to restore the previous image. Database migrations are **not** automatically rolled back. Deploy only backward-compatible migrations; data-destructive migrations require an explicit maintenance/restore procedure.

Manual application rollback (database must be compatible):

```bash
cd /root/hesabcheck
export RELEASE_SHA=$(cat previous-sha)
docker compose --env-file .env -f "releases/$RELEASE_SHA/deploy/compose.yaml" up -d --no-deps --wait web
ln -sfn "releases/$RELEASE_SHA" current
printf '%s\n' "$RELEASE_SHA" > current-sha
```

Database restore requires stopping writes, selecting the correct backup, and running `pg_restore` into the intended HesabCheck database. Do not restore another application's database. No destructive restore is automated.

Only the latest five HesabCheck application releases/images are retained. Unrelated Docker containers, images and volumes are never pruned by the deploy script. Container logs rotate at 10 MB × 3 files.
