"""Multi-agent AiPy travel planner with Python and HTML codegen workers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from openai import OpenAI

from ..config import get_settings
from .prompt_templates import (
    AMAP_TOOL_FORMAT_FALLBACK,
    FIXED_OUTPUT_FILENAMES,
    HOTEL_RECOMMEND_PROMPT,
    ITINERARY_PLANNER_PROMPT,
    WEATHER_AGENT_PROMPT,
    ATTRACTION_SEARCH_PROMPT,
    ATTRACTION_REVIEW_PROMPT,
    HTML_CODEGEN_PROMPT,
    PYTHON_CODEGEN_PROMPT,
)


@dataclass
class AgentStep:
    """Single event in the agent process trace."""

    step_index: int
    event_type: str
    title: str
    assistant_text: str
    file_path: Optional[str] = None
    code: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    success: bool = True
    agent_name: Optional[str] = None


@dataclass
class AgentRunResult:
    """Final run result with process events."""

    final_answer: str
    steps: List[AgentStep]


@dataclass
class NormalizedTravelRequest:
    """Fields that downstream agents must preserve even for terse user inputs."""

    raw_input: str
    destination: str
    destination_provided: bool
    travel_days: int
    nights: int
    budget_level: str
    preferences: List[str]
    date_text: str
    date_policy: str
    defaults_applied: List[str]


class TravelCodegenAgent:
    """Travel planner powered by a multi-agent code generation loop."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_iterations: int = 18,
        run_timeout_seconds: int = 120,
    ):
        self._ensure_env_loaded()
        self.model = model or os.getenv("SILICONFLOW_MODEL") or os.getenv("LLM_MODEL_ID") or "Qwen/Qwen3.6-35B-A3B"
        self.api_key = api_key or os.getenv("SILICONFLOW_API_KEY") or os.getenv("LLM_API_KEY")
        self.base_url = self._normalize_url(
            base_url or os.getenv("SILICONFLOW_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.siliconflow.cn/v1"
        )
        self.timeout = timeout or int(os.getenv("LLM_TIMEOUT", "180"))
        self.max_iterations = max(max_iterations, 15)
        self.run_timeout_seconds = run_timeout_seconds

        if not self.api_key:
            raise ValueError("缺少API Key,请在backend/.env中设置SILICONFLOW_API_KEY或LLM_API_KEY")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        self.work_dir = Path(__file__).resolve().parents[2]
        self.blocks_dir = self.work_dir / "blocks"
        self.output_dir = self.blocks_dir / "output"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.attraction_search_agent = "attraction_search_agent"
        self.attraction_analysis_agent = "attraction_analysis_agent"
        self.hotel_recommend_agent = "hotel_recommend_agent"
        self.weather_agent = "weather_agent"
        self.itinerary_planner_agent = "itinerary_planner_agent"
        self.python_codegen_agent = "python_codegen_agent"
        self.html_codegen_agent = "html_codegen_agent"
        self.artifact_manifest_path = self.output_dir / "artifacts_manifest.json"
        self.artifact_files: List[str] = []
        self._tool_specs_text_cache: Optional[str] = None
        self._amap_call_contract = (
            "高德工具调用硬约束:\n"
            "1) 必须写: from app.services.amap_service import get_amap_service\n"
            "2) 必须写: amap = get_amap_service()\n"
            "3) 必须通过 services 封装调用: result = amap.call_tool_json(\"maps_text_search\", {\"keywords\": keywords, \"city\": city})\n"
            "4) 如需详情补全,必须通过: detail = amap.call_tool_json(\"maps_search_detail\", {\"id\": poi_id})\n"
            "5) 不允许使用 call_to_json、call_tool、mcp_tool.run 或自定义封装替代 call_tool_json。\n"
            "6) 每次调用后必须检查 result.get(\"ok\"),失败读取 result.get(\"error\")。"
        )

    def run(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> AgentRunResult:
        steps: List[AgentStep] = []
        final_answer = "达到最大迭代次数,任务未完成。请缩小任务范围后重试。"

        for event in self.run_stream(user_input=user_input, history=history):
            step = event.get("step")
            if step is not None:
                steps.append(step)
            if event.get("type") == "final":
                result = event.get("result")
                if isinstance(result, AgentRunResult):
                    final_answer = result.final_answer

        return AgentRunResult(final_answer=final_answer, steps=steps)

    def run_stream(
        self,
        user_input: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Generator[Dict[str, object], None, None]:
        _ = history

        steps: List[AgentStep] = []
        normalized_request = self._normalize_travel_request(user_input)
        request_context = self._format_request_context(normalized_request)
        planning_input = self._build_planning_input(user_input, request_context)
        request_step = AgentStep(
            step_index=0,
            event_type="request_context",
            title="已补全旅行需求字段",
            assistant_text=request_context,
            success=True,
            agent_name="coordinator",
        )
        steps.append(request_step)
        yield {"type": "step", "step": request_step}

        state = "attraction_create"
        attraction_script = "blocks/generated_attractions.py"
        attraction_review_script = "blocks/generated_attractions_review.py"
        hotel_script = "blocks/generated_hotels.py"
        weather_script = "blocks/generated_weather.py"
        itinerary_script = "blocks/generated_itinerary.py"
        python_script = "blocks/generated_collect_data.py"
        html_script = "blocks/generated_render_html.py"
        fix_context = ""

        for idx in range(1, self.max_iterations + 1):
            if idx == 1:
                self._archive_disallowed_output_files()
            artifact_manifest = self._refresh_artifact_manifest()

            if state == "attraction_create":
                prompt = self._build_attraction_codegen_prompt(
                    user_input=planning_input,
                    target_script=attraction_script,
                    artifact_manifest=artifact_manifest,
                    fix_context=fix_context,
                )
                content = self._chat(ATTRACTION_SEARCH_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点搜索Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.attraction_search_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_attractions.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点搜索Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.attraction_search_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="attraction")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点搜索Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.attraction_search_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = (
                        f"代码约束失败: {code_msg}\n"
                        "行程脚本必须以写出 blocks/output/itinerary_plan.json 为目标。"
                        "不要因为景点/酒店名称与输入文件不完全一致就 raise 或 sys.exit,这类问题应写入 data_warnings。"
                    )
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"景点搜索Agent制造 python 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.attraction_search_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    attraction_script = expected_path
                    state = "attraction_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "attraction_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {attraction_script}",
                    file_path=attraction_script,
                    code=self._read_code_file(attraction_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(attraction_script)
                artifact_manifest = self._refresh_artifact_manifest()
                stage_ok, stage_msg = self._validate_records_artifact(
                    artifact_manifest=artifact_manifest,
                    rel_path="blocks/output/attractions.json",
                    display_name="attractions.json",
                )
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {attraction_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {attraction_script}",
                    file_path=attraction_script,
                    code=self._read_code_file(attraction_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok and stage_ok and whitelist_ok:
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=self._build_stage_thought(
                            stage="attraction",
                            run_output=out or "",
                            next_action="我会让景点结果分析Agent复审已落盘数据,合格则直接通过,不合格则只做去重、过滤和裁剪。",
                        ),
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}
                    state = "attraction_review_create"
                    fix_context = ""
                elif ok:
                    state = "attraction_create"
                    fix_context = (
                        f"景点数据校验失败: {stage_msg}\n"
                        f"白名单校验: {whitelist_msg}\n"
                        "请修复脚本并重新生成。"
                    )
                else:
                    state = "attraction_create"
                    fix_context = f"脚本执行失败,请修复后重试。错误:\n{err or ''}"
                continue

            if state == "attraction_review_create":
                prompt = self._build_attraction_review_prompt(
                    user_input=planning_input,
                    target_script=attraction_review_script,
                    artifact_manifest=artifact_manifest,
                    fix_context=fix_context,
                )
                content = self._chat(self._build_attraction_review_system_prompt(), prompt)
                review_status = self._extract_review_status(content)

                if review_status == "PASS":
                    artifact_manifest = self._refresh_artifact_manifest()
                    stage_ok, stage_msg = self._validate_records_artifact(
                        artifact_manifest=artifact_manifest,
                        rel_path="blocks/output/attractions.json",
                        display_name="attractions.json",
                    )
                    whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)
                    ok = stage_ok and whitelist_ok
                    step = AgentStep(
                        step_index=idx,
                        event_type="review",
                        title="景点结果分析Agent 复查通过" if ok else "景点结果分析Agent 复查未通过",
                        assistant_text=content,
                        output=stage_msg if ok else f"{stage_msg}\n白名单校验: {whitelist_msg}",
                        error=None if ok else f"{stage_msg}\n白名单校验: {whitelist_msg}",
                        success=ok,
                        agent_name=self.attraction_analysis_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}

                    if ok:
                        thought = AgentStep(
                            step_index=idx,
                            event_type="thought",
                            title="已完成思考",
                            assistant_text=(
                                "老板,景点结果复查通过,无需生成清洗脚本。"
                                "接下来,我会让酒店推荐Agent基于复审后的景点数据创建并运行酒店检索脚本。"
                            ),
                            output=stage_msg,
                            success=True,
                            agent_name="coordinator",
                        )
                        steps.append(thought)
                        yield {"type": "step", "step": thought}
                        state = "hotel_create"
                        fix_context = ""
                    else:
                        fix_context = (
                            f"你输出了 REVIEW_STATUS: PASS,但系统校验未通过:\n{stage_msg}\n"
                            f"白名单校验: {whitelist_msg}\n"
                            "请输出 REVIEW_STATUS: NEEDS_FIX,并生成只读写 attractions.json 的清洗脚本。"
                        )
                    continue

                if review_status is None:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点结果分析Agent 缺少复查标识",
                        assistant_text=content,
                        error="缺少 REVIEW_STATUS: PASS 或 REVIEW_STATUS: NEEDS_FIX",
                        success=False,
                        agent_name=self.attraction_analysis_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "请先输出 REVIEW_STATUS: PASS 或 REVIEW_STATUS: NEEDS_FIX; 只有 NEEDS_FIX 时才生成 python 清洗脚本。"
                    continue

                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点结果分析Agent 输出不符合协议",
                        assistant_text=content,
                        error="必须输出 REVIEW_STATUS: PASS,或输出 REVIEW_STATUS: NEEDS_FIX + ACTION_CREATE_FILE + python 代码块",
                        success=False,
                        agent_name=self.attraction_analysis_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = (
                        "如果 attractions.json 已合格,请返回 REVIEW_STATUS: PASS,不要生成代码。"
                        "如果需要清洗/裁剪,请返回 REVIEW_STATUS: NEEDS_FIX + ACTION_CREATE_FILE + ```python```。"
                    )
                    continue

                expected_path = "blocks/generated_attractions_review.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点结果分析Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.attraction_analysis_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="attraction_review")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="景点结果分析Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.attraction_analysis_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = (
                        f"代码约束失败: {code_msg}\n"
                        "景点复审脚本要按实际情况选择: 只删除/裁剪时不要调用 MCP; 缺少必须景点时允许用 "
                        "get_amap_service().call_tool_json(...) 少量补充,每个缺失方向只保留1个详情。"
                    )
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"景点结果分析Agent制造 python 回填工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.attraction_analysis_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    attraction_review_script = expected_path
                    state = "attraction_review_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "attraction_review_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {attraction_review_script}",
                    file_path=attraction_review_script,
                    code=self._read_code_file(attraction_review_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(attraction_review_script)
                artifact_manifest = self._refresh_artifact_manifest()
                stage_ok, stage_msg = self._validate_attractions_artifact(artifact_manifest, user_input)
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {attraction_review_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {attraction_review_script}",
                    file_path=attraction_review_script,
                    code=self._read_code_file(attraction_review_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok and stage_ok and whitelist_ok:
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=self._build_stage_thought(
                            stage="attraction_review",
                            run_output=out or "",
                            next_action="我会让酒店推荐Agent基于复审后的景点数据创建并运行酒店检索脚本。",
                        ),
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}
                    state = "hotel_create"
                    fix_context = ""
                elif ok:
                    state = "attraction_review_create"
                    fix_context = (
                        f"景点复审数据校验失败: {stage_msg}\n"
                        f"白名单校验: {whitelist_msg}\n"
                        "请只在分析阶段重新裁剪 attractions.json,不要回退到景点搜索,不要重新调用高德接口。"
                    )
                else:
                    state = "attraction_review_create"
                    fix_context = f"脚本执行失败,请修复后重试。错误:\n{err or ''}"
                continue

            if state == "hotel_create":
                prompt = self._build_hotel_codegen_prompt(
                    user_input=planning_input,
                    target_script=hotel_script,
                    artifact_manifest=artifact_manifest,
                    fix_context=fix_context,
                )
                content = self._chat(HOTEL_RECOMMEND_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="酒店推荐Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.hotel_recommend_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_hotels.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="酒店推荐Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.hotel_recommend_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="hotel")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="酒店推荐Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.hotel_recommend_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"代码约束失败: {code_msg}\n{self._amap_call_contract}"
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"酒店推荐Agent制造 python 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.hotel_recommend_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    hotel_script = expected_path
                    state = "hotel_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "hotel_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {hotel_script}",
                    file_path=hotel_script,
                    code=self._read_code_file(hotel_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(hotel_script)
                artifact_manifest = self._refresh_artifact_manifest()
                stage_ok, stage_msg = self._validate_records_artifact(
                    artifact_manifest=artifact_manifest,
                    rel_path="blocks/output/hotels.json",
                    display_name="hotels.json",
                )
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {hotel_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {hotel_script}",
                    file_path=hotel_script,
                    code=self._read_code_file(hotel_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok and stage_ok and whitelist_ok:
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=self._build_stage_thought(
                            stage="hotel",
                            run_output=out or "",
                            next_action="我会让天气查询Agent生成并运行天气脚本,补齐实时天气数据。",
                        ),
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}
                    state = "weather_create"
                    fix_context = ""
                elif ok:
                    state = "hotel_create"
                    fix_context = (
                        f"酒店数据校验失败: {stage_msg}\n"
                        f"白名单校验: {whitelist_msg}\n"
                        "请修复脚本并重新生成。"
                    )
                else:
                    state = "hotel_create"
                    fix_context = f"脚本执行失败,请修复后重试。错误:\n{err or ''}"
                continue

            if state == "weather_create":
                prompt = self._build_weather_codegen_prompt(
                    user_input=planning_input,
                    target_script=weather_script,
                    artifact_manifest=artifact_manifest,
                    fix_context=fix_context,
                )
                content = self._chat(WEATHER_AGENT_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="天气查询Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.weather_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_weather.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="天气查询Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.weather_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="weather")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="天气查询Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.weather_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"代码约束失败: {code_msg}\n{self._amap_call_contract}"
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"天气查询Agent制造 python 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.weather_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    weather_script = expected_path
                    state = "weather_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "weather_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {weather_script}",
                    file_path=weather_script,
                    code=self._read_code_file(weather_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(weather_script)
                artifact_manifest = self._refresh_artifact_manifest()
                stage_ok, stage_msg = self._validate_weather_artifact(artifact_manifest)
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {weather_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {weather_script}",
                    file_path=weather_script,
                    code=self._read_code_file(weather_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok and stage_ok and whitelist_ok:
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=self._build_stage_thought(
                            stage="weather",
                            run_output=out or "",
                            next_action="我会让行程规划Agent基于景点、酒店、天气结果生成并运行行程脚本。",
                        ),
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}
                    state = "itinerary_create"
                    fix_context = ""
                elif ok:
                    state = "weather_create"
                    fix_context = (
                        f"天气数据校验失败: {stage_msg}\n"
                        f"白名单校验: {whitelist_msg}\n"
                        "请修复脚本并重新生成。"
                    )
                else:
                    state = "weather_create"
                    fix_context = f"脚本执行失败,请修复后重试。错误:\n{err or ''}"
                continue

            if state == "itinerary_create":
                prompt = self._build_itinerary_codegen_prompt(
                    user_input=planning_input,
                    target_script=itinerary_script,
                    artifact_manifest=artifact_manifest,
                    fix_context=fix_context,
                )
                content = self._chat(ITINERARY_PLANNER_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="行程规划Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.itinerary_planner_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_itinerary.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="行程规划Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.itinerary_planner_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="itinerary")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="行程规划Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.itinerary_planner_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"代码约束失败: {code_msg}\n{self._amap_call_contract}"
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"行程规划Agent制造 python 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.itinerary_planner_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    itinerary_script = expected_path
                    state = "itinerary_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "itinerary_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {itinerary_script}",
                    file_path=itinerary_script,
                    code=self._read_code_file(itinerary_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(itinerary_script)
                artifact_manifest = self._refresh_artifact_manifest()
                stage_ok, stage_msg = self._validate_itinerary_artifact_basic(artifact_manifest)
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {itinerary_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {itinerary_script}",
                    file_path=itinerary_script,
                    code=self._read_code_file(itinerary_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok and stage_ok and whitelist_ok:
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=self._build_stage_thought(
                            stage="itinerary",
                            run_output=out or "",
                            next_action="我会直接交给HTML Agent生成可视化页面。",
                        ),
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}
                    state = "html_create"
                    fix_context = ""
                elif ok:
                    state = "itinerary_create"
                    fix_context = (
                        f"行程数据校验失败: {stage_msg}\n"
                        f"白名单校验: {whitelist_msg}\n"
                        "请修复脚本并重新生成。"
                    )
                else:
                    state = "itinerary_create"
                    fix_context = f"脚本执行失败,请修复后重试。错误:\n{err or ''}"
                continue

            if state == "python_create":
                prompt = self._build_python_prompt(
                    user_input=planning_input,
                    target_script=python_script,
                    fix_context=fix_context,
                    artifact_manifest=artifact_manifest,
                )
                content = self._chat(PYTHON_CODEGEN_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="Python Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.python_codegen_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "你上一次输出不符合协议,请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_collect_data.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="Python Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.python_codegen_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                code_ok, code_msg = self._validate_generated_code_integrity(code, stage="python")
                if not code_ok:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="Python Agent 代码不符合约束",
                        assistant_text=content,
                        error=code_msg,
                        success=False,
                        agent_name=self.python_codegen_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"代码约束失败: {code_msg}\n{self._amap_call_contract}"
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"制造 python 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.python_codegen_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    python_script = expected_path
                    state = "python_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "python_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {python_script}",
                    file_path=python_script,
                    code=self._read_code_file(python_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(python_script)
                self._relocate_stray_html_outputs()
                artifact_manifest = self._refresh_artifact_manifest()
                data_ok, data_msg = self._validate_python_artifacts(artifact_manifest)
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {python_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {python_script}",
                    file_path=python_script,
                    code=self._read_code_file(python_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    thought_text = self._build_stage_thought(
                        stage="python",
                        run_output=out or "",
                        next_action="我会基于落盘数据继续制造 HTML 工具并渲染结果。",
                    )
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=thought_text,
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}

                if ok and self._has_non_html_data_artifacts(artifact_manifest) and data_ok and whitelist_ok:
                    state = "html_create"
                    fix_context = ""
                elif ok:
                    state = "python_create"
                    if not self._has_non_html_data_artifacts(artifact_manifest):
                        fix_context = "脚本运行成功但没有产出json/csv数据文件,请补齐数据落盘。"
                    else:
                        fix_context = (
                            "数据质量校验失败:\n"
                            f"{data_msg}\n"
                            f"白名单校验: {whitelist_msg}\n"
                            "请修复并重新生成Python脚本。酒店检索请使用 maps_text_search,并确保 hotels.json 非空。"
                        )
                else:
                    state = "python_create"
                    fix_context = f"脚本执行失败,请修复报错后重新生成。错误信息:\n{err or ''}"
                continue

            if state == "html_create":
                prompt = self._build_html_prompt(
                    user_input=planning_input,
                    target_script=html_script,
                    fix_context=fix_context,
                    artifact_manifest=artifact_manifest,
                )
                content = self._chat(HTML_CODEGEN_PROMPT, prompt)
                create_path = self._extract_action_path(content, "ACTION_CREATE_FILE")
                code = self._extract_python_code(content)

                if not create_path or not code:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="HTML Agent 输出不符合协议",
                        assistant_text=content,
                        error="缺少 ACTION_CREATE_FILE 或 python 代码块",
                        success=False,
                        agent_name=self.html_codegen_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = "你上一次输出不符合协议,请严格返回 ACTION_CREATE_FILE + ```python```。"
                    continue

                expected_path = "blocks/generated_render_html.py"
                if create_path.replace("\\", "/").strip() != expected_path:
                    step = AgentStep(
                        step_index=idx,
                        event_type="protocol",
                        title="HTML Agent 文件名不符合硬约束",
                        assistant_text=content,
                        error=f"脚本文件名必须固定为 {expected_path}",
                        success=False,
                        agent_name=self.html_codegen_agent,
                    )
                    steps.append(step)
                    yield {"type": "step", "step": step}
                    fix_context = f"文件名必须固定为 {expected_path},请重试。"
                    continue

                ok, msg = self._save_code_file(create_path, code)
                step = AgentStep(
                    step_index=idx,
                    event_type="create",
                    title=f"制造 html 工具 (已编写{len(code)}字符)",
                    assistant_text=content,
                    file_path=create_path,
                    code=code,
                    output=msg if ok else None,
                    error=None if ok else msg,
                    success=ok,
                    agent_name=self.html_codegen_agent,
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    html_script = expected_path
                    state = "html_run"
                    fix_context = ""
                else:
                    fix_context = f"创建文件失败: {msg}"
                continue

            if state == "html_run":
                run_start_step = AgentStep(
                    step_index=idx,
                    event_type="run_start",
                    title="运行代码",
                    assistant_text=f"ACTION_RUN_FILE: {html_script}",
                    file_path=html_script,
                    code=self._read_code_file(html_script),
                    output="执行中...",
                    success=True,
                    agent_name="runner",
                )
                steps.append(run_start_step)
                yield {"type": "step", "step": run_start_step}

                ok, out, err = self._run_python_file(html_script)
                self._relocate_stray_html_outputs()
                artifact_manifest = self._refresh_artifact_manifest()
                html_ok, html_msg = self._validate_html_artifacts(artifact_manifest)
                whitelist_ok, whitelist_msg = self._validate_output_whitelist(artifact_manifest)

                step = AgentStep(
                    step_index=idx,
                    event_type="run",
                    title=f"运行 {html_script} {'成功' if ok else '失败'}",
                    assistant_text=f"ACTION_RUN_FILE: {html_script}",
                    file_path=html_script,
                    code=self._read_code_file(html_script),
                    output=out,
                    error=err,
                    success=ok,
                    agent_name="runner",
                )
                steps.append(step)
                yield {"type": "step", "step": step}

                if ok:
                    thought_text = self._build_stage_thought(
                        stage="html",
                        run_output=out or "",
                        next_action="我会检查 HTML 与图片元数据产物,通过后结束任务并汇总路径。",
                    )
                    thought = AgentStep(
                        step_index=idx,
                        event_type="thought",
                        title="已完成思考",
                        assistant_text=thought_text,
                        output=out,
                        success=True,
                        agent_name="coordinator",
                    )
                    steps.append(thought)
                    yield {"type": "step", "step": thought}

                if ok and self._discover_html_outputs() and html_ok and whitelist_ok:
                    state = "final"
                elif ok:
                    state = "html_create"
                    if not self._discover_html_outputs():
                        fix_context = "脚本运行成功但未在 blocks/output 产出html,请修复。"
                    else:
                        fix_context = (
                            "HTML产物校验失败:\n"
                            f"{html_msg}\n"
                            f"白名单校验: {whitelist_msg}\n"
                            "请重点修复 UTF-8 编码、JSON解析范围(.json only)和数据结构标准化。"
                        )
                else:
                    state = "html_create"
                    fix_context = (
                        f"脚本执行失败,请修复报错后重新生成。错误信息:\n{err or ''}\n"
                        f"建议修复方向:\n{self._analyze_common_runtime_error(err or '')}"
                    )
                continue

            if state == "final":
                final_answer = self._build_final_answer(user_input)
                final_step = AgentStep(
                    step_index=idx,
                    event_type="final",
                    title="完成任务",
                    assistant_text="FINAL_ANSWER",
                    output=final_answer,
                    success=True,
                    agent_name="coordinator",
                )
                steps.append(final_step)
                yield {"type": "step", "step": final_step}
                yield {
                    "type": "final",
                    "result": AgentRunResult(final_answer=final_answer, steps=steps),
                }
                return

        fallback = AgentRunResult(
            final_answer="达到最大迭代次数,任务未完成。请缩小任务范围后重试。",
            steps=steps,
        )
        yield {"type": "final", "result": fallback}

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""

    def _normalize_travel_request(self, user_input: str) -> NormalizedTravelRequest:
        raw = (user_input or "").strip()
        days, _, _ = self._estimate_attraction_count_bounds(raw)
        days_provided = bool(re.search(r"(\d{1,2}|[一二两俩三四五六七八九十]{1,3})\s*(?:天|日)", raw))
        destination = self._extract_destination(raw)
        destination_provided = destination not in {"", "待确认目的地"}
        budget_level = self._extract_budget_level(raw)
        preferences = self._extract_preferences(raw)
        date_text = self._extract_date_text(raw)

        defaults_applied: List[str] = []
        if not days_provided:
            defaults_applied.append("未提供旅行天数,默认 3 天 2 晚")
        if not destination_provided:
            defaults_applied.append("未明确目的地,要求模型只能从原始输入判断,不可凭空编造")
        if budget_level == "中等预算":
            defaults_applied.append("未提供预算,默认中等预算")
        if preferences == ["经典景点", "本地美食", "路线顺畅"]:
            defaults_applied.append("未提供偏好,默认经典景点、本地美食和路线顺畅")
        if date_text == "未提供":
            defaults_applied.append("未提供出行日期,天气仅作近期参考")

        return NormalizedTravelRequest(
            raw_input=raw,
            destination=destination or "待确认目的地",
            destination_provided=destination_provided,
            travel_days=days,
            nights=max(0, days - 1),
            budget_level=budget_level,
            preferences=preferences,
            date_text=date_text,
            date_policy="未提供具体日期时,使用高德当前/近期天气作为参考,不要虚构精确日期",
            defaults_applied=defaults_applied,
        )

    def _format_request_context(self, request: NormalizedTravelRequest) -> str:
        payload = {
            "raw_input": request.raw_input,
            "destination": request.destination,
            "destination_provided": request.destination_provided,
            "travel_days": request.travel_days,
            "nights": request.nights,
            "budget_level": request.budget_level,
            "preferences": request.preferences,
            "date_text": request.date_text,
            "date_policy": request.date_policy,
            "defaults_applied": request.defaults_applied,
            "required_itinerary_fields": {
                "top_level": [
                    "request_summary",
                    "destination",
                    "travel_days",
                    "nights",
                    "budget_level",
                    "preferences",
                    "date_text",
                    "date_policy",
                    "daily_plans",
                    "overall_suggestions",
                    "data_warnings",
                ],
                "request_summary": [
                    "destination",
                    "travel_days",
                    "nights",
                    "budget_level",
                    "preferences",
                    "date_text",
                    "date_policy",
                    "defaults_applied",
                ],
                "daily_plan_item": [
                    "day",
                    "title",
                    "theme",
                    "attractions",
                    "hotel",
                    "meals",
                    "weather",
                    "transportation",
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_planning_input(user_input: str, request_context: str) -> str:
        return (
            f"原始用户输入:\n{user_input}\n\n"
            "结构化旅行需求(后续所有Agent必须以此为准,如果原始输入很简单也必须回填这些字段):\n"
            f"{request_context}\n\n"
            "关键约束:\n"
            "1) 模型可以先分析和补全,但 itinerary_plan.json 必须写入 request_summary,保留上述字段。\n"
            "2) 若用户只提供城市,按默认 3 天 2 晚、中等预算、经典景点+本地美食+路线顺畅执行。\n"
            "3) 若某字段来自默认值,必须写入 request_summary.defaults_applied 或 data_warnings,不要假装用户明确提供。\n"
            "4) 每日行程数量必须与 travel_days 一致,每天必须有景点、住宿、餐食、天气和交通摘要。\n"
        )

    @staticmethod
    def _extract_destination(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return "待确认目的地"

        patterns = [
            r"(?:去|到|在)([\u4e00-\u9fa5A-Za-z]{2,20}?)(?:旅行|旅游|游玩|玩|出行|自由行|攻略|行程|$)",
            r"(?:规划|安排|制定|做)(?:一个|一下)?([\u4e00-\u9fa5A-Za-z]{2,20}?)(?:\d|[一二两俩三四五六七八九十]|旅行|旅游|游玩|玩|出行|自由行|攻略|行程|$)",
            r"([\u4e00-\u9fa5]{2,12}(?:市|省|州|县|区|岛))(?:旅行|旅游|游玩|玩|出行|自由行|攻略|行程|$)?",
        ]
        stop_words = ["帮我", "我想", "想去", "计划", "规划", "一个", "一下", "的", "旅行", "旅游", "游玩", "行程", "攻略"]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if not match:
                continue
            value = match.group(1).strip()
            for word in stop_words:
                value = value.replace(word, "")
            value = re.sub(r"\d.*$", "", value).strip()
            if len(value) >= 2:
                return value

        compact = re.sub(r"[，。！？,.!?；;：:\s]", "", raw)
        simple_match = re.search(r"([\u4e00-\u9fa5]{2,8})(?:旅行|旅游|游玩|玩)$", compact)
        if simple_match:
            return simple_match.group(1)

        return "待确认目的地"

    @staticmethod
    def _extract_budget_level(text: str) -> str:
        raw = text or ""
        amount_match = re.search(r"(\d{3,6})\s*(?:元|块|人民币|rmb|RMB)", raw)
        if amount_match:
            return f"{amount_match.group(1)}元预算"
        if re.search(r"高端|豪华|奢华|五星|不差钱", raw):
            return "高预算"
        if re.search(r"穷游|经济|省钱|便宜|低预算|学生", raw):
            return "经济预算"
        return "中等预算"

    @staticmethod
    def _extract_preferences(text: str) -> List[str]:
        raw = text or ""
        candidates = [
            ("美食", r"美食|吃|小吃|餐厅"),
            ("历史文化", r"历史|文化|古迹|博物馆|人文"),
            ("自然风光", r"自然|山水|风景|公园|徒步"),
            ("亲子友好", r"亲子|孩子|儿童|家庭"),
            ("购物娱乐", r"购物|商场|夜生活|娱乐"),
            ("小众深度", r"小众|深度|避开人群|不网红"),
            ("轻松慢游", r"轻松|休闲|慢游|不赶"),
            ("摄影打卡", r"摄影|拍照|打卡|出片"),
        ]
        found = [label for label, pattern in candidates if re.search(pattern, raw)]
        return found or ["经典景点", "本地美食", "路线顺畅"]

    @staticmethod
    def _extract_date_text(text: str) -> str:
        raw = text or ""
        patterns = [
            r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
            r"\d{1,2}月\d{1,2}日",
            r"明天|后天|周末|五一|国庆|春节|暑假|寒假|元旦|清明|端午|中秋",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if match:
                return match.group(0)
        return "未提供"

    def _build_python_prompt(
        self,
        user_input: str,
        target_script: str,
        fix_context: str,
        artifact_manifest: Dict[str, object],
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(必须严格遵循):\n{tool_specs}\n\n"
            f"{self._amap_call_contract}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            "实现要求:\n"
            "1) 产出数据汇总/补全脚本,读取已有产物并增量写入新的结构化结果;\n"
            "2) 实时调用高德MCP工具获取真实数据;\n"
            "3) 所有结果落盘到 blocks/output;\n"
            "4) 更新 artifacts_manifest.json;\n"
            "5) 不要覆盖已生成的关键文件(attractions/hotels/itinerary),优先做补全和校验;\n"
            "6) itinerary_plan.json 已由行程规划Agent基于三份输入数据完成模型思考后回填,不要重新用Python算法生成行程;\n"
            "7) 正常流程会在行程产物成功后直接进入HTML阶段,只有明确被要求补全数据时才运行本Agent。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _build_html_prompt(
        self,
        user_input: str,
        target_script: str,
        fix_context: str,
        artifact_manifest: Dict[str, object],
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        data_snapshot = self._build_artifact_snapshot(
            [
                "blocks/output/itinerary_plan.json",
                "blocks/output/attractions.json",
                "blocks/output/hotels.json",
                "blocks/output/weather.json",
                "blocks/output/unsplash_images.json",
            ],
            max_chars_per_file=14000,
        )
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(若用到地图查询必须遵循):\n{tool_specs}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            f"当前真实输入数据快照(必须按这些结构读取,不要猜字段):\n{data_snapshot}\n\n"
            "重要输入结构说明:\n"
            "1) blocks/output/itinerary_plan.json 通常是 dict,主行程在 payload['days'] 或 payload['daily_plans']; 不要把根对象当 list。\n"
            "2) payload['request_summary'] 是需求补全结果,必须在页面顶部展示 destination/travel_days/nights/budget_level/preferences/date_text/defaults_applied。\n"
            "3) day['attractions'] 是景点对象列表,不是景点 id 列表; 直接读取 attraction['name']/['address']/['location']/['description']/['ticket_price']。\n"
            "4) day['hotel'] 是当天住宿对象; 顶层 hotels.json 只是补充数据,不要只渲染 hotels.json 而忽略 daily plan。\n"
            "5) day['meals'] 是餐食对象列表,字段通常是 type/name/description/estimated_cost。\n"
            "6) 天气可能在顶层 payload['weather_info'],也可能在 day['weather']; 顶层 weather_info 应按日期匹配到每天展示。\n"
            "7) UnsplashService.search_photos 返回 list[dict],字段是 url/thumb/description/photographer,不是 urls.regular。\n"
            "8) location 字段常见格式是 '121.475497,31.228234' 字符串,可解析为高德地图 marker。\n\n"
            "实现要求:\n"
            "1) 读取 artifacts_manifest.json 和其中的数据文件;\n"
            "2) 必须优先读取 blocks/output/itinerary_plan.json 作为页面主数据,并渲染每一天的 description/transportation/hotel/attractions/meals/weather;\n"
            "3) 页面必须展示 request_summary,让用户知道哪些字段是默认补全的;\n"
            "4) 使用Unsplash API按城市和每天主要景点关键词搜索图片,保存到 blocks/output/unsplash_images.json,并在HTML中使用 photo['url'] 或 photo['thumb'] 插入真实图片;\n"
            "5) 生成 HTML 到 blocks/output/itinerary.html;\n"
            "6) HTML 必须包含高德地图展示模块: 读取 app.config.get_settings().amap_api_key,有 key 时加载 https://webapi.amap.com/maps?v=2.0&key=... 并为景点坐标打 marker; 没有 key 时展示坐标列表和提示,不要让页面空白;\n"
            "7) HTML 内容必须引用真实落盘数据,不要硬编码不存在的 day['title']、day['restaurants']、attraction['image_url']、hotel['stars'] 等字段;\n"
            "8) 生成前要写健壮的 normalize_itinerary/normalize_weather/get_photo_url/parse_location 辅助函数,兼容 dict/list 结构。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _build_attraction_codegen_prompt(
        self,
        user_input: str,
        target_script: str,
        artifact_manifest: Dict[str, object],
        fix_context: str,
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(必须严格遵循):\n{tool_specs}\n\n"
            f"{self._amap_call_contract}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            "实现要求:\n"
            "1) 生成景点检索脚本并写入 blocks/*.py;\n"
            "2) 实时调用 maps_text_search 获取景点数据;\n"
            "3) 仅输出景点/地标/文化场馆数据,不要输出美食/餐厅/小吃类POI;\n"
            "4) 用户提到的吃什么需求留到 itinerary 阶段处理;\n"
            "5) 写出 blocks/output/attractions.json;\n"
            "6) 输出简要计数和文件路径。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    @staticmethod
    def _build_attraction_review_system_prompt() -> str:
        return ATTRACTION_REVIEW_PROMPT

    def _build_attraction_review_prompt(
        self,
        user_input: str,
        target_script: str,
        artifact_manifest: Dict[str, object],
        fix_context: str,
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        attractions_snapshot = self._build_artifact_snapshot(
            ["blocks/output/attractions.json"],
            max_chars_per_file=16000,
        )
        days, min_count, max_count = self._estimate_attraction_count_bounds(user_input)
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(只有缺少必须景点时才使用):\n{tool_specs}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            f"当前景点查询结果快照(模型必须先分析这份结果):\n{attractions_snapshot}\n\n"
            "景点数量约束:\n"
            f"- 已从用户需求估算旅行天数为 {days} 天。\n"
            f"- 复审后的 attractions.json 建议控制在 {min_count}-{max_count} 个之间,上限为 {max_count} 个。\n"
            "- 如果原始景点超过上限,必须按城市代表性、用户偏好相关性、评分/热度和路线分布进行裁剪。\n"
            "- 如果缺少城市必游/代表性景点或用户明确偏好必须覆盖的景点,允许在 NEEDS_FIX 脚本中少量调用 MCP 补充。\n"
            "- 补充时每个缺失方向/关键词只保留 1 个最合适 POI,并只对这个 POI 调一次 maps_search_detail 获取详情。\n"
            "- 达到数量上限后停止补充; 如果补充后超过上限,必须裁掉优先级最低的已有景点,保证最终不超过上限。\n\n"
            "实现要求:\n"
            "1) 先在 THOUGHT 中说明你对快照的判断;\n"
            "2) 如果已有 attractions.json 没有重复、无关POI、缺失 name/location,数量不超过上限,且不缺少必须景点,输出 REVIEW_STATUS: PASS,不要生成代码;\n"
            "3) 只有需要删除/裁剪/修复字段/补充必须景点时,才输出 REVIEW_STATUS: NEEDS_FIX 并生成脚本;\n"
            "4) 如果只是删除、去重、裁剪,输出 REVIEW_ACTION: CLEAN_ONLY,脚本不要导入 get_amap_service,不要调用 call_tool_json;\n"
            "5) 如果缺少必须景点,输出 REVIEW_ACTION: SUPPLEMENT,脚本必须通过 from app.services.amap_service import get_amap_service; amap = get_amap_service(); amap.call_tool_json(...) 补充真实详情;\n"
            "6) SUPPLEMENT 只允许使用 maps_text_search 和 maps_search_detail,每个缺失关键词只补 1 个景点;\n"
            "7) 删除餐饮/住宿/公司/交通站点等非景点POI;\n"
            f"8) 覆盖写回后保持非空数组,最终数量不得超过 {max_count} 个;\n"
            "9) 脚本输出清洗前数量、删除数量、裁剪数量、补充数量、最终数量和文件路径。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _build_hotel_codegen_prompt(
        self,
        user_input: str,
        target_script: str,
        artifact_manifest: Dict[str, object],
        fix_context: str,
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(必须严格遵循):\n{tool_specs}\n\n"
            f"{self._amap_call_contract}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            "实现要求:\n"
            "1) 读取 blocks/output/attractions.json 作为上下文(如果存在);\n"
            "2) 实时调用 maps_text_search 或 maps_around_search 获取酒店数据;\n"
            "3) 写出 blocks/output/hotels.json;\n"
            "4) 输出简要计数和文件路径。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _build_itinerary_codegen_prompt(
        self,
        user_input: str,
        target_script: str,
        artifact_manifest: Dict[str, object],
        fix_context: str,
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        data_snapshot = self._build_artifact_snapshot(
            [
                "blocks/output/attractions.json",
                "blocks/output/hotels.json",
                "blocks/output/weather.json",
            ],
            max_chars_per_file=12000,
        )
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(仅兜底查询时使用):\n{tool_specs}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            f"三份输入数据快照(你必须先基于这些数据完成行程思考):\n{data_snapshot}\n\n"
            "实现要求:\n"
            "1) 必须读取 blocks/output/attractions.json、blocks/output/hotels.json、blocks/output/weather.json;\n"
            "2) 模型先根据上面的三份快照和结构化旅行需求决定每日景点、住宿、天气策略、交通摘要和餐食建议;\n"
            "3) Python 脚本中显式写入这个已经决定好的 itinerary_plan 字典,运行时只做读取校验和 JSON 落盘;\n"
            "4) 不要让 Python 再用循环均分、评分排序、随机选择等方式生成最终答案;\n"
            "5) 每天住宿必须来自 hotels.json,每天景点必须来自复审后的 attractions.json;\n"
            "6) 必须使用 weather.json 的天气结果调整每日行程强度与室内外比例;\n"
            "7) transportation 字段写入模型基于景点位置关系做出的路线摘要,不要单独落盘路线文件;\n"
            "8) meals 字段写入早/中/晚餐或等价建议; 用户提到的吃什么需求在本阶段完成;\n"
            "9) 写出 blocks/output/itinerary_plan.json;\n"
            "10) itinerary_plan.json 必须包含 daily_plans 或 days 或 travel_days,且每一天都要有 景点 + 住宿 + 餐食;\n"
            "11) itinerary_plan 顶层必须包含 request_summary 对象,且至少包含 destination/travel_days/nights/budget_level/preferences/date_text/date_policy/defaults_applied;\n"
            "12) 顶层必须同步写入 destination/travel_days/nights/budget_level/preferences/date_text/date_policy,供 HTML Agent 直接读取;\n"
            "13) daily_plans 的条数必须等于 request_summary.travel_days; 如果数据不足,用 data_warnings 解释,但不要少天数;\n"
            "14) 天气信息可以放在每一天内部,也可以统一放在顶层 weather_info,不要为了字段重复而重新生成行程。\n\n"
            "运行稳定性要求:\n"
            "1) 禁止在脚本运行时做“景点/酒店名称必须精确存在于输入文件”的阻断式校验;\n"
            "2) 禁止出现 Attraction not found in input / Hotel not found in input 这类 raise ValueError;\n"
            "3) 如果某个名称与输入文件不完全一致,把说明写入 itinerary_plan['data_warnings'] 或 overall_suggestions,但仍必须写出 itinerary_plan.json 并正常退出;\n"
            "4) 只有输入文件缺失、JSON无法解析、输出文件无法写入时,才允许抛异常终止。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _build_weather_codegen_prompt(
        self,
        user_input: str,
        target_script: str,
        artifact_manifest: Dict[str, object],
        fix_context: str,
    ) -> str:
        tool_specs = self._get_tool_specs_for_prompt()
        return (
            f"用户需求:\n{user_input}\n\n"
            f"当前目标脚本: {target_script}\n"
            f"当前可用MCP工具格式(必须严格遵循):\n{tool_specs}\n\n"
            f"{self._amap_call_contract}\n\n"
            f"当前已知产物清单(JSON):\n{json.dumps(artifact_manifest, ensure_ascii=False, indent=2)}\n\n"
            "实现要求:\n"
            "1) 生成天气查询脚本并写入 blocks/*.py;\n"
            "2) 实时调用 maps_weather 获取天气数据;\n"
            "3) 结果写入 blocks/output/weather.json;\n"
            "4) 输出简要计数和文件路径。\n\n"
            f"修复上下文(若为空可忽略):\n{fix_context or '(无)'}"
        )

    def _save_code_file(self, rel_path: str, code: Optional[str]) -> Tuple[bool, str]:
        if not code:
            return False, "未提供python代码块"
        norm = rel_path.replace("\\", "/").strip()
        if not norm.startswith("blocks/") or not norm.endswith(".py"):
            return False, "文件路径必须是 blocks/*.py"
        if ".." in norm:
            return False, "不允许使用上级目录路径"

        target = self.work_dir / norm
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_code = self._ensure_blocks_import_bootstrap(code)
        target.write_text(safe_code, encoding="utf-8")
        return True, f"已写入 {norm}"

    def _run_python_file(self, rel_path: str) -> Tuple[bool, str, Optional[str]]:
        norm = rel_path.replace("\\", "/").strip()
        if not norm.startswith("blocks/") or not norm.endswith(".py"):
            return False, "", "文件路径必须是 blocks/*.py"

        target = self.work_dir / norm
        if not target.exists():
            return False, "", f"文件不存在: {norm}"

        try:
            run_env = os.environ.copy()
            run_env.setdefault("PYTHONUTF8", "1")
            run_env.setdefault("PYTHONIOENCODING", "utf-8")

            proc = subprocess.run(
                [sys.executable, str(target)],
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                env=run_env,
                timeout=self.run_timeout_seconds,
            )
            output = (proc.stdout or "").strip()
            err_output = (proc.stderr or "").strip()
            if proc.returncode == 0:
                return True, output, None
            return False, output, err_output or f"进程退出码: {proc.returncode}"
        except subprocess.TimeoutExpired:
            return False, "", f"执行超时(>{self.run_timeout_seconds}s)"
        except Exception:
            return False, "", traceback.format_exc()

    def _read_code_file(self, rel_path: str) -> Optional[str]:
        norm = rel_path.replace("\\", "/").strip()
        target = self.work_dir / norm
        if not target.exists():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except Exception:
            return None

    def _build_artifact_snapshot(self, rel_paths: List[str], max_chars_per_file: int = 12000) -> str:
        sections: List[str] = []
        for rel_path in rel_paths:
            norm = rel_path.replace("\\", "/").strip()
            target = self.work_dir / norm
            if not target.exists():
                sections.append(f"## {norm}\n(文件不存在)")
                continue

            try:
                raw = target.read_text(encoding="utf-8")
            except Exception as exc:
                sections.append(f"## {norm}\n(读取失败: {exc})")
                continue

            text = raw.strip()
            if len(text) > max_chars_per_file:
                text = text[:max_chars_per_file] + "\n...<已截断,请脚本运行时读取完整文件>..."
            sections.append(f"## {norm}\n```json\n{text}\n```")

        return "\n\n".join(sections)

    def _refresh_artifact_manifest(self) -> Dict[str, object]:
        files: List[Dict[str, object]] = []
        if self.output_dir.exists():
            for p in sorted(self.output_dir.glob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".json", ".csv", ".html", ".txt"}:
                    continue
                rel = p.relative_to(self.work_dir).as_posix()
                stat = p.stat()
                files.append(
                    {
                        "path": rel,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    }
                )

        manifest = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.artifact_files = [item["path"] for item in files]
        return manifest

    def _has_non_html_data_artifacts(self, artifact_manifest: Dict[str, object]) -> bool:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", ""))
            if path.endswith(".json") and not path.endswith("artifacts_manifest.json") and not path.endswith("unsplash_images.json"):
                return True
            if path.endswith(".csv"):
                return True
        return False

    def _validate_python_artifacts(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}

        required_json = [
            "blocks/output/hotels.json",
            "blocks/output/itinerary_plan.json",
        ]
        missing = [p for p in required_json if p not in paths]
        if missing:
            return False, f"缺少必需文件: {', '.join(missing)}"

        ok_hotels, hotels_payload, msg = self._load_json_artifact("blocks/output/hotels.json")
        if not ok_hotels:
            return False, f"hotels.json 无法解析: {msg}"
        hotel_items = self._extract_records_from_payload(hotels_payload)
        if len(hotel_items) == 0:
            return False, "hotels.json 数据为空; 需要通过 maps_text_search 产出非空酒店列表"

        ok_itinerary, itinerary_payload, msg = self._load_json_artifact("blocks/output/itinerary_plan.json")
        if not ok_itinerary:
            return False, f"itinerary_plan.json 无法解析: {msg}"
        if not self._has_itinerary_days(itinerary_payload):
            return False, "itinerary_plan.json 缺少有效天数结构(daily_plans/days/travel_days)"

        return True, "数据产物校验通过"

    def _validate_html_artifacts(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}

        html_paths = [p for p in paths if p.endswith(".html") and p.startswith("blocks/output/")]
        if not html_paths:
            return False, "blocks/output 下未发现html文件"

        if "blocks/output/unsplash_images.json" in paths:
            ok_unsplash, _, msg = self._load_json_artifact("blocks/output/unsplash_images.json")
            if not ok_unsplash:
                return False, f"unsplash_images.json 无法解析: {msg}"

        return True, "HTML产物校验通过"

    def _validate_output_whitelist(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        names: List[str] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            rel_path = str(item.get("path", ""))
            if not rel_path.startswith("blocks/output/"):
                continue
            names.append(Path(rel_path).name)

        disallowed = sorted({name for name in names if name not in FIXED_OUTPUT_FILENAMES})
        if disallowed:
            return False, f"检测到非白名单文件: {', '.join(disallowed)}"
        return True, "白名单校验通过"

    def _archive_disallowed_output_files(self) -> None:
        if not self.output_dir.exists():
            return

        files_to_move: List[Path] = []
        for p in self.output_dir.glob("*"):
            if not p.is_file():
                continue
            if p.name not in FIXED_OUTPUT_FILENAMES:
                files_to_move.append(p)

        if not files_to_move:
            return

        archive_dir = self.output_dir / "_legacy"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = archive_dir / stamp
        target_dir.mkdir(parents=True, exist_ok=True)

        for src in files_to_move:
            try:
                dst = target_dir / src.name
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))
            except Exception:
                continue

    def _validate_records_artifact(
        self,
        artifact_manifest: Dict[str, object],
        rel_path: str,
        display_name: str,
    ) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}
        if rel_path not in paths:
            return False, f"缺少文件 {display_name}"

        ok_payload, payload, msg = self._load_json_artifact(rel_path)
        if not ok_payload:
            return False, f"{display_name} 无法解析: {msg}"
        records = self._extract_records_from_payload(payload)
        if len(records) == 0:
            return False, f"{display_name} 记录为空"
        return True, f"{display_name} 校验通过"

    def _validate_attractions_artifact(self, artifact_manifest: Dict[str, object], user_input: str = "") -> Tuple[bool, str]:
        ok, msg = self._validate_records_artifact(
            artifact_manifest=artifact_manifest,
            rel_path="blocks/output/attractions.json",
            display_name="attractions.json",
        )
        if not ok:
            return False, msg

        ok_payload, payload, load_msg = self._load_json_artifact("blocks/output/attractions.json")
        if not ok_payload:
            return False, f"attractions.json 无法解析: {load_msg}"

        records = self._extract_records_from_payload(payload)
        days, min_count, max_count = self._estimate_attraction_count_bounds(user_input)
        if len(records) > max_count:
            return (
                False,
                f"attractions.json 景点过多: 当前 {len(records)} 个,用户约 {days} 天行程建议控制在 {min_count}-{max_count} 个",
            )

        seen_keys = set()
        duplicate_names: List[str] = []
        missing_required: List[str] = []
        for index, item in enumerate(records, start=1):
            name = str(item.get("name", "")).strip()
            location = str(item.get("location", "")).strip()
            if not name or not location:
                missing_required.append(name or f"第{index}条")
                continue

            normalized_name = re.sub(r"\s+", "", name).lower()
            dedupe_key = str(item.get("id") or f"{normalized_name}|{location}")
            if dedupe_key in seen_keys:
                duplicate_names.append(name)
            seen_keys.add(dedupe_key)

        if duplicate_names:
            return False, f"attractions.json 仍有重复景点: {', '.join(duplicate_names[:5])}"
        if missing_required:
            return False, f"attractions.json 有记录缺少 name/location: {', '.join(missing_required[:5])}"

        return True, "attractions.json 复审校验通过"

    def _validate_itinerary_artifact(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}
        rel_path = "blocks/output/itinerary_plan.json"
        if rel_path not in paths:
            return False, "缺少文件 itinerary_plan.json"

        ok_payload, payload, msg = self._load_json_artifact(rel_path)
        if not ok_payload:
            return False, f"itinerary_plan.json 无法解析: {msg}"
        if not self._has_itinerary_days(payload):
            return False, "itinerary_plan.json 缺少有效天数结构"
        plan_days = self._extract_itinerary_days(payload)
        if not plan_days:
            return False, "itinerary_plan.json 缺少可解析的天级别计划"

        for index, day in enumerate(plan_days, start=1):
            if not isinstance(day, dict):
                return False, f"第{index}天结构非法,必须为对象"
            if not self._day_has_attractions(day):
                return False, f"第{index}天缺少景点分配(需来自 attractions.json 或其衍生结果)"
            if not self._day_has_hotels(day):
                return False, f"第{index}天缺少住宿分配(需来自 hotels.json)"
            if not self._day_has_weather(day):
                return False, f"第{index}天缺少天气信息或天气结论(需来自 weather.json)"
            if not self._day_has_route_plan(day):
                return False, f"第{index}天缺少交通路线结果(需写入 transportation 且来自高德路线工具)"
            if not self._day_has_meals(day):
                return False, f"第{index}天缺少餐食推荐(需基于景点附近检索)"
        return True, "itinerary_plan.json 校验通过"

    def _validate_itinerary_artifact_basic(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}
        rel_path = "blocks/output/itinerary_plan.json"
        if rel_path not in paths:
            return False, "缺少文件 itinerary_plan.json"

        ok_payload, payload, msg = self._load_json_artifact(rel_path)
        if not ok_payload:
            return False, f"itinerary_plan.json 无法解析: {msg}"
        summary_ok, summary_msg = self._validate_request_summary(payload)
        if not summary_ok:
            return False, summary_msg
        if not self._has_itinerary_days(payload):
            return False, "itinerary_plan.json 缺少有效天数结构"
        plan_days = self._extract_itinerary_days(payload)
        if not plan_days:
            return False, "itinerary_plan.json 缺少可解析的天级别计划"
        expected_days = self._extract_summary_days(payload)
        if expected_days > 0 and len(plan_days) != expected_days:
            return False, f"daily_plans 天数({len(plan_days)})必须等于 request_summary.travel_days({expected_days})"

        for index, day in enumerate(plan_days, start=1):
            if not isinstance(day, dict):
                return False, f"第{index}天结构非法,必须为对象"
            if not self._day_has_attractions(day):
                return False, f"第{index}天缺少景点分配"
            if not self._day_has_hotels(day):
                return False, f"第{index}天缺少住宿分配"
            if not self._day_has_meals(day):
                return False, f"第{index}天缺少餐食推荐"

        return True, "itinerary_plan.json 基础校验通过"

    @staticmethod
    def _validate_request_summary(payload: Any) -> Tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "itinerary_plan.json 根对象必须为字典,并包含 request_summary"

        summary = payload.get("request_summary")
        if not isinstance(summary, dict):
            return False, "itinerary_plan.json 缺少 request_summary 对象"

        required_fields = [
            "destination",
            "travel_days",
            "nights",
            "budget_level",
            "preferences",
            "date_text",
            "date_policy",
            "defaults_applied",
        ]
        missing = [field for field in required_fields if field not in summary]
        if missing:
            return False, f"request_summary 缺少字段: {', '.join(missing)}"

        if not str(summary.get("destination") or "").strip():
            return False, "request_summary.destination 不能为空"
        try:
            travel_days = int(summary.get("travel_days"))
        except Exception:
            return False, "request_summary.travel_days 必须为整数"
        if travel_days <= 0:
            return False, "request_summary.travel_days 必须大于0"
        try:
            nights = int(summary.get("nights"))
        except Exception:
            return False, "request_summary.nights 必须为整数"
        if nights < 0:
            return False, "request_summary.nights 不能小于0"
        if not isinstance(summary.get("preferences"), list) or not summary.get("preferences"):
            return False, "request_summary.preferences 必须为非空数组"
        if not isinstance(summary.get("defaults_applied"), list):
            return False, "request_summary.defaults_applied 必须为数组"

        for field in ["destination", "travel_days", "nights", "budget_level", "preferences", "date_text", "date_policy"]:
            if field not in payload:
                return False, f"itinerary_plan 顶层缺少字段: {field}"

        return True, "request_summary 校验通过"

    @staticmethod
    def _extract_summary_days(payload: Any) -> int:
        if not isinstance(payload, dict):
            return 0
        summary = payload.get("request_summary")
        if not isinstance(summary, dict):
            return 0
        try:
            return int(summary.get("travel_days") or 0)
        except Exception:
            return 0

    @staticmethod
    def _extract_itinerary_days(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            for key in ["daily_plans", "days", "itinerary", "daily_theme"]:
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _day_has_attractions(day_payload: Dict[str, Any]) -> bool:
        return TravelCodegenAgent._day_has_non_empty_field(
            day_payload,
            [
                "attractions",
                "spots",
                "pois",
                "scenic_spots",
                "sites",
                "景点",
                "景点安排",
                "当日景点",
            ],
        )

    @staticmethod
    def _day_has_hotels(day_payload: Dict[str, Any]) -> bool:
        return TravelCodegenAgent._day_has_non_empty_field(
            day_payload,
            [
                "hotel",
                "hotels",
                "lodging",
                "accommodation",
                "stay",
                "住宿",
                "酒店",
                "住宿建议",
                "当日住宿",
            ],
        )

    @staticmethod
    def _day_has_weather(day_payload: Dict[str, Any]) -> bool:
        return TravelCodegenAgent._day_has_non_empty_field(
            day_payload,
            [
                "weather",
                "weather_info",
                "forecast",
                "climate",
                "天气",
                "天气信息",
                "天气结论",
                "当日天气",
            ],
        )

    @staticmethod
    def _day_has_route_plan(day_payload: Dict[str, Any]) -> bool:
        return TravelCodegenAgent._day_has_non_empty_field(
            day_payload,
            [
                "transportation",
                "transport",
                "traffic",
                "commute",
                "交通方式",
                "交通",
                "routes",
                "route",
                "route_plan",
                "route_summary",
                "directions",
                "path",
                "路径",
                "路线",
                "路线规划",
                "交通路线",
            ],
        )

    @staticmethod
    def _day_has_meals(day_payload: Dict[str, Any]) -> bool:
        return TravelCodegenAgent._day_has_non_empty_field(
            day_payload,
            [
                "meals",
                "meal",
                "foods",
                "food",
                "restaurants",
                "dining",
                "餐食",
                "美食",
                "用餐",
                "餐饮推荐",
            ],
        )

    @staticmethod
    def _day_has_non_empty_field(day_payload: Dict[str, Any], candidate_keys: List[str]) -> bool:
        lowered_map = {str(k).lower(): v for k, v in day_payload.items()}

        def has_content(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (list, tuple, set, dict)):
                return len(value) > 0
            return True

        for key in candidate_keys:
            value = day_payload.get(key)
            if has_content(value):
                return True

            value_ci = lowered_map.get(key.lower())
            if has_content(value_ci):
                return True

        return False

    def _validate_weather_artifact(self, artifact_manifest: Dict[str, object]) -> Tuple[bool, str]:
        files = artifact_manifest.get("files", []) if isinstance(artifact_manifest, dict) else []
        paths = {str(item.get("path", "")) for item in files if isinstance(item, dict)}
        rel_path = "blocks/output/weather.json"
        if rel_path not in paths:
            return False, "缺少文件 weather.json"

        ok_payload, payload, msg = self._load_json_artifact(rel_path)
        if not ok_payload:
            return False, f"weather.json 无法解析: {msg}"

        if isinstance(payload, list) and len(payload) > 0:
            return True, "weather.json 校验通过"

        if isinstance(payload, dict):
            forecasts = payload.get("forecasts")
            lives = payload.get("lives")
            if isinstance(forecasts, list) and len(forecasts) > 0:
                return True, "weather.json 校验通过"
            if isinstance(lives, list) and len(lives) > 0:
                return True, "weather.json 校验通过"

        return False, "weather.json 结构无有效天气数据"

    def _load_json_artifact(self, rel_path: str) -> Tuple[bool, Optional[Any], str]:
        target = self.work_dir / rel_path
        if not target.exists():
            return False, None, "文件不存在"
        try:
            raw = target.read_text(encoding="utf-8")
        except Exception as exc:
            return False, None, f"UTF-8读取失败: {exc}"

        if not raw.strip():
            return False, None, "文件为空"

        try:
            return True, json.loads(raw), "ok"
        except Exception as exc:
            return False, None, str(exc)

    @staticmethod
    def _extract_records_from_payload(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ["data", "pois", "items", "results", "hotels", "attractions", "foods"]:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []

    @staticmethod
    def _has_itinerary_days(payload: Any) -> bool:
        if isinstance(payload, dict):
            for key in ["daily_plans", "days", "itinerary", "daily_theme"]:
                value = payload.get(key)
                if isinstance(value, list) and len(value) > 0:
                    return True
            value = payload.get("travel_days")
            if isinstance(value, int) and value > 0:
                return True
        return False

    @staticmethod
    def _estimate_attraction_count_bounds(user_input: str) -> Tuple[int, int, int]:
        text = user_input or ""

        days = 0
        numeric_match = re.search(r"(\d{1,2})\s*(?:天|日)", text)
        if numeric_match:
            days = int(numeric_match.group(1))
        else:
            chinese_digits = {
                "一": 1,
                "二": 2,
                "两": 2,
                "俩": 2,
                "三": 3,
                "四": 4,
                "五": 5,
                "六": 6,
                "七": 7,
                "八": 8,
                "九": 9,
                "十": 10,
            }
            chinese_match = re.search(r"([一二两俩三四五六七八九十]{1,3})\s*(?:天|日)", text)
            if chinese_match:
                value = chinese_match.group(1)
                if value == "十":
                    days = 10
                elif value.startswith("十"):
                    days = 10 + chinese_digits.get(value[-1], 0)
                elif value.endswith("十"):
                    days = chinese_digits.get(value[0], 1) * 10
                elif "十" in value:
                    left, right = value.split("十", 1)
                    days = chinese_digits.get(left, 1) * 10 + chinese_digits.get(right, 0)
                else:
                    days = chinese_digits.get(value, 0)

        if days <= 0:
            days = 3

        days = max(1, min(days, 15))
        min_count = max(2, days * 2)
        max_count = max(min_count, days * 3)
        return days, min_count, max_count

    @staticmethod
    def _analyze_common_runtime_error(err: str) -> str:
        text = (err or "").lower()
        suggestions: List[str] = []
        if "jsondecodeerror" in text:
            suggestions.append("仅对 .json 文件使用 json.load,并忽略 html/txt。")
            suggestions.append("所有读取使用 encoding=\"utf-8\"; 写入时确保生成有效JSON。")
        if "string indices must be integers" in text:
            suggestions.append("循环元素先判断 isinstance(item, dict) 再 item['name'] 取值。")
            suggestions.append("把 list / {'data': [...]} / {'pois': [...]} 标准化为统一列表后再渲染。")
        if "unicode" in text or "decode" in text:
            suggestions.append("文件读写统一 UTF-8,并在HTML里保留 <meta charset=\"UTF-8\">。")
        if not suggestions:
            suggestions.append("检查数据文件路径、JSON结构和字段访问方式。")
        return "\n".join(f"- {s}" for s in suggestions)

    @staticmethod
    def _build_stage_thought(stage: str, run_output: str, next_action: str) -> str:
        text = (run_output or "").strip()
        short = text.replace("\r", " ").replace("\n", " ")
        if len(short) > 160:
            short = short[:160] + "..."

        if stage == "python":
            prefix = "老板,数据采集脚本已执行。"
        elif stage == "html":
            prefix = "老板,HTML渲染脚本已执行。"
        elif stage == "attraction":
            prefix = "老板,景点搜索脚本已执行。"
        elif stage == "attraction_review":
            prefix = "老板,景点结果复审脚本已执行。"
        elif stage == "hotel":
            prefix = "老板,酒店搜索脚本已执行。"
        elif stage == "itinerary":
            prefix = "老板,行程规划脚本已执行。"
        elif stage == "weather":
            prefix = "老板,天气查询脚本已执行。"
        else:
            prefix = "老板,当前步骤已执行。"

        if short:
            return f"{prefix} 关键输出: {short} 接下来, {next_action}"
        return f"{prefix} 接下来, {next_action}"

    @staticmethod
    def _validate_generated_code_integrity(code: str, stage: str) -> Tuple[bool, str]:
        text = code or ""
        lowered = text.lower()

        forbidden_rules = [
            (r"def\s+get_amap_service\s*\(", "禁止在脚本中自定义 get_amap_service,必须直接导入 services 中的函数"),
            (r"def\s+call_tool_json\s*\(", "禁止在脚本中自定义 call_tool_json"),
            (r"call_to_json\s*\(", "禁止使用 call_to_json,请使用 services 封装的 call_tool_json"),
            (r"class\s+\w*mock\w*", "禁止使用 Mock 类伪造工具调用"),
            (r"MockAMAPService", "禁止使用 MockAMAPService 伪造数据"),
            (r"from\s+mcproto\s+import\s+call_tool_json", "禁止绕过 service 直接导入 mcproto.call_tool_json"),
            (r"mcp_tool\.run\s*\(", "禁止绕过 service 直接调用 mcp_tool.run"),
        ]
        for pattern, message in forbidden_rules:
            if re.search(pattern, text, re.IGNORECASE):
                return False, message

        if "mock implementation" in lowered or "tool not found" in lowered:
            return False, "检测到 mock/伪造实现痕迹,请改为真实 service 调用"

        if stage == "attraction_review":
            if "attractions.json" not in text:
                return False, "景点复查清洗脚本必须读取并覆盖写回 attractions.json"
            if not re.search(r"json\.dump\s*\(", text):
                return False, "景点复查清洗脚本必须用 json.dump 写回清洗后的 attractions.json"
            uses_mcp = any(
                re.search(pattern, text, re.IGNORECASE)
                for pattern in [
                    r"get_amap_service\s*\(",
                    r"call_tool_json\s*\(",
                    r"maps_text_search",
                    r"maps_search_detail",
                ]
            )
            if uses_mcp:
                required_patterns = [
                    (r"from\s+app\.services\.amap_service\s+import\s+get_amap_service", "补充缺失景点时必须从 app.services.amap_service 导入 get_amap_service"),
                    (r"get_amap_service\s*\(", "补充缺失景点时必须通过 get_amap_service() 获取 service"),
                    (r"call_tool_json\s*\(", "补充缺失景点时必须通过 amap.call_tool_json(...) 调用工具"),
                    (r"maps_text_search", "补充缺失景点时必须先调用 maps_text_search 获取候选"),
                    (r"maps_search_detail", "补充缺失景点时必须调用 maps_search_detail 获取详情"),
                ]
                for pattern, message in required_patterns:
                    if not re.search(pattern, text, re.IGNORECASE):
                        return False, message

        if stage == "itinerary":
            if not re.search(r"\bitinerary_plan\s*=", text, re.IGNORECASE):
                return False, "行程脚本必须显式定义 itinerary_plan 字典,由模型先完成规划后再回填"
            for required_file in ["attractions.json", "hotels.json", "weather.json"]:
                if required_file not in text:
                    return False, f"行程脚本必须读取并校验 {required_file},证明回填数据来源"
            forbidden_abort_patterns = [
                (r"not found in input", "禁止因景点/酒店名称未在输入文件中精确匹配就中止脚本,请写入 data_warnings"),
                (r"Attraction not found", "禁止因景点名称未精确匹配就 raise,请写入 data_warnings"),
                (r"Hotel not found", "禁止因酒店名称未精确匹配就 raise,请写入 data_warnings"),
                (r"raise\s+ValueError\s*\([^)]*(?:attraction|hotel|景点|酒店)", "禁止对景点/酒店匹配失败使用 raise ValueError"),
                (r"sys\.exit\s*\(\s*[1-9]", "禁止在行程脚本中主动非0退出"),
            ]
            for pattern, message in forbidden_abort_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return False, message
            runtime_decision_patterns = [
                (r"\brandom\.", "禁止在行程脚本运行时随机生成行程"),
                (r"\bsorted\s*\(", "禁止在行程脚本运行时通过排序算法临时决定核心行程"),
                (r"\.sort\s*\(", "禁止在行程脚本运行时通过排序算法临时决定核心行程"),
            ]
            for pattern, message in runtime_decision_patterns:
                if re.search(pattern, text):
                    return False, message

        requires_amap = stage in {"attraction", "hotel", "weather", "python"}
        if requires_amap:
            # 使用正则忽略多余空格
            patterns = [
                r"from\s+app\.services\.amap_service\s+import\s+get_amap_service",
                r"get_amap_service\s*\(",
                r"call_tool_json\s*\("
            ]
            for p in patterns:
                if not re.search(p, text):
                    if "call_tool_json" in p:
                        return False, (
                            "缺少必需调用模式: amap.call_tool_json(...). "
                            "生成脚本必须通过 app.services.amap_service.get_amap_service() 取得 service, "
                            "再调用 service.call_tool_json(tool_name, arguments)。"
                        )
                    return False, f"缺少必需调用模式: {p} (请检查导入语句或工具调用格式)"

        return True, "代码约束校验通过"

    def _get_tool_specs_for_prompt(self) -> str:
        if self._tool_specs_text_cache:
            return self._tool_specs_text_cache

        try:
            from ..services.amap_service import get_amap_mcp_tool

            mcp_tool = get_amap_mcp_tool()
            tools = getattr(mcp_tool, "_available_tools", []) or []
            lines: List[str] = ["可用高德工具及参数格式(实时读取):"]
            for item in tools:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                schema = item.get("input_schema", {})
                if not name or not isinstance(schema, dict):
                    continue

                props = schema.get("properties", {})
                required = set(schema.get("required", [])) if isinstance(schema.get("required", []), list) else set()
                if not isinstance(props, dict):
                    props = {}

                arg_parts: List[str] = []
                for key, value in props.items():
                    key_name = str(key)
                    is_required = key_name in required
                    default_value = None
                    if isinstance(value, dict) and "default" in value:
                        default_value = value.get("default")

                    if is_required:
                        arg_parts.append(key_name)
                    elif default_value is not None:
                        arg_parts.append(f"{key_name}={default_value}")
                    else:
                        arg_parts.append(f"{key_name}?")

                lines.append(f"- {name}({', '.join(arg_parts)})")

            lines.append("call_tool_json 失败时请读取 result['error'],不要读取 result['message']。")
            self._tool_specs_text_cache = "\n".join(lines)
            return self._tool_specs_text_cache
        except Exception:
            self._tool_specs_text_cache = (
                f"{AMAP_TOOL_FORMAT_FALLBACK}\n"
                "call_tool_json 失败时请读取 result['error'],不要读取 result['message']。"
            )
            return self._tool_specs_text_cache

    def _discover_html_outputs(self) -> List[str]:
        html_files: List[str] = []
        for p in self.blocks_dir.rglob("*.html"):
            rel = p.relative_to(self.work_dir).as_posix()
            html_files.append(rel)
        return sorted(set(html_files))

    def _relocate_stray_html_outputs(self) -> None:
        stray_candidates: List[Path] = []

        root_html = list(self.work_dir.glob("*.html"))
        output_dir = self.work_dir / "output"
        output_html = list(output_dir.glob("*.html")) if output_dir.exists() else []

        stray_candidates.extend(root_html)
        stray_candidates.extend(output_html)

        for src in stray_candidates:
            if not src.exists() or not src.is_file():
                continue
            target = self.output_dir / src.name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.unlink()
                src.replace(target)
            except Exception:
                continue

    def _build_final_answer(self, user_input: str) -> str:
        self._refresh_artifact_manifest()
        html_files = self._discover_html_outputs()
        if html_files:
            preferred = "blocks/output/itinerary.html"
            html_path = preferred if preferred in html_files else html_files[0]
            return f"已生成规划HTML: {html_path}"

        _ = user_input
        return "任务完成,但未发现规划HTML文件。"

    @staticmethod
    def _extract_python_code(text: str) -> Optional[str]:
        m = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_action_path(text: str, action_name: str) -> Optional[str]:
        m = re.search(rf"{re.escape(action_name)}\s*:\s*([^\n\r]+)", text)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_review_status(text: str) -> Optional[str]:
        m = re.search(r"REVIEW_STATUS\s*:\s*([A-Za-z_\-]+)", text or "", re.IGNORECASE)
        if not m:
            return None
        value = m.group(1).strip().upper().replace("-", "_")
        if value in {"PASS", "PASSED", "OK"}:
            return "PASS"
        if value in {"NEEDS_FIX", "NEED_FIX", "FIX", "FAIL", "FAILED"}:
            return "NEEDS_FIX"
        return None

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, object]]:
        if not text:
            return None

        fenced = re.search(r"```json\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else text.strip()

        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            parsed = json.loads(candidate[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _ensure_blocks_import_bootstrap(code: str) -> str:
        text = code or ""
        needs_app_import = ("from app." in text) or ("import app." in text)
        has_bootstrap = "Path(__file__).resolve().parents[1]" in text and "sys.path.insert(" in text

        if not needs_app_import or has_bootstrap:
            return text

        bootstrap = (
            "import sys\n"
            "from pathlib import Path\n\n"
            "# Ensure backend root is importable when running blocks/*.py directly\n"
            "ROOT = Path(__file__).resolve().parents[1]\n"
            "if str(ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(ROOT))\n\n"
        )
        return bootstrap + text

    @staticmethod
    def _normalize_url(url: str) -> str:
        value = (url or "").strip()
        if not value:
            return ""
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value.rstrip("/")

    @staticmethod
    def _ensure_env_loaded() -> None:
        get_settings()
