import uuid
from typing import TypedDict
from loguru import logger

from app.config import settings
from app.schemas.agent import EditPlan, CutOperation


class AgentState(TypedDict):
    video_id: str
    instruction: str
    transcript: list[dict]
    scenes: list[dict]
    edit_plan: dict


def _build_template_plan(video_id: str, instruction: str, scenes: list[dict]) -> EditPlan:
    operations = []
    if scenes and scenes[0].get("end_time", -1) > 0:
        operations.append(CutOperation(
            type="cut",
            start_time=scenes[0]["start_time"],
            end_time=min(scenes[0]["end_time"], 5.0),
            reason=f"Applying instruction: {instruction}",
        ))
    return EditPlan(
        video_id=uuid.UUID(video_id),
        instruction=instruction,
        operations=operations,
        summary=f"Template plan for: {instruction}",
    )


def run_agent(video_id: str, instruction: str, transcript: list[dict], scenes: list[dict]) -> tuple[EditPlan, bool]:
    from app.services.llm_provider import planning_endpoint
    ep = planning_endpoint()
    if settings.vllm_base_url:
        base_url, model, api_key = settings.vllm_base_url, settings.vllm_model, "not-needed"
    elif ep.configured:
        base_url, model, api_key = ep.base_url, ep.model, ep.api_key
    else:
        logger.info("No LLM configured, returning template plan")
        return _build_template_plan(video_id, instruction, scenes), False

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        import json

        llm = ChatOpenAI(
            base_url=base_url,
            model=model,
            temperature=settings.llm_temperature,
            api_key=api_key,
        )

        context = {
            "scenes": scenes[:10],
            "transcript_preview": transcript[:20],
        }
        prompt = (
            f"You are a video editor AI. Given the following video context and instruction, "
            f"produce a JSON edit plan.\n\n"
            f"Instruction: {instruction}\n\n"
            f"Context (first 10 scenes, first 20 transcript segments):\n{json.dumps(context, indent=2)}\n\n"
            f"Respond ONLY with valid JSON matching this schema: "
            f'{{"operations": [{{"type": "cut", "start_time": float, "end_time": float, "reason": str}}], '
            f'"summary": str}}'
        )

        response = llm.invoke([HumanMessage(content=prompt)])
        data = json.loads(response.content)
        operations = [CutOperation(**op) for op in data.get("operations", [])]
        plan = EditPlan(
            video_id=uuid.UUID(video_id),
            instruction=instruction,
            operations=operations,
            summary=data.get("summary", "LLM-generated plan"),
        )
        return plan, True
    except Exception as e:
        logger.error(f"Agent LLM call failed: {e}. Falling back to template.")
        return _build_template_plan(video_id, instruction, scenes), False
