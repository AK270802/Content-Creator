# Product linking status — Lumina Edit

Updated after Stages 2–6 wiring.

## Linked end-to-end

| Flow | Path |
|------|------|
| Editor route | `/editor/$videoId` registered in `routeTree.gen.ts` |
| Auth | Editor uses `api-client` + `getAccessToken` (no broken cookie read) |
| Timeline Save | Toolbar Save / ⌘S → `buildPlanPatchFromTimeline` → `PATCH edit-plans` (incl. effects) |
| Export | Saves dirty timeline → `approve` with preset/brand → `/videos/$id/render?job=` |
| Render page | Resolves latest job via `GET /videos/{id}/render-jobs` if no `?job=` |
| AI Command | Applies `operations` to timeline via `cutRanges` (then Save) |
| Silence | Source→timeline mapping → cutRanges |
| Captions | Edit + SRT/VTT via `api.exportCaptions` |
| Brand kits | CRUD + logo upload `POST /brand-kits/{id}/logo` |
| Thumbnails | `/videos/$id/thumbnails` studio + regenerate |
| Translate/Dub | Copilot → real MT/TTS providers with stub fallback |
| Nav | Dashboard actions, video detail, review → Editor / Thumbs / Render |
| Proxy preview | Ingest generates 720p proxy; `playback_url` prefers it |
| Animated reframe | Shot-aware keyframed crop on export |
| Clip effects | Properties → plan → ffmpeg burn |

## Apply migrations

```bash
cd backend && alembic upgrade head
```

Optional: `pip install edge-tts` (also in requirements). Set `PLANNING_MODEL_BASE_URL` / `VLLM_BASE_URL` for real translation; `TTS_PROVIDER=edge|openai|auto`.

See [stage-6-status.md](./stage-6-status.md) for Stage 6 details.
