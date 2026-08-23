"""
EventTimelineService

Chunks a video into 15-30s windows (snapped to existing scene cut points),
sends each chunk as native video input to an OpenAI-compatible vision model,
and extracts a list of timestamped events.  After all chunks are processed,
adjacent events that continue the same action are merged.

Stream copy is used for clip extraction where possible to avoid re-encode cost.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from loguru import logger

from app.config import settings

_TARGET_WINDOW = 20.0   # ideal chunk length in seconds
_SNAP_RANGE = 8.0       # accept a scene boundary within 8s of ideal cut point
_MERGE_GAP = 1.0        # merge events whose gap is <= this many seconds


class EventTimelineService:

    # ── public ─────────────────────────────────────────────────────────────────

    def extract_timeline(
        self,
        video_path: str,
        video_duration: float,
        scene_boundaries: list[float],
    ) -> list[dict]:
        """
        Returns merged event list:
          [{chunk_id, start_ts, end_ts, description, tags, confidence}]
        Returns [] when no vision model is configured.
        """
        if not settings.vision_model_base_url:
            logger.info("No vision model configured — skipping timeline extraction")
            return []

        chunks = self._build_chunks(video_duration, scene_boundaries)
        logger.info(f"Timeline: {len(chunks)} chunk(s) for {video_duration:.1f}s video")

        raw_events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for chunk_id, (c_start, c_end) in enumerate(chunks):
                clip_path = os.path.join(tmpdir, f"chunk_{chunk_id:03d}.mp4")
                try:
                    self._extract_clip(video_path, clip_path, c_start, c_end)
                    events = self._process_chunk(clip_path, c_start, c_end, chunk_id)
                    raw_events.extend(events)
                except Exception as exc:
                    logger.warning(f"Timeline chunk {chunk_id} ({c_start:.1f}-{c_end:.1f}s) failed: {exc}")

        merged = self._merge_adjacent_events(raw_events)
        logger.info(f"Timeline: {len(raw_events)} raw -> {len(merged)} merged events")
        return merged

    # ── chunking ───────────────────────────────────────────────────────────────

    def _build_chunks(
        self,
        duration: float,
        scene_boundaries: list[float],
        target: float = _TARGET_WINDOW,
        snap: float = _SNAP_RANGE,
    ) -> list[tuple[float, float]]:
        """
        Build windows of ~target seconds.  Snap each cut to the nearest
        scene boundary within snap seconds, so windows never bisect a cut.
        """
        chunks: list[tuple[float, float]] = []
        start = 0.0
        while start < duration - 0.5:
            ideal_end = start + target
            if ideal_end >= duration:
                chunks.append((start, duration))
                break
            candidates = [
                b for b in scene_boundaries
                if abs(b - ideal_end) <= snap and b > start + 5.0
            ]
            end = min(candidates, key=lambda b: abs(b - ideal_end)) if candidates else ideal_end
            chunks.append((start, end))
            start = end
        return chunks

    # ── clip extraction ─────────────────────────────────────────────────────────

    def _extract_clip(self, video_path: str, out_path: str, start: float, end: float) -> None:
        """Stream-copy clip; falls back to re-encode if stream copy fails."""
        import subprocess
        duration = end - start
        cmd_copy = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            out_path,
        ]
        result = subprocess.run(cmd_copy, capture_output=True)
        if result.returncode != 0:
            cmd_encode = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(duration),
                "-vcodec", "libx264", "-an",
                out_path,
            ]
            subprocess.run(cmd_encode, capture_output=True, check=True)

    # ── LLM call ───────────────────────────────────────────────────────────────

    def _process_chunk(
        self, clip_path: str, chunk_start: float, chunk_end: float, chunk_id: int
    ) -> list[dict]:
        with open(clip_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        prompt = self._build_prompt(chunk_start, chunk_end)
        raw = self._call_vision_llm(b64, prompt)
        return self._parse_events(raw, chunk_start, chunk_id)

    def _build_prompt(self, chunk_start: float, chunk_end: float) -> str:
        return (
            f"This video clip spans {chunk_start:.1f}s-{chunk_end:.1f}s of a longer video. "
            "Identify every distinct visual or spoken event in this clip. "
            "Respond with ONLY valid JSON (no markdown fences).\n"
            'Schema: {"events": [{"start": <seconds_from_clip_start>, "end": <seconds>, '
            '"description": "<concise action or event>", "tags": ["<tag>", ...], '
            '"confidence": <0.0-1.0>}]}\n'
            "Tags: short noun/verb phrases e.g. person speaking, product demo, "
            "text on screen, music, action shot. "
            "Timestamps are relative to the START of this clip."
        )

    def _call_vision_llm(self, video_b64: str, prompt: str) -> dict:
        from openai import OpenAI
        client = OpenAI(
            base_url=settings.vision_model_base_url or None,
            api_key="not-needed",
        )
        resp = client.chat.completions.create(
            model=settings.vision_model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=1024,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)

    def _parse_events(self, raw: dict, chunk_start: float, chunk_id: int) -> list[dict]:
        events = []
        for item in raw.get("events", []):
            try:
                start_abs = chunk_start + float(item["start"])
                end_abs = chunk_start + float(item["end"])
                if end_abs <= start_abs:
                    end_abs = start_abs + 1.0
                events.append({
                    "chunk_id": chunk_id,
                    "start_ts": round(start_abs, 3),
                    "end_ts": round(end_abs, 3),
                    "description": str(item.get("description", "")).strip(),
                    "tags": [str(t) for t in item.get("tags", [])],
                    "confidence": max(0.0, min(1.0, float(item.get("confidence", 1.0)))),
                })
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(f"Skipping malformed timeline event: {exc}")
        return events

    # ── temporal merge ─────────────────────────────────────────────────────────

    def _merge_adjacent_events(self, events: list[dict]) -> list[dict]:
        """
        Merge consecutive events when they share >= 1 tag AND
        the gap between them is <= _MERGE_GAP seconds.
        """
        if not events:
            return []
        sorted_evts = sorted(events, key=lambda e: e["start_ts"])
        merged = [sorted_evts[0].copy()]
        for evt in sorted_evts[1:]:
            prev = merged[-1]
            shared_tags = set(evt["tags"]) & set(prev["tags"])
            gap = evt["start_ts"] - prev["end_ts"]
            if shared_tags and gap <= _MERGE_GAP:
                prev["end_ts"] = evt["end_ts"]
                prev["description"] = prev["description"] + "; " + evt["description"]
                prev["tags"] = sorted(set(prev["tags"]) | set(evt["tags"]))
                prev["confidence"] = (prev["confidence"] + evt["confidence"]) / 2
            else:
                merged.append(evt.copy())
        return merged


event_timeline_service = EventTimelineService()
