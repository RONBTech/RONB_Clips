# Troubleshooting

Start with the dashboard's message and the server log. RONB Clips writes structured logs to the console; each request has an id that can be followed through background work.

## The server will not start

- Confirm Python 3.11+ is active and install dependencies with `python -m pip install -r requirements.txt`.
- Confirm `ffmpeg` and `ffprobe` are installed and available on `PATH`. If they are elsewhere, set `RONBCLIPS_FFMPEG_DIR` to their folder.
- Check that the configured port is free (`RONBCLIPS_PORT`, default `8765`).
- If storage was moved, reconnect that drive or edit/remove `storage.json` only after confirming where your existing data lives. Do not delete the data folder to fix a startup error.

## Speech model errors on Windows

Keep the checkout path short, such as `C:\ronbclips`. Windows and some native speech libraries cannot load files through very long paths. Install the model ahead of time with `python -m studio.fetch_model`; first startup normally downloads it automatically. A CUDA ASR setup must use a model and runtime compatible with its installed NVIDIA drivers.

## A video does not download or process

- Confirm the link is from an allowed site and that the source video is available to your machine.
- Check the server log for the request id shown in the dashboard. A private, removed or region-blocked source may not be downloadable.
- Check free disk space. The source, temporary render and finished clips need room in the configured data folder.
- If transcription is slow, reduce concurrent work or configure a separate ASR worker; see `deploy/gpu/` and `.env.example`.

## The dashboard cannot reach the server

Open the address printed at startup. `RONBCLIPS_HOST` defaults to `127.0.0.1`; use a reverse proxy for remote access and set `RONBCLIPS_PUBLIC_URL` to the public HTTPS address. When proxying, trust only the known proxy addresses and headers (`RONBCLIPS_TRUST_PROXY`, `RONBCLIPS_PROXY_IPS`, and `RONBCLIPS_CLIENT_IP_HEADER`).

## OAuth connect returns an error

Check the provider's client id and secret, enabled APIs and exact callback URL in **Settings, Connected apps**. The redirect URL must use the same public HTTPS host that opens RONB Clips. Provider restrictions and review modes can prevent test accounts from posting. See [DEPLOY.md](../DEPLOY.md#connecting-youtube-instagram-and-tiktok).

## Docker or PostgreSQL problems

- Run `docker compose ps` and `docker compose logs app db caddy` to see service health and startup errors.
- Verify `POSTGRES_PASSWORD`, `RONBCLIPS_DOMAIN`, and the selected Caddy profile in `.env`.
- A database connection error may mean PostgreSQL is still starting; check the `db` health status before restarting the app.
- Check Docker disk and memory limits. The speech model and video processing need both storage and working memory.

## Where to ask for help

Search existing issues, then open a bug report with the operating system, install method, Python version, steps to reproduce, and the relevant request id or redacted log lines. Never attach keys, OAuth secrets, personal data or private video files. For a suspected security issue, follow [SECURITY.md](../SECURITY.md).
