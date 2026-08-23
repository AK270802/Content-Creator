# Vision & Planning LLM Setup

Two model endpoints power the pipeline:

| Role | Service | Config keys |
|---|---|---|
| Vision / timeline extraction | VisionService, EventTimelineService | `VISION_MODEL_BASE_URL`, `VISION_MODEL_NAME` |
| Edit planning / revision | PlanningService, RevisionService | `PLANNING_MODEL_BASE_URL`, `PLANNING_MODEL_NAME` |

Both use the OpenAI-compatible chat-completions API, so any endpoint that speaks `/v1/chat/completions` works.

---

## Option A — Ollama (current staging setup)

Ollama endpoint: **https://ollama.baseel.com**

```env
# Vision (qwen3-vl for timeline extraction and scene tagging)
VISION_MODEL_BASE_URL=https://ollama.baseel.com/v1
VISION_MODEL_NAME=qwen3-vl:8b

# Planning / revision
PLANNING_MODEL_BASE_URL=https://ollama.baseel.com/v1
PLANNING_MODEL_NAME=qwen3-vl:8b
PLANNING_TEMPERATURE=0.1
```

Ollama''s `/v1` path is its OpenAI-compat shim. The `api_key` field is ignored — the
client sends `"not-needed"` which Ollama accepts.

### Confirming the model is loaded

```bash
curl https://ollama.baseel.com/api/tags | python -m json.tool
# Look for "qwen3-vl:8b" in the "models" list

# One-shot generate test
curl -X POST https://ollama.baseel.com/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-vl:8b", "prompt": "Say hello", "stream": false}'
```

### Multimodal / video input

EventTimelineService sends video clips as base64 data URIs inside a `video_url` content block.
Qwen3-VL supports this natively. If your Ollama version does not yet expose
`/v1/chat/completions` with multimodal support, upgrade to Ollama >= 0.3 which added
native OpenAI-compat multimodal routing.

---

## Option B — vLLM (GPU host / production)

Use vLLM when you need batched throughput or want to host the model yourself.

### Required launch flags for Qwen3-VL

```bash
docker run --gpus all \
  -p 8001:8000 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-VL-7B-Instruct \
  --served-model-name qwen3-vl \
  --chat-template /path/to/qwen3vl_chat_template.jinja \
  --limit-mm-per-prompt image=10,video=1 \
  --max-model-len 32768 \
  --trust-remote-code \
  --dtype bfloat16
```

| Flag | Why |
|---|---|
| `--limit-mm-per-prompt image=10,video=1` | Caps multi-modal inputs per request; prevents OOM on long timelines |
| `--chat-template` | Qwen3-VL needs its own Jinja template — the default tokenizer template mis-formats vision tokens |
| `--max-model-len 32768` | 32 k context fits a 20 s video chunk encoded as ~6 k tokens |
| `--trust-remote-code` | Required for Qwen3-VL''s custom modelling code |
| `--dtype bfloat16` | Recommended for A100/H100; use `float16` on older GPUs |

### Chat template

Download the official template before launching:

```bash
pip install huggingface_hub
python -c "
from huggingface_hub import hf_hub_download
print(hf_hub_download(
    repo_id='Qwen/Qwen3-VL-7B-Instruct',
    filename='chat_template.json'
))
"
```

Then pass the path to `--chat-template`.

### .env for vLLM

```env
VISION_MODEL_BASE_URL=http://<gpu-host>:8001/v1
VISION_MODEL_NAME=qwen3-vl
PLANNING_MODEL_BASE_URL=http://<gpu-host>:8001/v1
PLANNING_MODEL_NAME=qwen3-vl
```

---

## Verifying end-to-end

1. Upload a short test video (<60 s) via `POST /api/v1/videos/upload`.
2. Poll `GET /api/v1/videos/{id}/status` until `READY`.
3. Check `GET /api/v1/videos/{id}/scenes` — descriptions and visual_tags should be populated.
4. Check `GET /api/v1/videos/{id}/edit-plan` — plan should be `llm_generated: true`.
5. Inspect Grafana → Task Runtime p95 panel — `tasks.process_video` runtime indicates whether vision inference is completing in acceptable time.

---

## Confidence threshold tuning

Timeline events with `confidence < TIMELINE_CONFIDENCE_THRESHOLD` (default `0.5`) are
stored in the DB but excluded from the planning and revision prompts. Lower this value
to pass more uncertain events to the LLM; raise it to reduce prompt noise.

```env
TIMELINE_CONFIDENCE_THRESHOLD=0.5   # 0.0–1.0, default 0.5
```
