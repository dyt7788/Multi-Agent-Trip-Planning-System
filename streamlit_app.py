"""Streamlit chat app for AiPy-style travel planner agent."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Dict

import streamlit as st
import streamlit.components.v1 as components

from app.aipy import TravelCodegenAgent
from app.config import get_settings
from app.services.amap_service import get_amap_mcp_tool
from app.services.conversation_db import ConversationDatabase


st.set_page_config(page_title="AiPy Travel Agent", page_icon="🧭", layout="wide")

WORK_DIR = Path(__file__).resolve().parent


@st.cache_resource
def get_agent(max_iterations: int) -> TravelCodegenAgent:
    return TravelCodegenAgent(max_iterations=max_iterations)


@st.cache_resource
def get_conversation_db() -> ConversationDatabase:
    return ConversationDatabase(WORK_DIR / "data" / "conversations.db")


def to_history(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # Keep only role/content pairs for LLM.
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def short_text(text: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def collect_chat_pairs(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    pairs: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None
    for msg in messages:
        if msg.get("role") == "user":
            current = {"user": msg.get("content", ""), "assistant": ""}
            pairs.append(current)
        elif msg.get("role") == "assistant" and current is not None:
            current["assistant"] = msg.get("content", "")
    return pairs


def reset_current_conversation() -> None:
    st.session_state.messages = []
    st.session_state.steps = []
    st.session_state.selected_file = None
    st.session_state.selected_history = None
    st.session_state.selected_conversation_id = None
    st.session_state.active_page = "chat"
    st.session_state.file_widget_version = st.session_state.get("file_widget_version", 0) + 1
    st.session_state.file_widget_prefix = f"file_{st.session_state.file_widget_version}"
    if "history_search" in st.session_state:
        st.session_state.history_search = ""


def render_sidebar_conversations(messages: List[Dict[str, str]], conversation_db: ConversationDatabase) -> None:
    if st.button("新建对话", key="new_conversation", use_container_width=True):
        reset_current_conversation()
        st.rerun()

    current_tab, history_tab = st.tabs(["对话", "历史对话"])

    with current_tab:
        if not messages:
            st.caption("暂无对话")
        for index, msg in enumerate(messages[-8:], start=max(1, len(messages) - 7)):
            role_label = "用户" if msg.get("role") == "user" else "助手"
            st.markdown(f"**{index}. {role_label}**")
            st.caption(short_text(msg.get("content", ""), 90))

    with history_tab:
        query = st.text_input("搜索历史", key="history_search", placeholder="输入城市、偏好、预算等")
        conversations = (
            conversation_db.search_conversations(query, limit=20)
            if query.strip()
            else conversation_db.list_conversations(limit=20)
        )

        st.caption("搜索结果" if query.strip() else "最近对话")
        if not conversations:
            st.caption("暂无已保存对话")

        for item in conversations:
            conversation_id = item.get("id", "")
            title = item.get("title") or "未命名对话"
            status = item.get("status", "")
            label = f"{short_text(title, 32)}"
            if status and status != "success":
                label = f"{label} · {status}"
            if st.button(label, key=f"conv_{conversation_id}", use_container_width=True):
                st.session_state.selected_conversation_id = conversation_id
                st.session_state.active_page = "history"

            if conversation_id == st.session_state.get("selected_conversation_id"):
                st.caption(short_text(item.get("user_preview", ""), 90))


def render_steps(steps: List[object], target) -> None:
    completed_runs = {
        (step.step_index, step.file_path)
        for step in steps
        if step.event_type == "run"
    }

    with target.container():
        for step in steps:
            if step.event_type in {"thought", "final"}:
                continue
            if step.event_type == "run_start" and (step.step_index, step.file_path) in completed_runs:
                continue

            status_text = "成功" if step.success else "失败"
            prefix = "✅" if step.success else "❌"
            if step.event_type == "run_start":
                prefix = "⏳"
                status_text = "运行中"

            expanded = step.event_type == "run_start" or not step.success
            with st.expander(f"{prefix} {step.title}", expanded=expanded):
                st.markdown(f"**Step {step.step_index} · {status_text}**")
                if step.file_path:
                    st.markdown(f"文件: `{step.file_path}`")

                if step.event_type == "run_start":
                    running_target = f": `{step.file_path}`" if step.file_path else ""
                    st.info(f"正在运行{running_target}")
                elif step.success:
                    st.success("成功")

                if step.error:
                    st.markdown("**运行错误结果**")
                    st.error(step.error)
                    if step.output:
                        st.text(step.output)


def normalize_rel_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def collect_created_files(work_dir: Path, steps: List[object]) -> List[str]:
    paths = set()
    for step in steps:
        rel_path = normalize_rel_path(getattr(step, "file_path", "") or "")
        if rel_path.startswith("blocks/"):
            paths.add(rel_path)

        for field in ("output", "assistant_text"):
            value = getattr(step, field, "") or ""
            for match in re.findall(r"blocks/[\w\-/\.]+\.(?:py|json|csv|html|txt)", value):
                paths.add(normalize_rel_path(match))

    blocks_dir = work_dir / "blocks"
    output_dir = blocks_dir / "output"
    if blocks_dir.exists():
        for target in blocks_dir.glob("generated_*.py"):
            if target.is_file():
                paths.add(target.relative_to(work_dir).as_posix())
    if output_dir.exists():
        for target in output_dir.iterdir():
            if target.is_file() and target.suffix.lower() in {".json", ".csv", ".html", ".txt"}:
                paths.add(target.relative_to(work_dir).as_posix())

    return sorted(paths)


def build_file_tree(paths: List[str]) -> Dict[str, object]:
    tree: Dict[str, object] = {}
    for rel_path in paths:
        parts = [part for part in normalize_rel_path(rel_path).split("/") if part]
        cursor = tree
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        if parts:
            cursor[parts[-1]] = rel_path
    return tree


def format_file_size(work_dir: Path, rel_path: str) -> str:
    target = resolve_created_file(work_dir, rel_path)
    if target is None or not target.exists():
        return ""

    size = target.stat().st_size
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.2f} {unit}"


def file_icon(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    if suffix == ".json":
        return "JSON"
    if suffix == ".html":
        return "HTML"
    if suffix == ".py":
        return "PY"
    if suffix == ".csv":
        return "CSV"
    if suffix == ".txt":
        return "TXT"
    return "FILE"


def render_file_tree(
    work_dir: Path,
    tree: Dict[str, object],
    key_prefix: str,
    depth: int = 0,
    base_path: str = "",
) -> None:
    dirs = sorted((name, value) for name, value in tree.items() if isinstance(value, dict))
    files = sorted((name, value) for name, value in tree.items() if isinstance(value, str))
    indent = "　" * depth

    for dirname, children in dirs:
        full_path = f"{base_path}/{dirname}" if base_path else dirname
        state_key = f"{key_prefix}_dir_{full_path}"
        if state_key not in st.session_state:
            st.session_state[state_key] = depth < 1

        is_open = bool(st.session_state.get(state_key))
        icon = "▾" if is_open else "▸"
        label = f"{indent}{icon} 📁 {dirname}"
        if st.button(label, key=f"{key_prefix}_btn_{full_path}", use_container_width=True):
            st.session_state[state_key] = not is_open
            st.rerun()

        if st.session_state.get(state_key):
            render_file_tree(work_dir, children, key_prefix, depth + 1, full_path)

    selected = st.session_state.get("selected_file", "")
    for filename, rel_path in files:
        cols = st.columns([0.08, 0.62, 0.3])
        label_prefix = "●" if rel_path == selected else " "
        cols[0].caption(f"{indent}{label_prefix}")
        label = f"{file_icon(rel_path)}  {filename}"
        if cols[1].button(label, key=f"{key_prefix}_{rel_path}", use_container_width=True):
            st.session_state.selected_file = rel_path
            st.session_state.active_page = "file"
        cols[2].caption(format_file_size(work_dir, rel_path))


def resolve_created_file(work_dir: Path, rel_path: str) -> Path | None:
    target = (work_dir / normalize_rel_path(rel_path)).resolve()
    root = work_dir.resolve()
    try:
        if not target.is_relative_to(root):
            return None
    except AttributeError:
        if root not in target.parents and target != root:
            return None
    return target


def render_created_files_sidebar(work_dir: Path, steps: List[object], key_prefix: str) -> None:
    files = collect_created_files(work_dir, steps)
    st.subheader("已创建文件")
    if not files:
        st.caption("暂无创建文件")
        return

    render_file_tree(work_dir, build_file_tree(files), key_prefix)


def render_selected_file(work_dir: Path, rel_path: str | None) -> None:
    if not rel_path:
        st.info("请从左侧文件目录选择要查看的文件。")
        return

    target = resolve_created_file(work_dir, rel_path)
    st.subheader("文件浏览")
    st.caption(rel_path)

    if target is None or not target.exists() or not target.is_file():
        st.warning("文件不存在或不在工作目录内")
        return

    suffix = target.suffix.lower()
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:
        st.error(f"读取文件失败: {exc}")
        return

    if suffix == ".html":
        components.html(text, height=720, scrolling=True)
        with st.expander("查看 HTML 源码", expanded=False):
            st.code(text, language="html")
    elif suffix == ".json":
        try:
            st.json(json.loads(text))
        except Exception:
            st.code(text, language="json")
    elif suffix == ".py":
        st.code(text, language="python")
    elif suffix == ".csv":
        st.code(text, language="csv")
    else:
        st.text(text)


def render_history_detail(conversation_db: ConversationDatabase, conversation_id: str | None) -> None:
    if not conversation_id:
        st.info("请从左侧历史对话选择一条记录。")
        return

    detail = conversation_db.get_conversation(conversation_id)
    conversation = detail.get("conversation")
    messages = detail.get("messages", [])
    steps = detail.get("steps", [])

    if not conversation:
        st.warning("未找到这条历史对话。")
        return

    st.subheader(conversation.get("title") or "未命名对话")
    st.caption(
        f"状态: {conversation.get('status', '')} · "
        f"创建: {conversation.get('created_at', '')} · "
        f"更新: {conversation.get('updated_at', '')}"
    )

    st.divider()
    st.markdown("#### 对话原文")
    for msg in messages:
        role = msg.get("role", "assistant")
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(msg.get("content", ""))

    st.divider()
    st.markdown("#### Agent 原始过程")
    if not steps:
        st.caption("这条历史记录没有保存 Agent 过程。")
        return

    for step in steps:
        success = bool(step.get("success", 1))
        prefix = "✅" if success else "❌"
        if step.get("event_type") == "run_start":
            prefix = "⏳"
        if step.get("event_type") == "thought":
            prefix = "💭"

        title = step.get("title") or step.get("event_type") or "步骤"
        label = f"{prefix} Step {step.get('step_index', '')} · {title}"
        with st.expander(label, expanded=step.get("event_type") == "thought" or not success):
            st.markdown(f"事件类型: `{step.get('event_type', '')}`")
            if step.get("agent_name"):
                st.markdown(f"Agent: `{step.get('agent_name')}`")
            if step.get("file_path"):
                st.markdown(f"文件: `{step.get('file_path')}`")

            if step.get("assistant_text"):
                st.markdown("**模型/协调器原文**")
                st.code(step.get("assistant_text", ""), language="markdown")
            if step.get("code"):
                st.markdown("**生成代码原文**")
                st.code(step.get("code", ""), language="python")
            if step.get("output"):
                st.markdown("**运行输出原文**")
                st.code(step.get("output", ""), language="text")
            if step.get("error"):
                st.markdown("**运行错误原文**")
                st.code(step.get("error", ""), language="text")


def collect_html_paths(final_answer: str, steps: List[object]) -> List[str]:
    paths = set(re.findall(r"blocks/[\w\-/\.]+\.html", final_answer or ""))
    for step in steps:
        if step.file_path and step.file_path.endswith(".html"):
            paths.add(step.file_path)
        if step.output:
            for match in re.findall(r"blocks/[\w\-/\.]+\.html", step.output):
                paths.add(match)
    return sorted(paths)


def render_html_outputs(work_dir: Path, html_paths: List[str]) -> None:
    if not html_paths:
        return

    st.divider()
    st.subheader("规划 HTML")
    for rel in html_paths:
        st.success(f"已生成规划HTML: {rel}")
        target = work_dir / rel
        if not target.exists():
            st.warning(f"文件不存在: {rel}")
            continue
        try:
            html_text = target.read_text(encoding="utf-8")
        except Exception as exc:
            st.error(f"读取HTML失败: {exc}")
            continue
        components.html(html_text, height=720, scrolling=True)


st.title("🧭 AiPy 旅行规划 Agent")
st.caption("模式: 大模型 + Python 代码生成与执行 (Code Interpreter Style)")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "steps" not in st.session_state:
    st.session_state.steps = []

if "selected_file" not in st.session_state:
    st.session_state.selected_file = None

if "selected_history" not in st.session_state:
    st.session_state.selected_history = None

if "selected_conversation_id" not in st.session_state:
    st.session_state.selected_conversation_id = None

if "active_page" not in st.session_state:
    st.session_state.active_page = "chat"

if "file_widget_version" not in st.session_state:
    st.session_state.file_widget_version = 0

if "file_widget_prefix" not in st.session_state:
    st.session_state.file_widget_prefix = f"file_{st.session_state.file_widget_version}"

conversation_db = get_conversation_db()

with st.sidebar:
    render_sidebar_conversations(st.session_state.messages, conversation_db)

    st.divider()
    created_files_slot = st.empty()
    with created_files_slot.container():
        render_created_files_sidebar(WORK_DIR, st.session_state.steps, st.session_state.file_widget_prefix)

    st.divider()
    st.subheader("运行参数")
    max_iterations = st.slider("最大迭代轮次", min_value=20, max_value=40, value=28, step=1)
    st.markdown(
        "请在 backend/.env 配置: `SILICONFLOW_API_KEY`, `SILICONFLOW_BASE_URL`, `SILICONFLOW_MODEL`"
    )

    st.divider()
    st.subheader("环境自检")
    st.caption(f"Python: {sys.executable}")

    settings = get_settings()
    st.caption(f"AMAP_API_KEY: {'已配置' if bool(settings.amap_api_key) else '未配置'}")

    if st.button("运行 MCP 连通自检", use_container_width=True):
        try:
            mcp = get_amap_mcp_tool()
            names = [t.get("name") for t in mcp._available_tools]
            st.success(f"MCP 初始化成功,可用工具 {len(names)} 个")
            st.code("\n".join(names), language="text")
        except Exception as exc:
            st.error(f"MCP 自检失败: {exc}")

agent = get_agent(max_iterations=max_iterations)

if st.session_state.active_page == "file":
    col_back, col_path = st.columns([0.16, 0.84])
    if col_back.button("返回对话", use_container_width=True):
        st.session_state.active_page = "chat"
        st.rerun()
    col_path.caption(st.session_state.selected_file or "未选择文件")
    render_selected_file(WORK_DIR, st.session_state.selected_file)
    st.stop()

if st.session_state.active_page == "history":
    col_back, col_title = st.columns([0.16, 0.84])
    if col_back.button("返回对话", use_container_width=True):
        st.session_state.active_page = "chat"
        st.rerun()
    col_title.caption("历史对话详情")
    render_history_detail(conversation_db, st.session_state.selected_conversation_id)
    st.stop()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("例如: 帮我规划一个上海4天3晚的行程,偏好历史文化和美食,预算3000")

if user_input:
    conversation_id = conversation_db.create_conversation(user_input)
    if conversation_id:
        st.session_state.selected_conversation_id = conversation_id

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        live_status = st.status("Agent 正在生成规划HTML...", expanded=True)
        live_steps = st.empty()
        result = None
        steps: List[object] = []

        try:
            for event in agent.run_stream(user_input=user_input, history=to_history(st.session_state.messages[:-1])):
                if event.get("type") == "step":
                    step = event.get("step")
                    if step is not None:
                        steps.append(step)
                        render_steps(steps, live_steps)
                        conversation_db.save_step(conversation_id, step)
                        if step.event_type not in {"thought", "final"}:
                            live_status.update(label=f"Agent 执行中: {step.title}", state="running", expanded=True)
                elif event.get("type") == "final":
                    candidate = event.get("result")
                    if candidate is not None:
                        result = candidate
        except Exception as exc:
            err = f"Agent执行失败: {exc}"
            live_status.update(label="Agent 执行失败", state="error", expanded=True)
            st.error(err)
            st.session_state.steps = steps
            st.session_state.file_widget_version += 1
            st.session_state.file_widget_prefix = f"file_{st.session_state.file_widget_version}"
            with created_files_slot.container():
                render_created_files_sidebar(WORK_DIR, st.session_state.steps, st.session_state.file_widget_prefix)
            st.session_state.messages.append({"role": "assistant", "content": err})
            conversation_db.finish_conversation(conversation_id, user_input, err, status="error")
        else:
            if result is None:
                err = "Agent执行失败: 未返回最终结果"
                live_status.update(label="Agent 执行失败", state="error", expanded=True)
                st.error(err)
                st.session_state.steps = steps
                st.session_state.file_widget_version += 1
                st.session_state.file_widget_prefix = f"file_{st.session_state.file_widget_version}"
                with created_files_slot.container():
                    render_created_files_sidebar(WORK_DIR, st.session_state.steps, st.session_state.file_widget_prefix)
                st.session_state.messages.append({"role": "assistant", "content": err})
                conversation_db.finish_conversation(conversation_id, user_input, err, status="error")
            else:
                live_status.update(label="Agent 执行完成", state="complete", expanded=False)
                st.success(result.final_answer)
                st.session_state.messages.append({"role": "assistant", "content": result.final_answer})
                conversation_db.finish_conversation(
                    conversation_id,
                    user_input,
                    result.final_answer,
                    status="success",
                )
                st.session_state.steps = result.steps
                st.session_state.file_widget_version += 1
                st.session_state.file_widget_prefix = f"file_{st.session_state.file_widget_version}"
                with created_files_slot.container():
                    render_created_files_sidebar(WORK_DIR, st.session_state.steps, st.session_state.file_widget_prefix)
                html_paths = collect_html_paths(result.final_answer, result.steps)
                render_html_outputs(agent.work_dir, html_paths)

if st.session_state.steps and not user_input:
    st.divider()
    st.subheader("执行状态")
    render_steps(st.session_state.steps, st)

    html_paths = collect_html_paths(
        st.session_state.messages[-1]["content"] if st.session_state.messages else "",
        st.session_state.steps,
    )
    render_html_outputs(agent.work_dir, html_paths)
