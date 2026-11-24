# RONB Clips

Turn long videos and live streams into captioned vertical clips, ready to post. RONB Clips is free and open source: run it on
your own computer or server, with your own AI key.

Paste a YouTube, Twitch or Kick link (or upload a file) and RONB Clips finds the moments worth clipping, cuts them to
9:16, burns word-by-word captions into every video frame, and can post them to your connected accounts. It can also record a
creator's live stream and clip the highlights while the stream is still running.

## What it does

- **Clips from any video.** Transcribes the audio, asks an AI which moments would hold a stranger's attention, and
  renders each one with captions in one of three styles.
- **Hindi, Urdu and Nepali captions.** These languages stay in the words that were spoken and are romanized for
  readable captions in the bundled video fonts; other languages can be translated to English.
- **Live clipping.** Records a stream into a rolling buffer and clips as it happens, using loudness, chat activity
  and the transcript.
- **Workspaces per creator.** Channels, clip settings, posting rules and an activity log for each creator you clip.
- **Posting.** Connect YouTube, Instagram and TikTok in one click, then queue or auto-post finished clips, with
  spacing and daily caps (see [DEPLOY.md](DEPLOY.md#connecting-youtube-instagram-and-tiktok) for the one-time setup).
- **No sign-in.** It opens straight into the local owner's dashboard. Provider sign-in is only used when you connect
  a YouTube, Instagram or TikTok account for posting.
- **Your own AI.** Gemini, OpenAI, Grok, Groq, OpenRouter, Claude, a local model through Ollama, or anything that
  speaks the OpenAI chat format. No key at all also works: clips are then picked by loudness.

## Quick start

You need Python 3.11 or newer and `ffmpeg`. On Windows: `winget install Gyan.FFmpeg`. On a Mac: `brew install ffmpeg`.
On Linux: `sudo apt install ffmpeg`. RONB Clips finds it by itself and tells you in the dashboard if it is missing.

Put RONB Clips in a short folder that stays put: `C:\ronbclips` on Windows, `~/ronbclips` on a Mac or Linux. Not inside a
temporary folder or an AI assistant's workspace: those get cleaned up, and on Windows a folder that deep stops the
speech model from loading (Windows can't open files whose full path is over 260 characters). If an AI assistant
installs it for you, it should ask you where to put it first ([AGENTS.md](AGENTS.md) tells it to).

On Windows:

```bash
git clone <this repository> C:\ronbclips
```

```bash
cd C:\ronbclips
```

On a Mac or Linux:

```bash
git clone <this repository> ~/ronbclips
```

```bash
cd ~/ronbclips
```

```bash
pip install -r requirements.txt
```

Start the server:

```bash
python -m studio.server
```

The first start downloads the speech model by itself (about 480 MB, once).

Open http://127.0.0.1:8765. There is no sign-in: it opens straight into the dashboard. Go to **Settings, AI**, choose
your AI service, paste your key and press **Save and test** (see [Choose your AI](#choose-your-ai)).

## Choose your AI

RONB Clips needs a chat model that can return JSON. The easy way is in the dashboard: **Settings, AI**. Pick the
service, paste your key, press **Find models** to choose from the service's own list, then **Save and test**. The
key is kept in the data folder on your machine and is never shown again.

If you prefer a file (or run a server), the same three things can go in `.env`. What is saved in the dashboard wins:

```ini
RONBCLIPS_AI_PROVIDER=gemini
RONBCLIPS_AI_MODELS=gemini-2.5-flash
RONBCLIPS_AI_KEY=your-key-here
```

| `RONBCLIPS_AI_PROVIDER` | Service | Where to get a key |
|---|---|---|
| `gemini` | Google Gemini | https://aistudio.google.com/apikey |
| `openai` | OpenAI | https://platform.openai.com/api-keys |
| `xai` | Grok (xAI) | https://console.x.ai |
| `groq` | Groq | https://console.groq.com/keys |
| `openrouter` | OpenRouter (many models, one key) | https://openrouter.ai/keys |
| `anthropic` | Claude | https://console.anthropic.com |
| `ollama` | A model on your own machine, no key | https://ollama.com |
| `custom` | Any OpenAI-compatible address: also set `RONBCLIPS_AI_BASE_URL` | |

- `RONBCLIPS_AI_MODELS` is the model id exactly as the service names it. Model names change often, so check the
  service's current list. Several ids separated by commas are tried in order, which gives you a fallback.
- A fast, inexpensive model is the right choice. A one-hour video is read in about four requests.
- You pay the AI service directly for what you use. The **AI usage** page in the app shows how many requests and
  tokens each video took.
- Speech to text does not use the AI service. It runs on your own machine, so audio never leaves it.

## Settings

Everything is set through environment variables, normally in a `.env` file next to this README.
[`.env.example`](.env.example) lists every setting with a short explanation. The ones that matter first:

| Setting | What it is for |
|---|---|
| `RONBCLIPS_AI_PROVIDER`, `RONBCLIPS_AI_MODELS`, `RONBCLIPS_AI_KEY` | The AI that picks clips and writes titles (or set it in the dashboard) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional: connecting a YouTube channel |
| `RONBCLIPS_DATA` | Where videos, clips and the database are kept (default `./data`) |

`.env`, the `secrets/` folder and all user data are ignored by git. Keep keys there, never in the code.

## How it is built

| Part | What it is |
|---|---|
| `studio/` | The server: FastAPI, the clip pipeline, live recording and connected accounts |
| `studio/ai.py` | The only file that talks to an AI service. Add a provider here |
| `web/` | The app and public policy pages (plain HTML, CSS and JavaScript, no build step) |
| `tests/` | Tests that run against a throwaway copy of the app |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | One-server deployment with PostgreSQL and automatic HTTPS |

Video work is done with `ffmpeg` and `yt-dlp`. Speech is transcribed with `sherpa-onnx` and the Parakeet model.
Data is kept for the local owner: usage in the database (SQLite on your own computer, PostgreSQL on a server),
with videos and clips in the local owner's folder. [AGENTS.md](AGENTS.md) is a short map of the code for contributors and AI coding
assistants.

## Project documentation

- [Architecture and request flow](docs/architecture.md)
- [Generated API reference](docs/api.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Upgrading and database migrations](docs/upgrading.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)
- [Supported versions and compatibility](SUPPORT.md)
- [Changelog](CHANGELOG.md)

## Where your videos and clips are kept

Everything RONB Clips keeps is in one folder: videos, clips, recordings, creators, settings and the speech model. By
default it is the `data` folder inside RONB Clips. **Settings, Storage** shows the folder and how much room its drive
has, opens it in one click, and lets you move it to another drive: choose the new folder, and RONB Clips moves
everything there the next time it starts. If the drive is nearly full, RONB Clips says so and stops taking new videos
until there is room. (`RONBCLIPS_DATA` in `.env` still works and takes priority.)

## Updating

RONB Clips checks GitHub once a minute while it runs. When a newer version is out, the dashboard shows a notice
within a minute or two (no restart needed), with what changed
and how to update: ask your AI coding assistant to do it (the notice has a sentence to paste), or run
`git pull && pip install -r requirements.txt` in the RONB Clips folder and restart it (with Docker:
`git pull && docker compose up -d --build`). Your `.env`, keys and clips are not touched. Only the two commit ids
are sent to GitHub; set `RONBCLIPS_UPDATE_CHECK=0` to turn the check off. Back up first and review [upgrade and migration notes](docs/upgrading.md), especially when upgrading a database created by an older account-enabled version.

## Deploy

[DEPLOY.md](DEPLOY.md) walks through a single server with Docker: the app, PostgreSQL, and Caddy for HTTPS.

```bash
docker compose up -d --build
```

The release workflow publishes versioned Linux images to `ghcr.io/ronbtech/ronb_clips` when a matching `vX.Y.Z` tag is pushed. Preview tags (`0.x`) receive the `preview` channel; semantic prereleases receive `staging`; stable tags (`1.x` and later) receive `stable` and `latest`. Images publish for `linux/amd64` and `linux/arm64`; each release checks the manifest and boots the ARM64 image under QEMU. To use a published image instead of building from source, set `RONBCLIPS_IMAGE=ghcr.io/ronbtech/ronb_clips:X.Y.Z` in `.env`, then run `docker compose pull app` and `docker compose up -d --no-build`. The package must be public in GitHub Packages for unauthenticated pulls.

## How it behaves under load

- The page asks the server one small question every few seconds. When nothing has changed, the answer comes
  straight from memory.
- Each person's videos wait in their own line and the lines take turns, so one person cannot hold everyone up.
- The server measures its own processor, memory and disk every second. Extra videos and recordings start only
  while there is room, wait briefly when it is nearly full, and continue the moment it frees up.
- There are no limits on upload size, video length, recordings, creators or storage unless you set them in
  `.env`. Five videos can be sent at once. Request sizes are always capped, so junk can't fill the memory.

## Tests

```bash
python tests/run.py
```

This starts a copy of the app on a spare port with an empty temporary data folder, runs the tests against it, then
removes it. It needs no keys and never touches real data.

For contributors, `pip install -r requirements-dev.txt` adds the linter, formatter and type checker.
`pre-commit install` runs them before each commit: Ruff fixes and formats Python, and Pyright checks its types.

## Backups and recovery

The backup is a copy of the whole data folder: the database, settings, connected accounts, creators, jobs and clips.
The speech model and temporary files are left out, because they can be fetched again. The database copy is made
through SQLite, so it is complete even while RONB Clips is running.

```bash
python scripts/backup.py
```

That writes a folder such as `ronbclips-backup-20261122-230000`. To put one back, stop RONB Clips first:

```bash
python scripts/restore.py ronbclips-backup-20261122-230000 data/
```

`restore.py` also accepts a single `.db` file and a destination `.db` file, if you only want the database.

For PostgreSQL, the same scripts do both parts. With `DATABASE_URL` set to your Postgres address,
`python scripts/backup.py /path/to/backup` copies the data folder and writes `/path/to/backup/database.sql` with
`pg_dump`. The dump uses safe clean statements and carries no owners or privileges. To put it back, stop RONB Clips,
**recreate the target database or make sure it is empty**, then run
`python scripts/restore.py /path/to/backup /path/to/data`; the script feeds `database.sql` to `psql` with
`ON_ERROR_STOP=1` in one transaction, so a broken dump changes nothing. Both scripts read `.env` and use
`docker compose exec` by default; set `RONBCLIPS_PG_DUMP_BIN` and `RONBCLIPS_PG_RESTORE_BIN` to a local PostgreSQL
`bin` folder, or use the older `RONBCLIPS_PG_DUMP` and `RONBCLIPS_PG_RESTORE` command overrides. `DEPLOY.md` has the
same instructions.

On shutdown, RONB Clips first finishes work that is already queued: queued videos, single-clip renders and live
renders all get a turn, and new work is refused. It waits up to 30 seconds (`RONBCLIPS_DRAIN_SECONDS`); only work
that cannot finish in that time is marked to resume when the server starts again. `/health/detail` reports that the
server is shutting down while this happens. An interrupted restart (a crash, not a signal) still recovers unfinished
jobs and live recordings from the saved state.

Every log line is a small JSON object with a UTC time, a level, an event name and a request id. Send an
`X-Request-Id` header if you want to follow one request through the page, its background tasks and its workers;
otherwise RONB Clips makes one up and returns it in `X-Request-Id`. Set `RONBCLIPS_LOG_LEVEL` to `debug`, `info`
(the default), `warning` or `error` to choose how much is written.

Run `python scripts/benchmark.py` to time start-up, the database, files, a real HTTP upload, video probing and clip
rendering on this machine. Add `--transcribe` to time speech to text with a generated tone, or
`--transcribe <file.wav>` with your own recording; that loads the speech model.

## Security

- RONB Clips listens on this computer only (`127.0.0.1`) unless you explicitly deploy it, refuses changes sent by
  other websites, and only answers to its own address.
- There are no accounts, passwords or sessions: whoever can open the page is the owner. Keep access to that computer
  private. Every route uses that one local owner, never an identity supplied by the request.
- AI keys stay in the environment or `.env` (or in files named by the `*_FILE` settings) and are never sent back to
  the page.
- Only known video sites are fetched, and text from videos is treated as data when it is shown to the AI.
- Found a problem? Please report it privately through the repository's security advisories instead of opening a
  public issue.

Automated checks: `pip install -r requirements-dev.txt`, then `python scripts/security_audit.py`. CI runs the same
script with `--strict` (Bandit for source, pip-audit for dependencies, plus checks that secrets are not committed).
`DEPLOY.md` explains rotating keys, public-deployment assumptions and pinned images.

For tests, `python tests/run.py` runs the whole suite against a throwaway data folder. Add `RONBCLIPS_COVERAGE=1` to
also produce a coverage report and enforce the 85% floor set in `pyproject.toml`. To drive the real page in Chromium,
install the dev requirements, run `python -m playwright install chromium`, then `python scripts/e2e_browser.py`.
Set `RONBCLIPS_TEST_DATABASE_URL` to a PostgreSQL address to run the same suites against Postgres.

## Using it responsibly

Clip videos you made, or have permission to use. You are responsible for the rights to what you download, clip and
post, and for following the terms of the sites you fetch from and post to.

## Licence

[MIT](LICENSE). Caption fonts in `studio/fonts/` (Anton, Archivo Black, Poppins) are under the SIL Open Font
License; the licence texts are in the same folder.
