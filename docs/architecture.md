# Architecture and request flow

RONB Clips is a single FastAPI process with a plain HTML/CSS/JavaScript frontend. The server and background workers share a database and a data folder. A laptop can use SQLite; a hosted deployment uses PostgreSQL and the same data-folder layout for media.

## Main parts

```mermaid
flowchart LR
    Browser[Browser\nHTML + CSS + JavaScript] -->|HTTP /api, /media, /oauth| App[FastAPI server]
    App --> Routes[Route modules\nproviders, creators, jobs, settings]
    Routes --> State[SQLite or PostgreSQL]
    Routes --> Files[Data folder\nsource videos, clips, settings]
    Routes --> Queue[Fair work queue]
    Queue --> Pipeline[Download / upload\ntranscribe / score / render]
    Pipeline --> Tools[ffmpeg + yt-dlp + ASR]
    Pipeline --> State
    Pipeline --> Files
    Pipeline --> AI[Optional AI provider]
    Browser -->|poll /api/sync| App
    App -->|ctx.bump invalidates cached sync state| Sync[Per-owner change counter]
    Sync --> App
    Browser -->|OAuth redirect| App
    App <-->|provider authorization| Platforms[YouTube / Instagram / TikTok]
```

## Request flow

The browser sends API requests to the same origin. The server checks the host, origin and request limits before a route runs. In this local-owner build there is no sign-in: all application data belongs to the built-in owner. OAuth is only for connecting a posting destination.

```mermaid
sequenceDiagram
    actor Person
    participant UI as Browser UI
    participant Guard as Request checks
    participant API as FastAPI route
    participant Queue as Background queue
    participant DB as Database + data folder
    Person->>UI: Paste a link or upload a video
    UI->>Guard: POST /api/jobs or /api/upload
    Guard->>API: Accept same-origin request
    API->>DB: Save job and source metadata
    API->>Queue: Schedule work fairly
    API-->>UI: Return job id
    Queue->>DB: Read progress and save results
    Queue->>DB: ctx.bump() after visible changes
    UI->>Guard: GET /api/sync?v=...
    Guard->>API: Check request
    API->>DB: Return changed jobs and creators
    API-->>UI: Updated state or unchanged response
```

## Code map

- `studio/server.py` assembles the app, applies request checks and starts workers.
- `studio/*_api.py`, `studio/providers.py`, and `studio/settings.py` define HTTP routes and request models.
- `studio/jobs.py`, `studio/transcribe.py`, `studio/brain.py`, and `studio/render.py` run the clipping pipeline.
- `studio/db.py` and `studio/migrations.py` define storage and schema changes.
- `web/index.html` and `web/assets/` are the no-build frontend.
- `data/` is runtime state and is not source code.

## API schema

The running app serves the interactive API reference at [`/api/docs`](http://127.0.0.1:8765/api/docs) and its OpenAPI JSON at [`/api/openapi.json`](http://127.0.0.1:8765/api/openapi.json). See [API reference](api.md) for access rules and route groups.
