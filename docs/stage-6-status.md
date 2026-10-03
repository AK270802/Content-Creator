# Stage 6 status — shot-aware reframe, MT/TTS, effects, proxy

## Built

### Shot-aware animated reframe
- `app/services/reframe.py` — face timeline, shot jumps, smoothed keyframes, piecewise ffmpeg crop
- Render calls `apply_reframe(..., animated=True)`

### Translation + TTS
- `app/services/translation.py` — vLLM/Ollama OpenAI-compatible MT, stub fallback
- `app/services/tts.py` — edge-tts → OpenAI speech → silence stub (lazy settings import)
- Studio translate/dub persist `LocalizationJob` + optional dub audio in MinIO
- Config: `TTS_PROVIDER`, `TTS_MODEL`
- Deps: `edge-tts`, `openai` in requirements.txt

### Effects (persist + burn)
- Clip fields: brightness, contrast, saturation, fadeIn/Out, effect
- Properties panel + PreviewCanvas preview
- Save → `edit_plan_segments` columns (migration 012)
- Render `_extract_clip` applies eq + fade filters

### Proxy preview
- `app/services/proxy.py` — 720p H.264 proxy in ingest pipeline
- `videos.proxy_key` (migration 011)
- `VideoResponse.playback_url` prefers proxy when present

## Migrate

```bash
cd backend && alembic upgrade head
```

Optional: install deps; set `PLANNING_MODEL_BASE_URL` or `VLLM_BASE_URL` for real translation.
