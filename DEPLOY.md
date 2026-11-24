# Putting RONB Clips on a server

RONB Clips runs as three containers on one Linux server: the app, PostgreSQL, and Caddy (which handles HTTPS).

## What you need

- A Linux server with Docker. Clipping is CPU work: plan on **8+ CPU cores, 16 GB RAM and 200+ GB of disk**.
  Transcription runs at roughly 10x real time per video on 4 cores.
- A domain name pointing at the server (an `A` record).
- An AI key (see "Choose your AI" in the README). Optional: a Google "Web application" OAuth client.

## Steps

1. Copy this folder to the server (or `git clone` it there).
2. `cp .env.example .env` and fill it in: the AI lines, `RONBCLIPS_DOMAIN`, `POSTGRES_PASSWORD`, and
   `COMPOSE_PROFILES=own-https` if the server is RONB Clips's alone. Put key files, if you use files, in `./secrets/`.
3. `docker compose up -d --build`
   The first start downloads the speech model (about 480 MB) and creates the database tables.
4. Open `https://your-domain`. RONB Clips opens directly into its local-owner dashboard.
5. Open **Settings, AI** and add your AI key there (or put it in `.env`).

## Connecting YouTube, Instagram and TikTok

Connect posting accounts in **Settings, Connected apps**: press Connect, sign in on the
platform's own page, and come back. For that, the server needs one app registered with each platform. Set it up
right there: press **Set up** on a platform, follow the steps shown, and paste the app's id and secret (or put
them in `.env`, see `.env.example`). The redirect address to register is shown in the panel, with a Copy button.

- **YouTube** (Google Cloud): an OAuth client of type Web application, with YouTube Data API v3 turned on. Until
  Google verifies the app, people see a warning screen and uploads stay private.
- **Instagram** (Meta for Developers): a Business app with Instagram (API setup with Instagram login). Works with
  Creator and Business accounts. Until Meta reviews the app, only accounts added as testers can connect. Instagram
  fetches each clip from a one-hour signed link on your site, so the site needs a public https address.
- **TikTok** (TikTok for Developers): an app with Login Kit and the Content Posting API (Direct Post), scopes
  `user.info.basic` and `video.publish`. Until TikTok audits the app, clips are posted as private (RONB Clips does
  this by itself and says so on the account).

Instagram and TikTok only accept https redirect addresses.

## Google setup (YouTube)

In Google Cloud, create an OAuth client of type **Web application** with these redirect URIs:

- `https://your-domain/oauth/youtube/callback`

Enable **YouTube Data API v3** in the same project. Posting publicly to YouTube needs Google's API audit;
until then uploads from the API stay private.

## Good to know

- **Data**: everything lives in two Docker volumes, `pgdata` (AI usage) and `data` (videos, clips). Back both up.
  With PostgreSQL set in `DATABASE_URL`, `python scripts/backup.py /path/to/backup` copies the data volume and dumps
  the database to `backup/database.sql`. The dump uses plain SQL with `--clean --if-exists`, no owners and no
  privileges, so it can be put back into a recreated database. To restore, stop RONB Clips, **drop and recreate the
  target database (or make sure it is empty)**, then run
  `python scripts/restore.py /path/to/backup /path/to/data`. Do not restore into a database that already has RONB
  Clips tables. The restore runs psql with `ON_ERROR_STOP=1` and one transaction, so a bad dump fails loudly and
  changes nothing. The scripts read `.env` just like the app does. They use `docker compose exec` by default; set
  `RONBCLIPS_PG_DUMP_BIN` and `RONBCLIPS_PG_RESTORE_BIN` to a local PostgreSQL `bin` folder, or use the older
  `RONBCLIPS_PG_DUMP` and `RONBCLIPS_PG_RESTORE` command overrides. Passwords are passed to the child process through
  `PGPASSWORD`, never on the command line.
  The SQLite equivalent is `python scripts/backup.py` and `python scripts/restore.py <backup.db> <data/ronbclips.db>`.
- **Capacity**: `RONBCLIPS_JOB_WORKERS` is how many videos are processed at once; `RONBCLIPS_MAX_LIVE` is how many live
  streams are recorded at once. Raise them only as far as the CPU allows. ffmpeg's encoder and filter threads are
  shared out from the CPU count automatically; set `RONBCLIPS_FFMPEG_THREADS` to a fixed number if you need to. The
  web request thread pool also follows the CPU count (16–64 threads). `RONBCLIPS_DRAIN_SECONDS` (30 by default) is how
  long shutdown waits for work already queued or in progress; only what misses that deadline is left for recovery.
  `RONBCLIPS_LOG_LEVEL` (`info` by default) sets how much structured JSON is written; each line carries a request id
  that is also returned in the `X-Request-Id` response header. `/health/detail` probes the database on every call and
  reports `shutting_down` while a drain is in progress.
- **One server only**: jobs and live recordings run inside the app process. Do not start a second copy of the app
  against the same data.
- **YouTube downloads from data centres** are sometimes refused by YouTube ("confirm you're not a bot"). If links
  fail on the server but work at home, the server's address is the cause. RONB Clips then pauses YouTube links for a
  few minutes and says so; uploads and Twitch or Kick links keep working.
- **Updates**: `git pull && docker compose up -d --build`. A restart pauses videos in progress (they pick up
  where they stopped) and live recordings (clips already found are finished from the saved stream).
- **Public access**: a server deployment exposes the local owner's app to whoever can reach it. Keep limits in
  `.env` sensible and put the site behind your own access control. See "Threat assumptions" below.
- **Pinned images and packages**: the Docker and Compose images are pinned to digests, every Python dependency is
  pinned in `requirements.txt`/`requirements-dev.txt`, and the packages pip pulls in for them are locked in
  `constraints.txt`. Base-image system packages and the CI builder are pinned too. Update them deliberately; a
  Dependabot-style bot or a calendar reminder is a good way to pick up security fixes without silently changing what
  runs. Regenerate the Python lock with `python scripts/generate_constraints.py` after changing a requirement.

## Rotating secrets

Rotate a secret straight away if it may have leaked, and as a routine every few months. After each change, restart
the app (`docker compose up -d --build`) and check the affected feature once.

| Secret | Where to change it | What else to do |
|---|---|---|
| AI key | Settings → AI in the dashboard, or `RONBCLIPS_AI_KEY` in `.env` | Revoke the old key with the provider first. AI usage records stay. |
| Google / Instagram / TikTok client secret | Settings → Connected apps → Set up, or the matching `*_SECRET` setting in `.env` (or `*_SECRET_FILE`, including `RONBCLIPS_IG_APP_SECRET_FILE` and `RONBCLIPS_TIKTOK_CLIENT_SECRET_FILE`) | Update the secret in the platform's developer console. Existing connected accounts keep their refresh tokens; remove and reconnect an account if you also want to revoke those. |
| Discord webhook or Telegram bot token | Settings → each creator's alerts | Regenerate the webhook URL with Discord, or the token with BotFather, then save the new value. |
| `RONBCLIPS_ASR_TOKEN` | `.env` on both the main server and the speech machine | Change both together and restart both containers. |
| Signed clip links (`link.key` in the data folder) | Delete the file while the app is stopped | A new 32-byte key is made on the next start. **Every `/pub/` link already given to Instagram or TikTok stops working**, so do this only when no clip is being fetched. |
| PostgreSQL password | `POSTGRES_PASSWORD` in `.env` and `DATABASE_URL` on the app | Also change it inside PostgreSQL if you manage the database yourself, then restart both containers. |

Secrets live in the data folder (`ai.json`, `apps.json`, `accounts/*.json`, `link.key`). They are written with
restrictive permissions on Linux and macOS; Windows relies on the folder being private to your account. Never commit
`.env`, `secrets/`, `data/` or any `*.key` file.

## Threat assumptions for a public deployment

Trusted:

- The server itself, its operating system, the Docker daemon and anyone with shell access. They can read every
  secret and every video; keep that access to the owner.
- The reverse proxy **only if** you set `RONBCLIPS_TRUST_PROXY=1` and configure it to overwrite the address header
  on every incoming request. Also set `RONBCLIPS_PROXY_IPS` to the proxy's address. Without this, the app uses the
  socket address and ignores client-supplied forwarding headers.
- The local owner's browser, which is the only thing allowed to change settings.

Not trusted:

- Anyone who can reach the site. With accounts off, they all share the one local owner identity, its AI key and its
  connected posting accounts. Put real access control (a VPN, SSO at the proxy, or an allow-list) in front of a public
  deployment — this is required, not optional.
- Text and titles from downloaded videos, and anything a connected platform returns. They go into prompts as data
  and are escaped before being shown.
- Uploaded files: the kind, size and destination are checked, but they are still fed to ffmpeg. Run the container
  with the memory/CPU limits shown in `docker-compose.yml`.
- Forwarded headers, `Origin`/`Host` values, and `CF-Connecting-IP` unless a trusted proxy overwrites them.

Operator checklist before exposing the site:

1. Access control at the proxy or network edge, plus HTTPS.
2. Sensible `RONBCLIPS_MAX_UPLOAD_MB`, `RONBCLIPS_MAX_LIVE`, `RONBCLIPS_JOB_WORKERS` and per-user limits in `.env`.
3. Correct `RONBCLIPS_PUBLIC_URL`; `RONBCLIPS_TRUST_PROXY` and `RONBCLIPS_PROXY_IPS` only when the proxy is set up
   to overwrite headers.
4. Backups running (data folder and database), with a tested restore.
5. A plan for rotating the secrets in the table above.
6. `python scripts/security_audit.py --strict` passing in CI, and dependency images updated on a schedule.

Rate limits are in memory, per address and per route, and reset on restart. Uploads, provider calls and API actions
have their own limits; signed one-clip links allow 300 views per 10 minutes per address. A single process is assumed;
running two copies of the app against the same data would split those limits and is not supported.

## A server that already runs other sites

Leave `COMPOSE_PROFILES=own-https` out of `.env`. RONB Clips then listens only on `127.0.0.1:8765` and the web
server that is already there forwards the domain to it: see `deploy/nginx-ronbclips.conf`, or
`deploy/docker-compose.traefik.yml` for Traefik.

## Cloudflare CDN in front

With the record proxied (orange cloud):

- Set `RONBCLIPS_TRUST_PROXY=1`, `RONBCLIPS_CLIENT_IP_HEADER=CF-Connecting-IP` and `RONBCLIPS_PROXY_IPS` to
  Cloudflare's address ranges in `.env`, so rate limits see each visitor. The header is only believed for connections
  from those ranges; never trust it when visitors can reach the app directly.
- Images, videos and scripts under `/brand/` and `/assets/` are cached by Cloudflare automatically. Signed-in
  pages, clips and the API send `private` or `no-store` and are never cached by Cloudflare.
- Use SSL/TLS mode Full (strict) with a Cloudflare origin certificate in `secrets/` and
  `RONBCLIPS_CADDYFILE=Caddyfile.origin`.
- Cloudflare's standard plans refuse requests over 100 MB. RONB Clips sends big uploads in pieces
  (`RONBCLIPS_UPLOAD_PART_MB`, 40 by default), so large files still work.

## Speech to text on a graphics card

Optional. A second machine with an NVIDIA card can do most of the transcription much faster:
`deploy/gpu/docker-compose.yml` runs it, and `RONBCLIPS_ASR_URL` plus `RONBCLIPS_ASR_TOKEN` on the main server point
at it. That compose file sets `RONBCLIPS_ASR_HOST=0.0.0.0` so the speech machine answers the app on another host;
the worker listens only on localhost when run without it. Keep port 9000 on a private network, never on the internet.
If it is unreachable, the main server carries on with its own processor.
