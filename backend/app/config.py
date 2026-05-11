"""Application configuration for the travel multi-agent backend."""

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)
load_dotenv(override=False)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_name: str = "Multi-Agent Travel Planner"
    app_version: str = "3.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = (
        "http://localhost:5173,http://localhost:3000,"
        "http://127.0.0.1:5173,http://127.0.0.1:3000"
    )

    data_dir: Path = Field(default=BACKEND_ROOT / "data")
    report_dir: Path = Field(default=BACKEND_ROOT / "data" / "reports")
    memory_db_path: Path = Field(default=BACKEND_ROOT / "data" / "travel_memory.db")
    redis_url: Optional[str] = None

    enable_live_web: bool = True
    request_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
    search_api_url: Optional[str] = None
    search_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    tavily_api_url: str = "https://api.tavily.com/search"
    search_timeout: float = 12.0
    max_search_results: int = 6
    max_crawled_articles: int = 6

    # Legacy OpenAI-compatible config (fallback)
    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 90.0
    llm_max_tokens: Optional[int] = 1600

    # QueryAgent legacy config
    query_llm_api_key: Optional[str] = None
    query_llm_base_url: Optional[str] = None
    query_llm_model: Optional[str] = None
    query_query_model: Optional[str] = None
    query_extraction_model: Optional[str] = None
    query_synthesis_model: Optional[str] = None
    query_llm_timeout: Optional[float] = None
    query_llm_max_tokens: Optional[int] = None

    # OpenAI vision model
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None
    vision_model: str = "gpt-4o-mini"
    enable_vision: bool = True

    # ==========================================
    # SiliconFlow Configuration (Primary)
    # ==========================================
    siliconflow_api_key: Optional[str] = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "Qwen/Qwen2.5-7B-Instruct"

    # Per-Agent model configuration
    total_agent_model: str = "Qwen/Qwen2.5-7B-Instruct"
    total_agent_timeout: float = 30.0
    total_agent_max_tokens: int = 500

    strategy_agent_model: str = "Qwen/Qwen2.5-7B-Instruct"
    strategy_agent_timeout: float = 30.0
    strategy_agent_max_tokens: int = 800

    query_agent_model: str = "Qwen/Qwen2.5-14B-Instruct"
    query_agent_timeout: float = 60.0
    query_agent_max_tokens: int = 2000

    analysis_agent_model: str = "Qwen/Qwen2.5-32B-Instruct"
    analysis_agent_timeout: float = 120.0
    analysis_agent_max_tokens: int = 8000

    report_agent_model: str = "Qwen/Qwen2.5-7B-Instruct"
    report_agent_timeout: float = 30.0
    report_agent_max_tokens: int = 1500

    # Legacy OpenAI settings
    hf_token: Optional[str] = None
    hf_vision_model: str = "openai/clip-vit-base-patch32"
    enable_pdf_export: bool = False

    # Map and image services
    amap_api_key: Optional[str] = None
    unsplash_access_key: Optional[str] = None

    @field_validator("data_dir", "report_dir", "memory_db_path", mode="before")
    @classmethod
    def resolve_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path

    @property
    def effective_llm_api_key(self) -> Optional[str]:
        """Get API key with SiliconFlow as priority."""
        return self.siliconflow_api_key or self.llm_api_key or self.openai_api_key

    @property
    def effective_llm_base_url(self) -> str:
        """Get base URL with SiliconFlow as priority."""
        return self.siliconflow_base_url or self.openai_base_url or self.llm_base_url

    @property
    def effective_llm_model(self) -> str:
        """Get default model with SiliconFlow as priority."""
        return self.siliconflow_model or self.openai_model or self.llm_model

    # Per-Agent model getters
    def get_agent_model_config(self, agent_name: str) -> dict:
        """Get model configuration for a specific agent."""
        configs = {
            "TotalAgent": {
                "model": self.total_agent_model,
                "timeout": self.total_agent_timeout,
                "max_tokens": self.total_agent_max_tokens,
            },
            "StrategyAgent": {
                "model": self.strategy_agent_model,
                "timeout": self.strategy_agent_timeout,
                "max_tokens": self.strategy_agent_max_tokens,
            },
            "QueryAgent": {
                "model": self.query_agent_model,
                "timeout": self.query_agent_timeout,
                "max_tokens": self.query_agent_max_tokens,
            },
            "AnalysisAgent": {
                "model": self.analysis_agent_model,
                "timeout": self.analysis_agent_timeout,
                "max_tokens": self.analysis_agent_max_tokens,
            },
            "ReportAgent": {
                "model": self.report_agent_model,
                "timeout": self.report_agent_timeout,
                "max_tokens": self.report_agent_max_tokens,
            },
        }
        return configs.get(agent_name, {
            "model": self.effective_llm_model,
            "timeout": self.llm_timeout,
            "max_tokens": self.llm_max_tokens,
        })

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    return settings
