"""Shared API and agent data models."""

from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AgentTrace(BaseModel):
    agent: str
    status: Literal["started", "completed", "skipped", "failed"] = "completed"
    message: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TripPlanRequest(BaseModel):
    destination: str = Field(default="", description="Destination city or region. Empty means extract from free_text.")
    days: int = Field(default=0, ge=0, le=30, description="Trip length in days. 0 means extract from free_text.")
    budget: Optional[float] = Field(default=None, ge=0, description="Total budget in CNY.")
    preferences: List[str] = Field(default_factory=list)
    preference_details: Dict[str, Any] = Field(default_factory=dict)
    mode: Literal["初次规划", "修改报告", "plan_trip", "modify_report"] = "初次规划"
    travel_style: Optional[str] = Field(default=None, description="Pace or style, such as relaxed, foodie, family.")
    start_date: Optional[Date] = None
    user_id: str = Field(default="guest")
    conversation_id: Optional[str] = None
    free_text: str = ""
    image_urls: List[str] = Field(default_factory=list)
    xhs_post_urls: List[str] = Field(default_factory=list)
    language: str = Field(default="zh-CN")
    export_pdf: bool = False
    # For modify mode
    previous_plan: Optional["ItineraryPlan"] = Field(default=None, description="Previous plan to modify.")
    modification_request: str = Field(default="", description="User's modification instructions.")

    @field_validator("preferences", mode="before")
    @classmethod
    def split_preferences(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, dict):
            items: List[str] = []
            for key, raw in value.items():
                if isinstance(raw, list):
                    items.extend(str(item).strip() for item in raw if str(item).strip())
                elif raw is not None and str(raw).strip():
                    items.append(f"{key}:{raw}")
            return items
        if isinstance(value, str):
            return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        return value

    @model_validator(mode="before")
    @classmethod
    def keep_preference_details(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("preferences"), dict):
            data = dict(data)
            data.setdefault("preference_details", data["preferences"])
        return data


class QueryResearchRequest(BaseModel):
    destination: str
    keywords: List[str] = Field(default_factory=list)
    limit: int = Field(default=6, ge=1, le=20)


class TotalAgentOutput(BaseModel):
    action: Literal["plan_trip", "modify_report"] = "plan_trip"
    agent_input: Dict[str, Any] = Field(default_factory=dict)


class StrategyQueryInput(BaseModel):
    destination: str
    days: int
    scenic_types: List[str] = Field(default_factory=list)
    required_info: List[str] = Field(default_factory=lambda: ["攻略"])
    keywords: List[str] = Field(default_factory=list)
    limit: int = Field(default=6, ge=1, le=20)


class StrategyAgentOutput(BaseModel):
    destination: str
    days: int
    scenic_types: List[str] = Field(default_factory=list)
    required_info: List[str] = Field(default_factory=lambda: ["攻略"])
    preferences: Dict[str, Any] = Field(default_factory=dict)
    user_history: Dict[str, Any] = Field(default_factory=dict)
    query: StrategyQueryInput
    memory: "PreferenceMemory"
    trace: List[AgentTrace] = Field(default_factory=list)


class ImageAnalysisRequest(BaseModel):
    destination: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    xhs_post_urls: List[str] = Field(default_factory=list)
    user_id: str = "guest"


class PreferenceUpdateRequest(BaseModel):
    preferences: List[str] = Field(default_factory=list)
    disliked: List[str] = Field(default_factory=list)
    budget_level: Optional[str] = None
    pace: Optional[str] = None
    notes: str = ""


class WebSource(BaseModel):
    title: str = ""
    url: str
    snippet: str = ""
    source_type: Literal["search", "article", "blog", "forum", "xiaohongshu", "fallback"] = "search"
    score: float = 0.0


class ArticleContent(BaseModel):
    title: str = ""
    url: str
    text: str = ""
    image_urls: List[str] = Field(default_factory=list)


class ExtractedTravelInfo(BaseModel):
    destination: str
    summary: str = ""
    attractions: List[str] = Field(default_factory=list)
    restaurants: List[str] = Field(default_factory=list)
    activities: List[str] = Field(default_factory=list)
    route_suggestions: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)
    source_coverage: List[str] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)


class QuerySpotSummary(BaseModel):
    name: str
    keywords: List[str] = Field(default_factory=list)
    brief_desc: str = ""


class QueryAgentOutput(BaseModel):
    destination: str
    days: Optional[int] = None
    spots_summary: List[QuerySpotSummary] = Field(default_factory=list)
    raw_summary: str = ""
    sources: List[WebSource] = Field(default_factory=list)
    articles: List[ArticleContent] = Field(default_factory=list)
    extracted: ExtractedTravelInfo
    trace: List[AgentTrace] = Field(default_factory=list)


class ImageObservation(BaseModel):
    image_url: str
    labels: List[str] = Field(default_factory=list)
    ocr_text: str = ""
    scene_type: Literal["attraction", "restaurant", "street", "map", "hotel", "unknown"] = "unknown"
    inferred_location: Optional[str] = None
    description: str = ""
    confidence: float = 0.0


class ImageAgentOutput(BaseModel):
    destination: Optional[str] = None
    crawled_image_urls: List[str] = Field(default_factory=list)
    observations: List[ImageObservation] = Field(default_factory=list)
    trace: List[AgentTrace] = Field(default_factory=list)


class LocationPoint(BaseModel):
    longitude: Optional[float] = None
    latitude: Optional[float] = None


class HotelRecommendation(BaseModel):
    name: str = ""
    address: str = ""
    location: LocationPoint = Field(default_factory=LocationPoint)
    price_range: str = ""
    rating: str = ""
    distance: str = ""
    type: str = "舒适型酒店"
    estimated_cost: float = 0


class AttractionDetail(BaseModel):
    name: str
    address: str = ""
    location: LocationPoint = Field(default_factory=LocationPoint)
    visit_duration: int = 120
    description: str = ""
    category: str = "景点"
    ticket_price: float = 0


class MealPlan(BaseModel):
    type: Literal["breakfast", "lunch", "dinner"]
    name: str
    description: str = ""
    estimated_cost: float = 0


class DailyWeatherInfo(BaseModel):
    date: Optional[Date] = None
    day_weather: str = ""
    night_weather: str = ""
    day_temp: Optional[int] = None
    night_temp: Optional[int] = None
    wind_direction: str = ""
    wind_power: str = ""


class BudgetSummary(BaseModel):
    total_attractions: float = 0
    total_hotels: float = 0
    total_meals: float = 0
    total_transportation: float = 0
    total: float = 0


class DetailedDayPlan(BaseModel):
    date: Optional[Date] = None
    day_index: int = 0
    description: str = ""
    transportation: str = "地铁/步行/打车结合"
    accommodation: str = "舒适型酒店"
    hotel: HotelRecommendation = Field(default_factory=HotelRecommendation)
    attractions: List[AttractionDetail] = Field(default_factory=list)
    meals: List[MealPlan] = Field(default_factory=list)


class DetailedTravelPlan(BaseModel):
    city: str
    start_date: Optional[Date] = None
    end_date: Optional[Date] = None
    days: List[DetailedDayPlan] = Field(default_factory=list)
    weather_info: List[DailyWeatherInfo] = Field(default_factory=list)
    overall_suggestions: str = ""
    budget: BudgetSummary = Field(default_factory=BudgetSummary)


class AnalysisSpot(BaseModel):
    name: str
    type: str = ""
    desc: str = ""
    weather: str = ""
    hotel_nearby: List[str] = Field(default_factory=list)
    address: str = ""
    location: LocationPoint = Field(default_factory=LocationPoint)
    images: List[str] = Field(default_factory=list)
    status: Literal["推荐", "用户确认", "备选", "不推荐"] = "推荐"


class AnalysisAgentOutput(BaseModel):
    destination: str
    days: int
    spots: List[AnalysisSpot] = Field(default_factory=list)
    trace: List[AgentTrace] = Field(default_factory=list)


class PreferenceMemory(BaseModel):
    user_id: str
    preferences: List[str] = Field(default_factory=list)
    disliked: List[str] = Field(default_factory=list)
    budget_level: Optional[str] = None
    pace: Optional[str] = None
    notes: str = ""
    history_count: int = 0
    updated_at: Optional[datetime] = None


class PreferenceAgentOutput(BaseModel):
    memory: PreferenceMemory
    trace: List[AgentTrace] = Field(default_factory=list)


class TimeSlot(BaseModel):
    time: str
    title: str
    category: Literal["attraction", "food", "activity", "transport", "rest", "hotel"]
    description: str = ""
    estimated_cost: float = 0
    duration_minutes: int = 90
    source: Optional[str] = None


class DayItinerary(BaseModel):
    day: int
    date: Optional[Date] = None
    theme: str
    slots: List[TimeSlot] = Field(default_factory=list)
    route_notes: str = ""
    daily_budget: float = 0


class ItineraryPlan(BaseModel):
    trip_id: str
    destination: str
    days: int
    summary: str
    itinerary: List[DayItinerary] = Field(default_factory=list)
    highlights: List[str] = Field(default_factory=list)
    restaurants: List[str] = Field(default_factory=list)
    packing_tips: List[str] = Field(default_factory=list)
    risk_notes: List[str] = Field(default_factory=list)
    total_budget: Optional[float] = None
    source_references: List[str] = Field(default_factory=list)
    detailed_plan: Optional[DetailedTravelPlan] = None


class ReportArtifact(BaseModel):
    type: Literal["html", "pdf", "json"]
    path: str
    url: str
    generated_at: datetime


class ReportAgentOutput(BaseModel):
    html_report: str = ""
    structured_report: Dict[str, Any] = Field(default_factory=dict)
    modifiable_spots: List[str] = Field(default_factory=list)
    artifacts: List[ReportArtifact] = Field(default_factory=list)


class TravelPlanResponse(BaseModel):
    success: bool = True
    message: str = ""
    total: Optional[TotalAgentOutput] = None
    strategy: Optional[StrategyAgentOutput] = None
    analysis: Optional[AnalysisAgentOutput] = None
    report: Optional[ReportAgentOutput] = None
    plan: ItineraryPlan
    query: QueryAgentOutput
    images: ImageAgentOutput
    preference: PreferenceMemory
    reports: List[ReportArtifact] = Field(default_factory=list)
    trace: List[AgentTrace] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    agents: List[str]


# ===== Conversation models =====

class ConversationCreateRequest(BaseModel):
    id: str
    title: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    destination: str = ""
    days: int = 0


class ConversationSummary(BaseModel):
    id: str
    title: str
    destination: Optional[str] = None
    days: Optional[int] = None
    created_at: str
    updated_at: str


class ConversationDetailResponse(BaseModel):
    id: str
    title: str
    messages: List[Dict[str, Any]]
    destination: str
    days: int
    created_at: str
    updated_at: str


# Resolve forward references
TripPlanRequest.model_rebuild()
