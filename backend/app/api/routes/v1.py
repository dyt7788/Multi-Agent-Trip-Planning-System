# coding: utf-8
"""Versioned API routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_settings
from app.models.schemas import (
    AgentTrace,
    ImageAgentOutput,
    ImageAnalysisRequest,
    ItineraryPlan,
    PreferenceMemory,
    PreferenceUpdateRequest,
    QueryAgentOutput,
    QueryResearchRequest,
    TravelPlanResponse,
    TripPlanRequest,
)
from TotalAgent.agent import TotalAgent
from TravelCore.text import utc_now


router = APIRouter()
settings = get_settings()
coordinator = TotalAgent(settings)


# ===== SSE Streaming Helpers =====

def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _stream_plan(request: TripPlanRequest) -> AsyncGenerator[str, None]:
    """流式输出 agent 执行进度，最终返回完整行程数据。"""
    started_event = "agent_started"
    completed_event = "agent_completed"
    thinking_event = "agent_thinking"
    done_event = "done"

    # Thinking token queue for real-time streaming
    thinking_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    def make_thinking_cb(agent_name: str = "ParserAgent"):
        def cb(token: str):
            try:
                thinking_queue.put_nowait(token)
            except asyncio.QueueFull:
                pass
        return cb

    async def drain_thinking(agent_name: str = "ParserAgent"):
        while True:
            try:
                token = await asyncio.wait_for(thinking_queue.get(), timeout=0.3)
                yield _event(thinking_event, {"agent": agent_name, "token": token})
            except asyncio.TimeoutError:
                break

    # Step 0: ParserAgent — 从 free_text 中提取目的地和天数
    from TravelCore.llm import create_agent_llm

    llm = create_agent_llm("TotalAgent", coordinator.settings)
    parse_prompt = (
        f"请从以下用户的旅行需求中提取出【目的地】和【旅行天数】。\n\n"
        f"用户输入：{request.free_text}\n\n"
        f"以 JSON 格式返回，仅包含两个字段：{{\"destination\": \"目的地名称\", \"days\": 数字}}\n"
        f"如果天数没有明确数字，根据天数描述推断（如'几天'=3，'一周'=7）。\n"
        f"如果没有提到天数，默认返回 3。\n"
        f"如果目的地不明确，返回空字符串。"
    )

    yield _event(started_event, {"agent": "ParserAgent", "message": "正在分析你的需求..."})
    parse_task = asyncio.create_task(llm.complete_text(
        [{"role": "user", "content": parse_prompt}],
        fallback=json.dumps({"destination": "", "days": 3}, ensure_ascii=False),
        response_format={"type": "json_object"},
    ))
    parse_text = await parse_task

    try:
        parse_json = json.loads(parse_text)
        extracted_dest = parse_json.get("destination", "") or request.destination or "未知目的地"
        extracted_days = parse_json.get("days", 0) or request.days or 3
    except (json.JSONDecodeError, AttributeError):
        extracted_dest = request.destination or "未知目的地"
        extracted_days = request.days or 3

    request.destination = extracted_dest
    request.days = extracted_days

    yield _event(completed_event, {
        "agent": "ParserAgent",
        "message": f"提取到目的地：{extracted_dest}，{extracted_days}天",
    })

    # Step 1: StrategyAgent
    yield _event(started_event, {"agent": "StrategyAgent", "message": "正在生成旅行策略..."})
    strategy_output = await coordinator.strategy_agent.run(request)
    yield _event(completed_event, {
        "agent": "StrategyAgent",
        "message": "根据偏好和记忆生成了查询方案。",
        "metadata": strategy_output.trace[0].metadata if strategy_output.trace else {},
    })

    # Step 2: QueryAgent + ImageAgent (parallel)
    yield _event(started_event, {"agent": "QueryAgent", "message": "正在搜索旅行攻略信息..."})
    yield _event(started_event, {"agent": "ImageAgent", "message": "正在搜索目的地图片..."})
    query_task = asyncio.create_task(coordinator.query_agent.run(strategy_output))
    image_task = coordinator.image_agent.run(request)
    query_output, image_output = await asyncio.gather(query_task, image_task)
    yield _event(completed_event, {
        "agent": "QueryAgent",
        "message": f"从 {len(query_output.sources)} 个来源获取了信息。",
    })
    yield _event(completed_event, {
        "agent": "ImageAgent",
        "message": f"找到 {len(image_output.observations)} 张图片。",
    })

    # Step 3: AnalysisAgent
    thinking_cb = make_thinking_cb("AnalysisAgent")
    yield _event(started_event, {"agent": "AnalysisAgent", "message": "正在分析景点并规划行程..."})
    analysis_task = asyncio.create_task(coordinator.analysis_agent.run(
        request,
        strategy_output,
        query_output,
        image_output,
        strategy_output.memory,
        thinking_callback=thinking_cb,
    ))
    async for evt in drain_thinking("AnalysisAgent"):
        yield evt
    analysis_output, plan, analysis_trace = await analysis_task
    while not thinking_queue.empty():
        try:
            token = thinking_queue.get_nowait()
            yield _event(thinking_event, {"agent": "AnalysisAgent", "token": token})
        except asyncio.QueueEmpty:
            break
    yield _event(completed_event, {
        "agent": "AnalysisAgent",
        "message": f"生成了 {len(plan.itinerary)} 天的行程安排。",
    })

    # Step 4: ReportAgent
    yield _event(started_event, {"agent": "ReportAgent", "message": "正在生成旅行报告..."})
    report_output, report_trace = await coordinator.report_agent.run(plan, request.export_pdf, analysis_output)
    yield _event(completed_event, {
        "agent": "ReportAgent",
        "message": "已生成 HTML 格式旅行报告。",
    })

    # Memory updates
    await coordinator.strategy_agent.remember_plan(request, plan.model_dump(mode="json"))
    coordinator.memory.set_short_term(
        request.user_id,
        {
            "destination": request.destination,
            "days": request.days,
            "spots": [spot.model_dump(mode="json") for spot in analysis_output.spots],
            "trip_id": plan.trip_id,
        },
    )
    coordinator.memory.record_trip(
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        trip_data={
            "trip_id": plan.trip_id,
            "destination": request.destination,
            "days": request.days,
            "kept_spots": [spot.name for spot in analysis_output.spots if spot.status in ("推荐", "用户确认")],
        },
    )

    # Build trace
    total_trace = [
        AgentTrace(
            agent="TotalAgent",
            status="completed",
            message="已完成全部 agent 调用。",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
    ]
    trace = total_trace + strategy_output.trace + query_output.trace + image_output.trace + analysis_trace + report_trace

    # Build final data matching frontend StreamDoneData type
    images_data = {
        "destination": image_output.destination,
        "observations": [
            {
                "image_url": obs.image_url,
                "labels": obs.labels,
                "scene_type": obs.scene_type,
                "inferred_location": obs.inferred_location,
                "description": obs.description,
            }
            for obs in image_output.observations
        ],
    }

    final = {
        "plan": plan.model_dump(mode="json"),
        "query": {
            "destination": query_output.destination,
            "sources": [s.model_dump(mode="json") for s in query_output.sources],
            "extracted": query_output.extracted.model_dump(mode="json"),
        },
        "images": images_data,
        "trace": [t.model_dump(mode="json") for t in trace],
        "reports": [a.model_dump(mode="json") for a in report_output.artifacts],
    }
    yield _event(done_event, final)


async def _stream_modify(request: TripPlanRequest, previous_plan: ItineraryPlan) -> AsyncGenerator[str, None]:
    """流式输出修改行程的步骤。"""
    thinking_event = "agent_thinking"

    # Thinking token queue
    thinking_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    def make_thinking_cb(agent_name: str):
        def cb(token: str):
            try:
                thinking_queue.put_nowait(token)
            except asyncio.QueueFull:
                pass
        return cb

    # Step 1: ImageAgent
    yield _event("agent_started", {"agent": "ImageAgent", "message": "正在搜索目的地图片..."})
    image_output = await coordinator.image_agent.run(request)
    yield _event("agent_completed", {
        "agent": "ImageAgent",
        "message": f"找到 {len(image_output.observations)} 张图片。",
    })

    # Step 2: AnalysisAgent with previous_plan
    thinking_cb = make_thinking_cb("AnalysisAgent")
    yield _event("agent_started", {"agent": "AnalysisAgent", "message": "正在根据你的意见调整行程..."})
    analysis_task = asyncio.create_task(coordinator.analysis_agent.run(
        request,
        QueryAgentOutput(
            destination=previous_plan.destination,
            days=previous_plan.days,
            extracted=coordinator.analysis_agent._empty_extraction(previous_plan.destination),
        ),
        image_output,
        None,
        None,
        previous_plan=previous_plan,
        modification_text=request.modification_request,
        thinking_callback=thinking_cb,
    ))
    try:
        while not analysis_task.done():
            try:
                token = await asyncio.wait_for(thinking_queue.get(), timeout=0.3)
                yield _event(thinking_event, {"agent": "AnalysisAgent", "token": token})
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        pass
    analysis_output, plan, analysis_trace = await analysis_task
    while not thinking_queue.empty():
        try:
            token = thinking_queue.get_nowait()
            yield _event(thinking_event, {"agent": "AnalysisAgent", "token": token})
        except asyncio.QueueEmpty:
            break
    yield _event("agent_completed", {
        "agent": "AnalysisAgent",
        "message": f"已调整生成 {len(plan.itinerary)} 天的新行程。",
    })

    # Step 3: ReportAgent
    yield _event("agent_started", {"agent": "ReportAgent", "message": "正在重新生成旅行报告..."})
    report_output, report_trace = await coordinator.report_agent.run(plan, request.export_pdf, analysis_output)
    yield _event("agent_completed", {
        "agent": "ReportAgent",
        "message": "已更新 HTML 格式旅行报告。",
    })

    # Memory updates
    await coordinator.strategy_agent.remember_plan(request, plan.model_dump(mode="json"))
    coordinator.memory.set_short_term(
        request.user_id,
        {
            "destination": request.destination,
            "days": request.days,
            "spots": [spot.model_dump(mode="json") for spot in analysis_output.spots],
            "trip_id": plan.trip_id,
        },
    )
    coordinator.memory.record_trip(
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        trip_data={
            "trip_id": plan.trip_id,
            "destination": request.destination,
            "days": request.days,
            "kept_spots": [spot.name for spot in analysis_output.spots if spot.status in ("推荐", "用户确认")],
        },
    )

    total_trace = [
        AgentTrace(
            agent="TotalAgent",
            status="completed",
            message="已完成行程修改。",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
    ]
    trace = total_trace + analysis_trace + report_trace

    images_data = {
        "destination": image_output.destination,
        "observations": [
            {
                "image_url": obs.image_url,
                "labels": obs.labels,
                "scene_type": obs.scene_type,
                "inferred_location": obs.inferred_location,
                "description": obs.description,
            }
            for obs in image_output.observations
        ],
    }

    final = {
        "plan": plan.model_dump(mode="json"),
        "query": {
            "destination": previous_plan.destination,
            "sources": [],
            "extracted": {
                "destination": previous_plan.destination,
                "summary": plan.summary,
                "attractions": [slot.title for day in plan.itinerary for slot in day.slots if slot.category == "attraction"],
                "restaurants": [slot.title for day in plan.itinerary for slot in day.slots if slot.category == "food"],
                "activities": [],
                "route_suggestions": [],
                "tips": plan.packing_tips,
            },
        },
        "images": images_data,
        "trace": [t.model_dump(mode="json") for t in trace],
        "reports": [a.model_dump(mode="json") for a in report_output.artifacts],
    }
    yield _event("done", final)


# ===== Endpoints =====

@router.post("/trips/plan", response_model=TravelPlanResponse)
async def plan_trip(request: TripPlanRequest) -> TravelPlanResponse:
    """生成或修改旅行计划。"""
    return await coordinator.plan_trip(request)


@router.post("/trips/plan/stream")
async def stream_trip_plan(request: TripPlanRequest) -> StreamingResponse:
    """流式输出旅行规划过程。"""
    return StreamingResponse(
        _stream_plan(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/trips/modify/stream")
async def stream_modify_trip(request: TripPlanRequest) -> StreamingResponse:
    """流式输出行程修改过程。"""
    if not request.previous_plan:
        raise HTTPException(status_code=400, detail="需要提供 previous_plan。")
    return StreamingResponse(
        _stream_modify(request, request.previous_plan),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/query/research", response_model=QueryAgentOutput)
async def research_destination(request: QueryResearchRequest) -> QueryAgentOutput:
    """独立旅行研究接口。"""
    return await coordinator.research(request)


@router.post("/images/analyze", response_model=ImageAgentOutput)
async def analyze_images(request: ImageAnalysisRequest) -> ImageAgentOutput:
    """独立图片分析接口。"""
    return await coordinator.analyze_images(request)


@router.get("/users/{user_id}/preferences", response_model=PreferenceMemory)
async def get_preferences(user_id: str) -> PreferenceMemory:
    """获取用户长期偏好。"""
    return await coordinator.get_preferences(user_id)


@router.put("/users/{user_id}/preferences", response_model=PreferenceMemory)
async def update_preferences(user_id: str, request: PreferenceUpdateRequest) -> PreferenceMemory:
    """更新用户长期偏好。"""
    return await coordinator.update_preferences(user_id, request)


@router.get("/reports/{trip_id}.html")
async def get_html_report(trip_id: str) -> FileResponse:
    """根据 trip_id 获取 HTML 报告。"""
    return _report_response(trip_id, "html", "text/html")


@router.get("/reports/{trip_id}.pdf")
async def get_pdf_report(trip_id: str) -> FileResponse:
    """根据 trip_id 获取 PDF 报告（如果已导出）。"""
    return _report_response(trip_id, "pdf", "application/pdf")


@router.get("/reports/{trip_id}.json")
async def get_json_report(trip_id: str) -> FileResponse:
    """根据 trip_id 获取结构化 JSON 报告。"""
    return _report_response(trip_id, "json", "application/json")


def _report_response(trip_id: str, extension: str, media_type: str) -> FileResponse:
    if not trip_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="无效的 trip id。")
    report_dir = Path(settings.report_dir).resolve()
    path = (report_dir / f"{trip_id}.{extension}").resolve()
    if not str(path).startswith(str(report_dir)):
        raise HTTPException(status_code=400, detail="无效的报告路径。")
    if not path.exists():
        raise HTTPException(status_code=404, detail="报告不存在。")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename=\"{path.name}\""},
    )
