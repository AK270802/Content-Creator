"""
RevisionService

Loads an existing EditPlan + context (timeline events, transcript) and calls
the planning LLM with a natural-language instruction to produce a revised plan.
Returns a new LLMPlan (same schema as initial planner) without persisting —
the caller (endpoint) is responsible for creating the new version row.
Retries once on validation failure, matching the initial planner behaviour.
"""
from __future__ import annotations

import json
from loguru import logger
from pydantic import ValidationError

from app.config import settings
from app.schemas.edit_plan import LLMPlan
from app.services.planning import PlanningError


class RevisionService:

    def revise(
        self,
        video_id: str,
        current_plan_segments: list,
        scenes: list,
        timeline_events: list,
        transcript_segments: list,
        instruction: str,
        current_version: int = 1,
    ) -> LLMPlan:
        """
        Produce a revised LLMPlan based on the user instruction.
        Raises PlanningError after two failed LLM attempts.
        Falls back to PlanningError (not a silent template) because revision
        requires understanding the instruction — a template would ignore it.
        """
        if not settings.planning_model_base_url:
            raise PlanningError("No planning LLM configured — revision requires an LLM endpoint")

        prompt = self._build_prompt(
            video_id, current_plan_segments, scenes, timeline_events, transcript_segments, instruction
        )
        try:
            raw = self._call_llm(prompt)
            return self._validate(raw)
        except (ValidationError, Exception) as first_err:
            logger.warning(f"Revision attempt 1 failed for video {video_id}: {first_err}")
            retry_prompt = self._build_prompt(
                video_id, current_plan_segments, scenes, timeline_events,
                transcript_segments, instruction, previous_error=str(first_err)
            )
            try:
                raw2 = self._call_llm(retry_prompt)
                return self._validate(raw2)
            except Exception as second_err:
                raise PlanningError(f"Revision failed after retry: {second_err}") from second_err

    def _build_prompt(
        self,
        video_id: str,
        current_segments: list,
        scenes: list,
        timeline_events: list,
        transcript_segments: list,
        instruction: str,
        previous_error: str | None = None,
    ) -> str:
        current_plan_summary = [
            {
                "scene_id": str(seg.scene_id),
                "action": seg.action,
                "reason": seg.reason or "",
                "caption": seg.caption or "",
                "order": seg.order,
            }
            for seg in current_segments
        ]
        scenes_summary = [
            {
                "scene_id": str(sc.id),
                "scene_number": sc.scene_number,
                "start": sc.start_time,
                "end": sc.end_time,
                "description": sc.description or "",
                "visual_tags": sc.visual_tags or [],
            }
            for sc in scenes[:30]
        ]
        threshold = settings.timeline_confidence_threshold
        eligible_events = [te for te in timeline_events if te.confidence >= threshold]
        filtered_out = len(timeline_events) - len(eligible_events)
        if filtered_out:
            logger.info(
                f"Revision prompt: filtered {filtered_out} low-confidence timeline events "
                f"(threshold={threshold}) for video {video_id}"
            )
        timeline_summary = [
            {
                "start": te.start_ts,
                "end": te.end_ts,
                "description": te.description,
                "tags": te.tags or [],
            }
            for te in eligible_events[:50]
        ]
        transcript_preview = [
            {"start": s.start_time, "end": s.end_time, "text": s.text}
            for s in transcript_segments[:40]
        ]
        schema_desc = (
            '{"segments": [{"scene_id": "<uuid>", "action": "keep" | "cut", '
            '"reason": "<why>", "caption": "<text overlay>", "order": <int>}]}'
        )
        prompt = (
            f"You are a professional video editor revising an edit plan for video {video_id}.\n\n"
            f"USER INSTRUCTION: {instruction}\n\n"
            f"Apply the instruction to produce a COMPLETE revised plan (not a diff).\n"
            f"Return ONLY valid JSON matching: {schema_desc}\n\n"
            f"Current plan:\n{json.dumps(current_plan_summary, indent=2)}\n\n"
            f"Available scenes:\n{json.dumps(scenes_summary, indent=2)}\n\n"
        )
        if timeline_summary:
            prompt += f"Event timeline:\n{json.dumps(timeline_summary, indent=2)}\n\n"
        prompt += f"Transcript (first 40 segments):\n{json.dumps(transcript_preview, indent=2)}"
        if previous_error:
            prompt += f"\n\nPrevious attempt failed validation:\n{previous_error}\nFix the JSON."
        return prompt

    def _call_llm(self, prompt: str) -> dict:
        import json as _json
        from openai import OpenAI
        client = OpenAI(
            base_url=settings.planning_model_base_url or None,
            api_key="not-needed",
        )
        resp = client.chat.completions.create(
            model=settings.planning_model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=settings.planning_temperature,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return _json.loads(raw)

    def _validate(self, data: dict) -> LLMPlan:
        return LLMPlan.model_validate(data)


revision_service = RevisionService()
