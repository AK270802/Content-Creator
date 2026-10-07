import os
import base64
import json
import tempfile
from loguru import logger

from app.config import settings


class VisionService:
    """
    Extracts representative frames from a scene and queries a vision-language
    model (OpenAI-compatible endpoint) for structured scene metadata.
    Works with Ollama (llava), vLLM vision models, or any OAI-compatible API
    — endpoint and model are configured entirely via environment variables.
    """

    def _extract_frames(
        self, video_path: str, start: float, end: float, tmpdir: str
    ) -> list[str]:
        import ffmpeg

        duration = max(end - start, 0.1)
        count = min(settings.vision_frame_count, max(1, int(duration)))
        frame_paths = []

        for i in range(count):
            ts = start + (duration / (count + 1)) * (i + 1)
            frame_path = os.path.join(tmpdir, f"frame_{i:03d}.jpg")
            try:
                (
                    ffmpeg.input(video_path, ss=ts)
                    .output(frame_path, vframes=1, qscale=2)
                    .overwrite_output()
                    .run(quiet=True)
                )
                if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                    frame_paths.append(frame_path)
            except Exception as e:
                logger.warning(f"Frame extraction at {ts:.2f}s failed: {e}")

        return frame_paths

    def _encode_image(self, path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def _build_prompt(self) -> str:
        return (
            "Analyse this video scene and respond with ONLY valid JSON (no markdown). "
            "Schema: "
            '{"description": "<one sentence>", '
            '"visual_tags": ["<tag1>", "<tag2>", ...], '
            '"quality_flags": {"blurry": <bool>, "poorly_framed": <bool>, '
            '"static": <bool>, "silent": <bool>}}'
        )

    def _call_vision_llm(self, frame_paths: list[str]) -> dict:
        from app.services.llm_provider import make_client, vision_endpoint

        ep = vision_endpoint()
        client = make_client(ep)

        content: list[dict] = [{"type": "text", "text": self._build_prompt()}]
        for fp in frame_paths:
            b64 = self._encode_image(fp)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        resp = client.chat.completions.create(
            model=ep.model,
            messages=[{"role": "user", "content": content}],
            max_tokens=512,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content or "{}"
        # Strip markdown code fences if present
        raw = raw.strip().strip("```json").strip("```").strip()
        return json.loads(raw)

    def tag_scene(
        self, video_path: str, start_time: float, end_time: float
    ) -> dict:
        """
        Returns dict with keys: description, visual_tags, quality_flags.
        Returns empty defaults if vision model is not configured or call fails.
        """
        empty = {"description": None, "visual_tags": [], "quality_flags": {}}
        from app.services.llm_provider import vision_endpoint
        if not vision_endpoint().configured:
            return empty

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                frames = self._extract_frames(video_path, start_time, end_time, tmpdir)
                if not frames:
                    return empty
                result = self._call_vision_llm(frames)
                return {
                    "description": result.get("description"),
                    "visual_tags": result.get("visual_tags", []),
                    "quality_flags": result.get("quality_flags", {}),
                }
        except Exception as e:
            logger.warning(f"Vision tagging failed for [{start_time:.2f}-{end_time:.2f}]: {e}")
            return empty


vision_service = VisionService()
