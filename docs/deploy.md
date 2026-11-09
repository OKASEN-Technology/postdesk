# Deploying postdesk

postdesk is one small service and a Postgres database (a second service,
the slide renderer, arrives in M1). It needs one thing from wherever it
runs: **Instagram must be able to fetch your slide images from a public
URL.** Instagram has no upload; it downloads each image itself when you
publish. So either

- the app is reachable from the internet at `PUBLIC_BASE_URL`, and the
  images are served from `<PUBLIC_BASE_URL>/media/<id>`; or
- you give it an S3-compatible public bucket (`STORAGE_S3_*` in `.env`),
  and the images go there. The app itself can then sit behind NAT on a
  laptop.

`postdesk doctor` stores a tiny test image and fetches it back from the
outside, and tells you which of the two it used and whether it worked.

## A VPS with Docker Compose (the recommended way)

Any Linux box with Docker: a $5 VPS is plenty.

```bash
git clone https://github.com/OKASEN-Technology/postdesk
cd postdesk
cp .env.example .env
cp examples/postdesk.toml postdesk.toml
# edit both, then:
docker compose up -d
```

The `app` container runs `postdesk db upgrade` on every start and then
`postdesk serve`: the API, the web UI, and the publisher loop (every 15
minutes within `[schedule].publisher_hours`). Data lives in two named
volumes, `db` and `data`; back those up.

Put it behind HTTPS. The simplest is Caddy on the same box:

```
ig.example.com {
    reverse_proxy localhost:8080
}
```

and `PUBLIC_BASE_URL=https://ig.example.com` in `.env`. The session cookie
is marked secure when the app sees HTTPS (directly, or via an
`X-Forwarded-Proto: https` header, which Caddy and nginx both send).

Updating:

```bash
git pull
docker compose up -d --build
```

## A laptop that stays on

Works for the scheduler, but Instagram cannot reach a laptop behind NAT.
Use a bucket for the images:

```
STORAGE_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com   # or s3.amazonaws.com, or MinIO
STORAGE_S3_BUCKET=postdesk-slides
STORAGE_S3_ACCESS_KEY_ID=...
STORAGE_S3_SECRET_ACCESS_KEY=...
STORAGE_S3_PUBLIC_BASE_URL=https://pub-xxxx.r2.dev    # the bucket's public address
```

The bucket must be **publicly readable** (R2: enable the public bucket URL;
S3: a bucket policy allowing `s3:GetObject`). All five variables set means
images go to the bucket; any one unset means local disk behind
`PUBLIC_BASE_URL`. `postdesk doctor` says which.

## Hosted platforms

All of these run the same Docker image (`docker/Dockerfile.app`). The
database is the platform's managed Postgres, or any Postgres you have; set
`DATABASE_URL`. Set `POSTDESK_DATA_DIR` to a persistent disk, or use a
bucket (recommended on platforms without disks).

**Fly.io.** `fly launch` from the checkout detects the Dockerfile; pick
`docker/Dockerfile.app`. Attach a Postgres (`fly postgres create`, `fly
postgres attach`), add a volume mounted at `/data` or use a bucket, set the
secrets with `fly secrets set`. The app's URL is your `PUBLIC_BASE_URL`.

**Railway.** New project from the GitHub repo, set the Dockerfile path to
`docker/Dockerfile.app`, add the Postgres plugin (it sets `DATABASE_URL`;
make sure it is the `postgresql+psycopg://` form, or set it yourself), add
the variables from `.env.example` in the Variables tab.

**Render.** A Web Service from the repo with the Dockerfile path above and
a managed Postgres. If you would rather not run the loop inside the web
service, set the service's start command to `postdesk serve
--no-scheduler` and add a Cron Job on the same image with the command
`postdesk cron` every 15 minutes. One lesson from running it there: the
cron command must be a single command, not `sh -c "a; b"`, which is why
`postdesk cron` does both steps itself.

`postdesk.toml` on a platform without a mounted file: set `POSTDESK_CONFIG`
to wherever you can put the file, or bake it into your own image
(`FROM ghcr.io/...`, `COPY postdesk.toml /app/`).

## A system cron instead of the loop

`postdesk serve --no-scheduler` runs only the API and web UI; then run

```
*/15 * * * * cd /srv/postdesk && docker compose exec -T app postdesk cron
```

`cron` publishes what is due (only inside `publisher_hours`; `--force`
ignores them) and collects insights once a day. It exits non-zero when a
publish failed, so your cron's mail shows it.

## Without Docker

```bash
pip install "postdesk[s3]"        # or without the extra for local storage
cp .env.example .env; postdesk init
postdesk db upgrade
postdesk serve
```

The web UI needs to be built once from a checkout (`cd web && npm ci && npm
run build`) before `serve` can show it; the PyPI wheel will carry it
pre-built from the first release on.
