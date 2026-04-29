"""LLM服务模块"""

import os
from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 全局LLM实例
_llm_instance = None


def _first_non_empty(*env_names: str) -> str:
    """按顺序获取第一个非空环境变量值"""
    for name in env_names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def _is_placeholder(value: str) -> bool:
    """判断是否为示例占位值"""
    if not value:
        return True
    lowered = value.strip().lower()
    return any(token in lowered for token in ["your-", "example", "placeholder", "changeme"])


def _normalize_base_url(url: str) -> str:
    """规范化LLM Base URL,缺少协议时自动补齐https://"""
    if not url:
        return ""
    normalized = url.strip()
    if not normalized:
        return ""
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized.rstrip("/")


def _apply_llm_env_aliases() -> None:
    """统一LLM环境变量,兼容SiliconFlow/OpenAI/项目自定义变量名"""
    api_key = _first_non_empty("SILICONFLOW_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")
    base_url = _first_non_empty("SILICONFLOW_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL")
    base_url = _normalize_base_url(base_url)
    model = _first_non_empty("SILICONFLOW_MODEL", "LLM_MODEL_ID", "OPENAI_MODEL")

    if _is_placeholder(base_url):
        base_url = _normalize_base_url(_first_non_empty("OPENAI_BASE_URL", "LLM_BASE_URL"))

    if _is_placeholder(model):
        model = _first_non_empty("LLM_MODEL_ID", "OPENAI_MODEL")

    if api_key:
        os.environ["LLM_API_KEY"] = api_key
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["SILICONFLOW_API_KEY"] = api_key

    if base_url:
        os.environ["LLM_BASE_URL"] = base_url
        os.environ["OPENAI_BASE_URL"] = base_url
        os.environ["SILICONFLOW_BASE_URL"] = base_url

    if model:
        os.environ["LLM_MODEL_ID"] = model
        os.environ["OPENAI_MODEL"] = model
        os.environ["SILICONFLOW_MODEL"] = model


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)
    
    Returns:
        HelloAgentsLLM实例
    """
    global _llm_instance
    
    if _llm_instance is None:
        # 触发配置模块加载,确保环境变量已从backend/.env读取
        get_settings()
        _apply_llm_env_aliases()

        timeout = int(os.getenv("LLM_TIMEOUT", "180"))
        max_tokens_env = os.getenv("LLM_MAX_TOKENS", "").strip()
        max_tokens = int(max_tokens_env) if max_tokens_env else None

        # HelloAgentsLLM会自动从环境变量读取配置
        # 包括OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL等
        _llm_instance = HelloAgentsLLM(timeout=timeout, max_tokens=max_tokens)
        
        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")
        print(f"   超时: {timeout}s")
        print(f"   Max Tokens: {max_tokens if max_tokens is not None else '未限制'}")
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None

