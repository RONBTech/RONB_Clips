# API reference

The running FastAPI server generates the complete, version-matched API reference:

- Interactive Swagger UI: `http://127.0.0.1:8765/api/docs`
- OpenAPI JSON: `http://127.0.0.1:8765/api/openapi.json`

Change the host and port to match your `.env`. The schema is generated from the route definitions and Pydantic request/response models in `studio/`; it is the source of truth for endpoint fields.

## Access and safety

The app has one local owner and does not have user sign-in. Open the UI only on a machine or private network you control. The API is same-origin and protected by the server's host, origin, size and rate checks. Do not expose it directly to the public internet without the reverse-proxy and TLS setup in [DEPLOY.md](../DEPLOY.md).

OAuth routes only connect a destination account for posting; they do not create a RONB Clips login. Provider setup is documented in [DEPLOY.md](../DEPLOY.md#connecting-youtube-instagram-and-tiktok).

## Route groups

| Group | Common routes | Purpose |
|---|---|---|
| App and health | `/api/status`, `/api/sync`, `/api/overview`, `/healthz`, `/readyz` | Startup state, dashboard updates and service health |
| Videos and clips | `/api/jobs`, `/api/jobs/{job_id}/clips/{clip_id}` | Submit, list, edit, render and delete video work |
| Uploads | `/api/upload` | Start and finish chunked browser uploads |
| Creators and live | `/api/creators`, `/api/creators/{creator_id}/live` | Creator workspaces, channels and live recordings |
| AI and usage | `/api/ai`, `/api/ai/test`, `/api/usage` | AI configuration, connectivity checks and token history |
| Posting | `/api/posts`, `/api/accounts` | Queue posts and manage connected destinations |
| Settings | `/api/settings`, `/api/apps` | Local settings and OAuth app configuration |
| OAuth callbacks | `/oauth/{provider}/callback` | Finish a provider's authorization flow |

Use `/api/openapi.json` for exact methods, fields, response schemas and status codes. The Swagger page has “Try it out” disabled; it is a reference only. API requests sent separately still pass through the app's normal request checks.
