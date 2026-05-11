# coding: utf-8
"""AnalysisAgent: enriches candidate spots and builds the itinerary plan."""

from __future__ import annotations

import json
import re
from datetime import date as DateValue, timedelta
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.models.schemas import (
    AgentTrace,
    AnalysisAgentOutput,
    AnalysisSpot,
    AttractionDetail,
    BudgetSummary,
    DayItinerary,
    DailyWeatherInfo,
    DetailedDayPlan,
    DetailedTravelPlan,
    ExtractedTravelInfo,
    HotelRecommendation,
    ImageAgentOutput,
    ItineraryPlan,
    LocationPoint,
    MealPlan,
    PreferenceMemory,
    QueryAgentOutput,
    QuerySpotSummary,
    StrategyAgentOutput,
    TimeSlot,
    TripPlanRequest,
)
from ItineraryEngine.nodes.plan_builder import ItineraryBuilder
from AnalysisAgent.tools.external_api import AmapTool, DailyWeather, HotelInfo, UnsplashTool
from TravelCore.llm import create_agent_llm
from TravelCore.text import dedupe, stable_id, utc_now


_ITINERARY_PROMPT = """
你是行程规划专家。请根据以下信息，生成一份完整、个性化的{days}天{destination}旅行计划。

## 目的地攻略
攻略摘要：{summary}
候选景点：{attractions}
候选餐厅：{restaurants}
候选活动：{activities}
路线建议：{route_suggestions}
出行贴士：{tips}

## 用户偏好
用户偏好：{preferences}
不喜欢的：{disliked}
预算：{budget}
旅行风格：{travel_style}

## 天气信息
天气：{weather}

请严格按照以下 JSON 格式返回旅行计划：

```json
{{
    "city": "城市名称",
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "days": [
    {{
            "date": "YYYY-MM-DD",
            "day_index": 0,
            "description": "第1天行程概述",
            "transportation": "交通方式",
            "accommodation": "住宿类型",
            "hotel": {{
                "name": "酒店名称",
                "address": "酒店地址",
                "location": {{"longitude": 116.397128, "latitude": 39.916527}},
                "price_range": "300-500元",
                "rating": "4.5",
                "distance": "距离景点2公里",
                "type": "经济型酒店",
                "estimated_cost": 400
            }},
            "attractions": [
        {{
                    "name": "景点名称",
                    "address": "详细地址",
                    "location": {{"longitude": 116.397128, "latitude": 39.916527}},
                    "visit_duration": 120,
                    "description": "景点详细描述",
                    "category": "景点类别",
                    "ticket_price": 60
        }}
      ],
            "meals": [
                {{"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30}},
                {{"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50}},
                {{"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}}
            ]
    }}
  ],
    "weather_info": [
        {{
            "date": "YYYY-MM-DD",
            "day_weather": "晴",
            "night_weather": "多云",
            "day_temp": 25,
            "night_temp": 15,
            "wind_direction": "南风",
            "wind_power": "1-3级"
        }}
    ],
    "overall_suggestions": "总体建议",
    "budget": {{
        "total_attractions": 180,
        "total_hotels": 1200,
        "total_meals": 480,
        "total_transportation": 200,
        "total": 2060
    }}
}}
```

重要约束：
1. weather_info 必须覆盖每一天，长度等于 {days}
2. day_temp/night_temp 必须是数字，不要带单位
3. 每天安排 2-3 个景点，且尽量按地理邻近性排序
4. 每天必须包含早中晚三餐
5. hotel 字段必须完整
6. budget 必须完整且 total = 各项和
7. 仅输出 JSON，不要输出额外解释、Markdown 或代码块
"""


class AnalysisAgent:
    """
    Analysis Agent (分析 Agent) - SiliconFlow Qwen2.5-32B-Instruct

    职责：
    - 分析 QueryAgent 的搜索结果
    - 用高德地图/天气/Unsplash 丰富景点信息
    - 用 LLM 生成完整行程安排（含个性化描述）
    - 筛选和排序景点

    输入：QueryAgentOutput / StrategyAgentOutput
    输出：(AnalysisAgentOutput, ItineraryPlan, trace)
    """

    name = "AnalysisAgent"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm: Optional[Any] = None,
        builder: Optional[ItineraryBuilder] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or create_agent_llm(self.name, self.settings)
        self.builder = builder or ItineraryBuilder()
        self.amap_tool = AmapTool(self.settings)
        self.unsplash_tool = UnsplashTool(self.settings)

    async def run(
        self,
        request: TripPlanRequest,
        strategy: StrategyAgentOutput | QueryAgentOutput,
        query: QueryAgentOutput | ImageAgentOutput | None = None,
        images: ImageAgentOutput | PreferenceMemory | None = None,
        preference: PreferenceMemory | None = None,
        previous_plan=None,
        modification_text: str = "",
        thinking_callback=None,
    ) -> tuple[AnalysisAgentOutput, ItineraryPlan, list[AgentTrace]]:
        """
        主分析流程：
        1. 解析输入
        2. 查询天气
        3. 构建景点列表（含地址/酒店/图片）
        4. 用 LLM 生成完整行程
        5. 返回 AnalysisAgentOutput + ItineraryPlan + trace
        """
        started = utc_now()

        # --- 修改模式 ---
        if previous_plan is not None and modification_text:
            return await self._modify_plan(
                request, previous_plan, modification_text, started, thinking_callback,
            )

        # --- 正常模式 ---
        legacy_call = isinstance(strategy, QueryAgentOutput)

        if legacy_call:
            query_output = strategy
            image_output = (
                query if isinstance(query, ImageAgentOutput)
                else ImageAgentOutput(destination=request.destination)
            )
            preference_memory = (
                images if isinstance(images, PreferenceMemory)
                else PreferenceMemory(user_id=request.user_id)
            )
            scenic_types = request.preferences or ["综合"]
        else:
            query_output = (
                query if isinstance(query, QueryAgentOutput)
                else QueryAgentOutput(
                    destination=request.destination,
                    extracted=self._empty_extraction(request.destination),
                )
            )
            image_output = (
                images if isinstance(images, ImageAgentOutput)
                else ImageAgentOutput(destination=request.destination)
            )
            preference_memory = preference or PreferenceMemory(user_id=request.user_id)
            scenic_types = strategy.scenic_types or ["综合"]

        # 查询逐日天气；后续提示词、报告和预算结构都使用同一份天气数据
        weather_forecast = await self.amap_tool.get_weather_forecast(
            request.destination,
            request.start_date,
            request.days,
        )
        weather_text = self._format_weather_forecast(weather_forecast)

        # 构建景点列表
        spots = await self._build_spots(
            scenic_types, query_output, image_output, request, weather_text,
        )

        # LLM 生成行程
        plan = await self._generate_itinerary(
            request, query_output.extracted, image_output,
            preference_memory, weather_text,
        )
        plan = await self._attach_detailed_plan(
            request=request,
            plan=plan,
            spots=spots,
            weather_forecast=weather_forecast,
        )

        trace = [
            AgentTrace(
                agent=self.name,
                status="completed",
                message=(
                    f"分析了 {len(spots)} 个候选景点，生成了 "
                    f"{len(plan.itinerary)} 天行程安排。"
                ),
                started_at=started,
                finished_at=utc_now(),
                metadata={
                    "map_mcp": "已接入" if self.amap_tool.api_key else "未配置",
                    "unsplash": "已接入" if self.unsplash_tool.access_key else "未配置",
                    "weather": weather_text,
                    "model": self.llm.effective_model,
                },
            )
        ]

        warning_traces = self._build_weather_warning_traces(plan, request.days)
        trace.extend(warning_traces)

        return (
            AnalysisAgentOutput(
                destination=request.destination,
                days=request.days,
                spots=spots,
                trace=trace,
            ),
            plan,
            trace,
        )

    async def _generate_itinerary(
        self,
        request: TripPlanRequest,
        extracted: ExtractedTravelInfo,
        images: ImageAgentOutput,
        preference: PreferenceMemory,
        weather_text: str,
    ) -> ItineraryPlan:
        """用 LLM 生成完整行程，失败则回退到确定性 builder。"""
        try:
            return await self._llm_generate(
                request, extracted, images, preference, weather_text,
            )
        except Exception:
            return self.builder.build(
                request, extracted, images, preference,
            )

    async def _llm_generate(
        self,
        request: TripPlanRequest,
        extracted: ExtractedTravelInfo,
        images: ImageAgentOutput,
        preference: PreferenceMemory,
        weather_text: str,
    ) -> ItineraryPlan:
        """调用 LLM 生成行程 JSON，并验证/修复输出。"""
        budget_str = f"¥{request.budget:.0f}" if request.budget else "未指定"
        style = request.travel_style or (request.preferences[0] if request.preferences else "自由行")

        prompt = _ITINERARY_PROMPT.format(
            destination=request.destination,
            days=request.days,
            summary=extracted.summary or "",
            attractions="、".join(extracted.attractions),
            restaurants="、".join(extracted.restaurants),
            activities="、".join(extracted.activities),
            route_suggestions="；".join(extracted.route_suggestions),
            tips="；".join(extracted.tips),
            preferences="、".join(preference.preferences) or "无",
            disliked="、".join(preference.disliked) or "无",
            budget=budget_str,
            travel_style=style,
            weather=weather_text,
        )

        response = await self.llm.complete_text(
            [{"role": "user", "content": prompt}],
            fallback="",
            response_format={"type": "json_object"},
        )
        if not response:
            raise ValueError("LLM 返回为空")

        start = response.find("{")
        end = response.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("LLM 返回无有效 JSON")

        data = json.loads(response[start:end])
        return self._parse_itinerary(data, request, extracted)

    def _parse_itinerary(
        self,
        data: Dict[str, Any],
        request: TripPlanRequest,
        extracted: ExtractedTravelInfo,
    ) -> ItineraryPlan:
        """验证并解析 LLM 返回的行程 JSON，缺失字段用 fallback 补全。"""
        fallback = self.builder.build(
            request, extracted, ImageAgentOutput(destination=request.destination),
            PreferenceMemory(user_id=request.user_id),
        )

        # 优先兼容 planner-style 结构化输出（city/start_date/days/weather_info/budget）
        if self._is_planner_schema(data):
            return self._parse_planner_schema(data, request, extracted, fallback)

        raw_itinerary = data.get("itinerary", [])
        if not isinstance(raw_itinerary, list) or len(raw_itinerary) != request.days:
            return fallback

        itinerary: List[DayItinerary] = []
        used_visit_titles: set[str] = set()
        used_food_titles: set[str] = set()
        for i, day_data in enumerate(raw_itinerary):
            raw_slots = day_data.get("slots", [])
            if not isinstance(raw_slots, list) or not raw_slots:
                itinerary.append(fallback.itinerary[i])
                continue

            date_val = request.start_date + timedelta(days=i) if request.start_date else None
            slots: List[TimeSlot] = []
            fb_slots = fallback.itinerary[i].slots if i < len(fallback.itinerary) else []

            for si, slot in enumerate(raw_slots[:4]):
                if not isinstance(slot, dict):
                    slot = {}
                title = slot.get("title", fb_slots[si].title if si < len(fb_slots) else "待定")
                category = self._valid_category(slot.get("category", "attraction"))
                description = slot.get("description", "") or f"{title} 的游览安排。"
                estimated_cost = float(slot.get("estimated_cost", 0) or 0)
                duration_minutes = int(slot.get("duration_minutes", 90) or 90)

                if category in {"attraction", "activity"}:
                    replacement = self._replacement_slot(
                        title,
                        fb_slots,
                        used_visit_titles,
                        {"attraction", "activity"},
                    )
                    if replacement:
                        title = replacement.title
                        category = replacement.category
                        description = replacement.description
                        estimated_cost = replacement.estimated_cost
                        duration_minutes = replacement.duration_minutes
                    used_visit_titles.add(title)
                elif category == "food":
                    replacement = self._replacement_slot(
                        title,
                        fb_slots,
                        used_food_titles,
                        {"food"},
                    )
                    if replacement:
                        title = replacement.title
                        category = replacement.category
                        description = replacement.description
                        estimated_cost = replacement.estimated_cost
                        duration_minutes = replacement.duration_minutes
                    used_food_titles.add(title)

                slots.append(TimeSlot(
                    time=slot.get("time", ["09:00", "12:00", "14:00", "18:30"][si]),
                    title=title,
                    category=category,
                    description=description,
                    estimated_cost=estimated_cost,
                    duration_minutes=duration_minutes,
                ))

            while len(slots) < 4 and si + 1 < len(fb_slots):
                slots.append(fb_slots[len(slots)])

            itinerary.append(DayItinerary(
                day=i + 1,
                date=date_val,
                theme=day_data.get("theme", fallback.itinerary[i].theme),
                slots=slots,
                route_notes=day_data.get("route_notes", fallback.itinerary[i].route_notes),
                daily_budget=fallback.itinerary[i].daily_budget if i < len(fallback.itinerary) else 0,
            ))

        plan_id = stable_id(
            request.user_id, request.destination, request.days,
            request.start_date, request.free_text,
        )
        return ItineraryPlan(
            trip_id=plan_id,
            destination=request.destination,
            days=request.days,
            summary=data.get("summary", fallback.summary),
            itinerary=itinerary,
            highlights=dedupe(data.get("highlights", fallback.highlights), 10),
            restaurants=dedupe(data.get("restaurants", fallback.restaurants), 8),
            packing_tips=dedupe(data.get("packing_tips", fallback.packing_tips), 10),
            risk_notes=data.get("risk_notes", fallback.risk_notes),
            total_budget=request.budget,
            source_references=extracted.source_urls,
        )

    @staticmethod
    def _is_planner_schema(data: Dict[str, Any]) -> bool:
        return isinstance(data.get("days"), list) and (
            "city" in data or "weather_info" in data or "budget" in data
        )

    def _parse_planner_schema(
        self,
        data: Dict[str, Any],
        request: TripPlanRequest,
        extracted: ExtractedTravelInfo,
        fallback: ItineraryPlan,
    ) -> ItineraryPlan:
        raw_days = data.get("days", [])
        if not isinstance(raw_days, list) or not raw_days:
            return fallback

        detailed_days: List[DetailedDayPlan] = []
        itinerary: List[DayItinerary] = []
        restaurants: List[str] = []

        for index, day in enumerate(raw_days[: request.days]):
            if not isinstance(day, dict):
                continue

            day_date = self._parse_date(day.get("date"))
            attractions = self._parse_attractions(day.get("attractions", []))
            hotel = self._parse_hotel(day.get("hotel", {}), request)
            meals = self._parse_meals(day.get("meals", []), request.destination)

            detailed_days.append(
                DetailedDayPlan(
                    date=day_date,
                    day_index=int(day.get("day_index", index)),
                    description=str(day.get("description", f"第{index + 1}天行程")),
                    transportation=str(day.get("transportation", "地铁/步行/打车结合")),
                    accommodation=str(day.get("accommodation", hotel.type or "舒适型酒店")),
                    hotel=hotel,
                    attractions=attractions,
                    meals=meals,
                )
            )

            for meal in meals:
                if meal.name:
                    restaurants.append(meal.name)

            # 转成现有前端兼容的 time-slot 结构
            slots: List[TimeSlot] = []
            if attractions:
                first = attractions[0]
                slots.append(
                    TimeSlot(
                        time="09:00",
                        title=first.name,
                        category="attraction",
                        description=first.description,
                        estimated_cost=first.ticket_price,
                        duration_minutes=first.visit_duration,
                    )
                )
            lunch = next((m for m in meals if m.type == "lunch"), None)
            if lunch:
                slots.append(
                    TimeSlot(
                        time="12:00",
                        title=lunch.name,
                        category="food",
                        description=lunch.description,
                        estimated_cost=lunch.estimated_cost,
                        duration_minutes=75,
                    )
                )
            if len(attractions) > 1:
                second = attractions[1]
                slots.append(
                    TimeSlot(
                        time="14:00",
                        title=second.name,
                        category="attraction",
                        description=second.description,
                        estimated_cost=second.ticket_price,
                        duration_minutes=second.visit_duration,
                    )
                )
            dinner = next((m for m in meals if m.type == "dinner"), None)
            if dinner:
                slots.append(
                    TimeSlot(
                        time="18:30",
                        title=dinner.name,
                        category="food",
                        description=dinner.description,
                        estimated_cost=dinner.estimated_cost,
                        duration_minutes=90,
                    )
                )

            itinerary.append(
                DayItinerary(
                    day=index + 1,
                    date=day_date,
                    theme=str(day.get("description", f"第{index + 1}天行程"))[:30],
                    slots=slots if slots else fallback.itinerary[index].slots,
                    route_notes=str(day.get("transportation", "按地理邻近性安排行程")),
                    daily_budget=float(day.get("daily_budget", fallback.itinerary[index].daily_budget if index < len(fallback.itinerary) else 0) or 0),
                )
            )

        if not detailed_days:
            return fallback

        weather_info = self._parse_weather_info(data.get("weather_info", []), request, detailed_days)
        budget = self._parse_budget(data.get("budget", {}), detailed_days)

        start_date = self._parse_date(data.get("start_date")) or request.start_date or detailed_days[0].date
        end_date = self._parse_date(data.get("end_date"))
        if not end_date and start_date and request.days:
            end_date = start_date + timedelta(days=request.days - 1)

        plan = ItineraryPlan(
            trip_id=fallback.trip_id,
            destination=str(data.get("city") or request.destination),
            days=request.days,
            summary=str(data.get("overall_suggestions") or fallback.summary),
            itinerary=itinerary,
            highlights=dedupe([a.name for d in detailed_days for a in d.attractions], 10),
            restaurants=dedupe(restaurants, 8),
            packing_tips=fallback.packing_tips,
            risk_notes=fallback.risk_notes,
            total_budget=budget.total,
            source_references=extracted.source_urls,
        )

        raw_weather = data.get("weather_info", [])
        if not isinstance(raw_weather, list):
            raw_weather = []
        if len(raw_weather) != request.days:
            plan.risk_notes = dedupe(
                [
                    f"⚠️ weather_info 长度为 {len(raw_weather)}，已自动补齐到 {request.days} 天。",
                    *plan.risk_notes,
                ],
                12,
            )
        plan.detailed_plan = DetailedTravelPlan(
            city=str(data.get("city") or request.destination),
            start_date=start_date,
            end_date=end_date,
            days=detailed_days,
            weather_info=weather_info,
            overall_suggestions=str(data.get("overall_suggestions") or ""),
            budget=budget,
        )
        return plan

    @staticmethod
    def _parse_date(value: Any) -> Optional[DateValue]:
        if not value:
            return None
        if isinstance(value, DateValue):
            return value
        try:
            return DateValue.fromisoformat(str(value))
        except ValueError:
            return None

    def _parse_hotel(self, raw: Any, request: TripPlanRequest) -> HotelRecommendation:
        if not isinstance(raw, dict):
            raw = {}
        loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        return HotelRecommendation(
            name=str(raw.get("name", f"{request.destination}舒适型酒店")),
            address=str(raw.get("address", f"{request.destination}核心商圈")),
            location=LocationPoint(
                longitude=self._to_float(loc.get("longitude")),
                latitude=self._to_float(loc.get("latitude")),
            ),
            price_range=str(raw.get("price_range", "价格待查询")),
            rating=str(raw.get("rating", "待查询")),
            distance=str(raw.get("distance", "待查询")),
            type=str(raw.get("type", "舒适型酒店")),
            estimated_cost=self._to_float(raw.get("estimated_cost")) or 0,
        )

    def _parse_attractions(self, raw: Any) -> List[AttractionDetail]:
        if not isinstance(raw, list):
            return []
        items: List[AttractionDetail] = []
        for item in raw[:3]:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") if isinstance(item.get("location"), dict) else {}
            items.append(
                AttractionDetail(
                    name=str(item.get("name", "待定景点")),
                    address=str(item.get("address", "待查询")),
                    location=LocationPoint(
                        longitude=self._to_float(loc.get("longitude")),
                        latitude=self._to_float(loc.get("latitude")),
                    ),
                    visit_duration=int(self._to_float(item.get("visit_duration")) or 120),
                    description=str(item.get("description", "")),
                    category=str(item.get("category", "景点")),
                    ticket_price=self._to_float(item.get("ticket_price")) or 0,
                )
            )
        return items

    def _parse_meals(self, raw: Any, destination: str) -> List[MealPlan]:
        if not isinstance(raw, list):
            raw = []
        by_type: Dict[str, MealPlan] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            meal_type = str(item.get("type", "")).lower()
            if meal_type not in {"breakfast", "lunch", "dinner"}:
                continue
            by_type[meal_type] = MealPlan(
                type=meal_type,
                name=str(item.get("name", f"{destination}{meal_type}")),
                description=str(item.get("description", "")),
                estimated_cost=self._to_float(item.get("estimated_cost")) or 0,
            )

        defaults = {
            "breakfast": MealPlan(type="breakfast", name=f"{destination}早餐", description="当地特色早餐", estimated_cost=30),
            "lunch": MealPlan(type="lunch", name=f"{destination}午餐", description="午餐推荐", estimated_cost=50),
            "dinner": MealPlan(type="dinner", name=f"{destination}晚餐", description="晚餐推荐", estimated_cost=80),
        }
        return [by_type.get("breakfast", defaults["breakfast"]), by_type.get("lunch", defaults["lunch"]), by_type.get("dinner", defaults["dinner"])]

    def _parse_weather_info(
        self,
        raw: Any,
        request: TripPlanRequest,
        detailed_days: List[DetailedDayPlan],
    ) -> List[DailyWeatherInfo]:
        rows: List[DailyWeatherInfo] = []
        if isinstance(raw, list):
            for index, item in enumerate(raw[: request.days]):
                if not isinstance(item, dict):
                    continue
                rows.append(
                    DailyWeatherInfo(
                        date=self._parse_date(item.get("date")) or detailed_days[min(index, len(detailed_days) - 1)].date,
                        day_weather=str(item.get("day_weather", "待查询")),
                        night_weather=str(item.get("night_weather", "待查询")),
                        day_temp=self._to_int(item.get("day_temp")),
                        night_temp=self._to_int(item.get("night_temp")),
                        wind_direction=str(item.get("wind_direction", "")),
                        wind_power=str(item.get("wind_power", "")),
                    )
                )

        # 补齐到 request.days
        while len(rows) < request.days:
            idx = len(rows)
            rows.append(
                DailyWeatherInfo(
                    date=detailed_days[min(idx, len(detailed_days) - 1)].date,
                    day_weather="待查询",
                    night_weather="待查询",
                )
            )
        return rows

    def _parse_budget(self, raw: Any, detailed_days: List[DetailedDayPlan]) -> BudgetSummary:
        if isinstance(raw, dict):
            base = BudgetSummary(
                total_attractions=self._to_float(raw.get("total_attractions")) or 0,
                total_hotels=self._to_float(raw.get("total_hotels")) or 0,
                total_meals=self._to_float(raw.get("total_meals")) or 0,
                total_transportation=self._to_float(raw.get("total_transportation")) or 0,
                total=self._to_float(raw.get("total")) or 0,
            )
            if base.total <= 0:
                base.total = round(
                    base.total_attractions + base.total_hotels + base.total_meals + base.total_transportation,
                    2,
                )
            return base
        return self._budget_summary(detailed_days)

    @staticmethod
    def _build_weather_warning_traces(plan: ItineraryPlan, expected_days: int) -> List[AgentTrace]:
        warnings = [note for note in plan.risk_notes if str(note).startswith("⚠️")]
        if not warnings:
            detail = plan.detailed_plan
            if detail and len(detail.weather_info) != expected_days:
                warnings = [
                    f"⚠️ weather_info 长度为 {len(detail.weather_info)}，与 days={expected_days} 不一致，已自动补齐。"
                ]
        return [
            AgentTrace(
                agent="AnalysisAgent",
                status="failed",
                message=warning,
                started_at=utc_now(),
                finished_at=utc_now(),
                metadata={"severity": "warning", "auto_fill": "weather_info"},
            )
            for warning in warnings
        ]

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value)
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        number = AnalysisAgent._to_float(value)
        return int(round(number)) if number is not None else None

    @staticmethod
    def _valid_category(value: str) -> str:
        valid = {"attraction", "food", "activity", "transport", "rest", "hotel"}
        return value if value in valid else "attraction"

    @staticmethod
    def _replacement_slot(
        title: str,
        fallback_slots: List[TimeSlot],
        used_titles: set[str],
        allowed_categories: set[str],
    ) -> Optional[TimeSlot]:
        if title not in used_titles:
            return None
        return next(
            (
                slot
                for slot in fallback_slots
                if slot.category in allowed_categories and slot.title not in used_titles
            ),
            None,
        )

    @staticmethod
    def _format_weather_forecast(forecast: List[DailyWeather]) -> str:
        if not forecast:
            return "待查询"
        chunks = []
        for index, weather in enumerate(forecast, start=1):
            temps = []
            if weather.day_temp is not None:
                temps.append(f"白天{weather.day_temp}度")
            if weather.night_temp is not None:
                temps.append(f"夜间{weather.night_temp}度")
            temp_text = "，".join(temps) if temps else "温度待查询"
            wind = ""
            if weather.wind_direction or weather.wind_power:
                wind = f"，{weather.wind_direction}{weather.wind_power}"
            chunks.append(
                f"第{index}天({weather.date.isoformat()}): "
                f"白天{weather.day_weather}，夜间{weather.night_weather}，{temp_text}{wind}"
            )
        return "；".join(chunks)

    async def _attach_detailed_plan(
        self,
        request: TripPlanRequest,
        plan: ItineraryPlan,
        spots: List[AnalysisSpot],
        weather_forecast: List[DailyWeather],
    ) -> ItineraryPlan:
        spot_map = {spot.name: spot for spot in spots}
        accommodation = self._accommodation_type(request)
        detailed_days: List[DetailedDayPlan] = []

        for index, day in enumerate(plan.itinerary):
            weather = weather_forecast[index] if index < len(weather_forecast) else None
            route_note = self._weather_route_note(weather)
            if route_note and route_note not in day.route_notes:
                day.route_notes = f"{day.route_notes} {route_note}".strip()

            attractions = self._build_attraction_details(day, spot_map)
            hotel = await self._hotel_for_day(request, attractions, accommodation)
            meals = self._build_meals(request, day, plan.restaurants, index)
            day_date = day.date or (weather.date if weather else None)
            detailed_days.append(
                DetailedDayPlan(
                    date=day_date,
                    day_index=index,
                    description=self._day_description(index, day, weather),
                    transportation=self._transportation_for_weather(weather),
                    accommodation=accommodation,
                    hotel=hotel,
                    attractions=attractions,
                    meals=meals,
                )
            )

        weather_info = [
            self._weather_to_schema(item)
            for item in weather_forecast[: request.days]
        ]
        budget = self._budget_summary(detailed_days)
        start_date = (
            request.start_date
            or (weather_forecast[0].date if weather_forecast else None)
            or (detailed_days[0].date if detailed_days else None)
        )
        end_date = start_date + timedelta(days=request.days - 1) if start_date and request.days else None
        overall_suggestions = self._overall_suggestions(weather_forecast, plan)
        plan.detailed_plan = DetailedTravelPlan(
            city=request.destination,
            start_date=start_date,
            end_date=end_date,
            days=detailed_days,
            weather_info=weather_info,
            overall_suggestions=overall_suggestions,
            budget=budget,
        )
        if overall_suggestions and overall_suggestions not in plan.risk_notes:
            plan.risk_notes = dedupe(plan.risk_notes + [overall_suggestions], 12)
        return plan

    @staticmethod
    def _weather_to_schema(weather: DailyWeather) -> DailyWeatherInfo:
        return DailyWeatherInfo(
            date=weather.date,
            day_weather=weather.day_weather,
            night_weather=weather.night_weather,
            day_temp=weather.day_temp,
            night_temp=weather.night_temp,
            wind_direction=weather.wind_direction,
            wind_power=weather.wind_power,
        )

    def _build_attraction_details(
        self,
        day: DayItinerary,
        spot_map: Dict[str, AnalysisSpot],
    ) -> List[AttractionDetail]:
        details: List[AttractionDetail] = []
        for slot in day.slots:
            if slot.category not in {"attraction", "activity"}:
                continue
            spot = spot_map.get(slot.title)
            details.append(
                AttractionDetail(
                    name=slot.title,
                    address=(spot.address if spot else "") or "待查询",
                    location=(spot.location if spot else LocationPoint()),
                    visit_duration=slot.duration_minutes,
                    description=slot.description or (spot.desc if spot else ""),
                    category=(spot.type if spot else slot.category) or "景点",
                    ticket_price=self._estimate_ticket_price(slot.title, slot.category),
                )
            )
            if len(details) >= 3:
                break
        return details

    async def _hotel_for_day(
        self,
        request: TripPlanRequest,
        attractions: List[AttractionDetail],
        accommodation: str,
    ) -> HotelRecommendation:
        location = next(
            (
                item.location
                for item in attractions
                if item.location.longitude is not None and item.location.latitude is not None
            ),
            None,
        )
        if location:
            hotels = await self.amap_tool.get_hotels_nearby(
                f"{location.longitude},{location.latitude}",
                limit=1,
            )
            if hotels:
                return self._hotel_to_schema(hotels[0], accommodation)

        estimated_cost = self._default_hotel_cost(accommodation)
        return HotelRecommendation(
            name=f"{request.destination}{accommodation}推荐",
            address=f"{request.destination}核心商圈或地铁沿线",
            price_range=f"{int(estimated_cost)}元左右",
            rating="待查询",
            distance="建议选择距离当日核心景点30分钟交通圈内",
            type=accommodation,
            estimated_cost=estimated_cost,
        )

    def _hotel_to_schema(self, hotel: HotelInfo, accommodation: str) -> HotelRecommendation:
        estimated_cost = self._parse_price(hotel.price_range) or self._default_hotel_cost(accommodation)
        price_range = hotel.price_range
        if price_range and price_range != "未知" and "元" not in price_range:
            price_range = f"{price_range}元左右"
        return HotelRecommendation(
            name=hotel.name,
            address=hotel.address,
            location=self._parse_location(hotel.location),
            price_range=price_range or f"{int(estimated_cost)}元左右",
            rating=str(hotel.rating) if hotel.rating else "待查询",
            distance=hotel.distance,
            type=accommodation,
            estimated_cost=estimated_cost,
        )

    @staticmethod
    def _build_meals(
        request: TripPlanRequest,
        day: DayItinerary,
        restaurants: List[str],
        day_index: int,
    ) -> List[MealPlan]:
        food_slots = [slot for slot in day.slots if slot.category == "food"]
        lunch = food_slots[0] if food_slots else None
        dinner = food_slots[1] if len(food_slots) > 1 else None
        breakfast_name = (
            restaurants[(day_index * 2) % len(restaurants)]
            if restaurants else f"{request.destination}特色早餐"
        )
        return [
            MealPlan(
                type="breakfast",
                name=breakfast_name,
                description="安排在酒店或附近街区，优先体验当地早餐。",
                estimated_cost=30,
            ),
            MealPlan(
                type="lunch",
                name=lunch.title if lunch else f"{request.destination}本地午餐",
                description=(lunch.description if lunch else "选择靠近上午景点的餐厅，减少折返。"),
                estimated_cost=(lunch.estimated_cost if lunch and lunch.estimated_cost else 50),
            ),
            MealPlan(
                type="dinner",
                name=dinner.title if dinner else f"{request.destination}本地晚餐",
                description=(dinner.description if dinner else "结合夜游或商圈动线安排晚餐。"),
                estimated_cost=(dinner.estimated_cost if dinner and dinner.estimated_cost else 80),
            ),
        ]

    @staticmethod
    def _budget_summary(days: List[DetailedDayPlan]) -> BudgetSummary:
        total_attractions = sum(
            attraction.ticket_price
            for day in days
            for attraction in day.attractions
        )
        total_hotels = sum(day.hotel.estimated_cost for day in days)
        total_meals = sum(meal.estimated_cost for day in days for meal in day.meals)
        total_transportation = 60 * len(days)
        return BudgetSummary(
            total_attractions=round(total_attractions, 2),
            total_hotels=round(total_hotels, 2),
            total_meals=round(total_meals, 2),
            total_transportation=round(total_transportation, 2),
            total=round(total_attractions + total_hotels + total_meals + total_transportation, 2),
        )

    @staticmethod
    def _parse_location(value: str) -> LocationPoint:
        try:
            longitude, latitude = value.split(",", 1)
            return LocationPoint(longitude=float(longitude), latitude=float(latitude))
        except (AttributeError, ValueError):
            return LocationPoint()

    @staticmethod
    def _parse_price(value: str) -> float:
        numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
        if not numbers:
            return 0
        return round(sum(numbers[:2]) / min(len(numbers), 2), 2)

    @staticmethod
    def _default_hotel_cost(accommodation: str) -> float:
        if any(word in accommodation for word in ("豪华", "高端", "五星")):
            return 800
        if "经济" in accommodation:
            return 300
        if "民宿" in accommodation:
            return 350
        return 450

    @staticmethod
    def _accommodation_type(request: TripPlanRequest) -> str:
        details = request.preference_details or {}
        value = (
            details.get("酒店级别")
            or details.get("hotel_level")
            or details.get("住宿类型")
            or details.get("accommodation")
            or ""
        )
        text = str(value)
        if any(word in text for word in ("豪华", "高端", "五星")):
            return "高端酒店"
        if "经济" in text:
            return "经济型酒店"
        if "民宿" in text:
            return "民宿"
        return "舒适型酒店"

    @staticmethod
    def _estimate_ticket_price(name: str, category: str) -> float:
        free_hints = ("街", "巷", "路", "桥", "公园", "广场", "商圈", "步行街", "绿道")
        if any(hint in name for hint in free_hints):
            return 0
        if "轮渡" in name:
            return 10
        if category == "activity":
            return 60
        return 50

    @staticmethod
    def _weather_route_note(weather: Optional[DailyWeather]) -> str:
        if not weather:
            return ""
        if AnalysisAgent._has_bad_weather(weather):
            return "天气存在雨雪/大风/高温等不确定性，建议优先地铁或打车，并保留室内备选。"
        if "晴" in weather.day_weather:
            return "天气较适合户外游览，可安排登高、湖边或城市步行体验。"
        return "根据当天温度和风力灵活调整户外停留时长。"

    @staticmethod
    def _transportation_for_weather(weather: Optional[DailyWeather]) -> str:
        if weather and AnalysisAgent._has_bad_weather(weather):
            return "地铁/打车为主，减少长距离户外步行"
        return "地铁/步行/打车结合"

    @staticmethod
    def _day_description(index: int, day: DayItinerary, weather: Optional[DailyWeather]) -> str:
        weather_text = ""
        if weather:
            weather_text = f"，白天{weather.day_weather}"
            if weather.day_temp is not None:
                weather_text += f"{weather.day_temp}度"
        return f"第{index + 1}天以{day.theme}为主{weather_text}，按同区域顺路游览。"

    @staticmethod
    def _overall_suggestions(weather_forecast: List[DailyWeather], plan: ItineraryPlan) -> str:
        suggestions = ["出发前复核景点开放时间、门票预约和酒店取消政策。"]
        if any(AnalysisAgent._has_bad_weather(item) for item in weather_forecast):
            suggestions.append("行程中存在天气波动，建议准备雨具并预留室内备选点。")
        if plan.days >= 3:
            suggestions.append("多日行程建议每天保留30-60分钟机动时间，避免因排队和交通影响后续安排。")
        return " ".join(suggestions)

    @staticmethod
    def _has_bad_weather(weather: DailyWeather) -> bool:
        text = f"{weather.day_weather}{weather.night_weather}{weather.wind_power}"
        if any(word in text for word in ("雨", "雪", "雷", "雾", "沙", "大风", "阵风")):
            return True
        if weather.day_temp is not None and weather.day_temp >= 32:
            return True
        if weather.night_temp is not None and weather.night_temp <= 3:
            return True
        power_match = re.search(r"\d+", weather.wind_power or "")
        return bool(power_match and int(power_match.group(0)) >= 5)

    async def _build_spots(
        self,
        scenic_types: list[str],
        query: QueryAgentOutput,
        images: ImageAgentOutput,
        request: TripPlanRequest,
        weather_text: str = "待查询",
    ) -> list[AnalysisSpot]:
        """构建 enriched 景点列表（含地址/酒店/图片）。"""
        image_map = self._build_image_map(images)
        city = request.destination

        names = (
            [spot.name for spot in query.spots_summary]
            or query.extracted.attractions[: request.days * 2]
        )

        spots: list[AnalysisSpot] = []
        for index, name in enumerate(dedupe(names, 12)):
            summary = next(
                (spot for spot in query.spots_summary if spot.name == name), None
            )
            spot_type = self._determine_spot_type(
                summary.keywords if summary else [], scenic_types, index
            )

            address = "待查询"
            location = LocationPoint()
            place_info = await self.amap_tool.get_place_info(name, city)
            if place_info:
                address = place_info.address or f"{city}{name}"
                location = self._parse_location(place_info.location)

            hotels: list[str] = []
            if place_info and place_info.location:
                hotel_list = await self.amap_tool.get_hotels_nearby(
                    place_info.location, limit=3,
                )
                hotels = [h.name for h in hotel_list if h.name]

            spot_images = image_map.get(name, [])
            if not spot_images and self.unsplash_tool.access_key:
                spot_images = await self.unsplash_tool.get_place_images(name, city, limit=3)

            spots.append(
                AnalysisSpot(
                    name=name,
                    type=spot_type,
                    desc=(
                        summary.brief_desc if summary
                        else query.extracted.summary
                    ) or f"{name} 是候选旅行景点。",
                    weather=weather_text,
                    hotel_nearby=hotels if hotels else ["待查询"],
                    address=address,
                    location=location,
                    images=spot_images,
                    status=(
                        "用户确认"
                        if name and name in request.free_text
                        else "推荐"
                    ),
                )
            )

        return spots

    @staticmethod
    def _determine_spot_type(
        keywords: list[str],
        scenic_types: list[str],
        index: int,
    ) -> str:
        for keyword in keywords:
            if keyword in scenic_types:
                return keyword
        return (
            scenic_types[index % len(scenic_types)]
            if scenic_types
            else "综合"
        )

    @staticmethod
    def _build_image_map(images: ImageAgentOutput) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for observation in images.observations:
            if not observation.inferred_location:
                continue
            result.setdefault(observation.inferred_location, []).append(
                observation.image_url
            )
        return {name: dedupe(urls, 4) for name, urls in result.items()}

    @staticmethod
    def _empty_extraction(destination: str):
        return ExtractedTravelInfo(destination=destination)

    async def _modify_plan(
        self,
        request: TripPlanRequest,
        previous_plan: ItineraryPlan,
        modification_text: str,
        started,
        thinking_callback=None,
    ) -> tuple[AnalysisAgentOutput, ItineraryPlan, list[AgentTrace]]:
        """修改已有行程。"""
        plan_summary = "; ".join(
            f"Day{d.day}: {', '.join(s.title for s in d.slots)}"
            for d in previous_plan.itinerary
        )
        prompt = (
            f"用户有一份已有的旅行计划，想根据以下意见进行修改：\n\n"
            f"修改意见：{modification_text}\n\n"
            f"原计划：{plan_summary}\n\n"
            f"请分析修改意见，返回修改后的景点和餐厅名称列表。\n"
            f"以 JSON 格式返回：{{\"attractions\": [\"景点1\", \"景点2\"], \"restaurants\": [\"餐厅1\"], \"notes\": \"修改说明\"}}"
        )

        try:
            response = await self.llm.complete_text(
                [{"role": "user", "content": prompt}],
                fallback=json.dumps({"attractions": [], "restaurants": [], "notes": modification_text}, ensure_ascii=False),
                response_format={"type": "json_object"},
            )
            content = response if isinstance(response, str) else str(response)
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(content[json_start:json_end])
                new_attractions = parsed.get("attractions", [])
                new_restaurants = parsed.get("restaurants", [])
                notes = parsed.get("notes", "")
            else:
                new_attractions, new_restaurants, notes = [], [], content[:200]
        except Exception:
            new_attractions, new_restaurants, notes = [], [], "自动调整"

        old_attractions = [
            s.title for d in previous_plan.itinerary for s in d.slots
            if s.category == "attraction"
        ]
        old_restaurants = [
            s.title for d in previous_plan.itinerary for s in d.slots
            if s.category == "food"
        ]
        old_activities = [
            s.title for d in previous_plan.itinerary for s in d.slots
            if s.category == "activity"
        ]

        attractions = dedupe(new_attractions, 12) or dedupe(old_attractions, 12)
        restaurants = dedupe(new_restaurants, 8) or dedupe(old_restaurants, 8)

        image_output = ImageAgentOutput(destination=request.destination, observations=[])
        extracted = ExtractedTravelInfo(
            destination=request.destination,
            summary=notes or previous_plan.summary,
            attractions=attractions,
            restaurants=restaurants,
            activities=old_activities[:4],
            route_suggestions=[],
            tips=previous_plan.packing_tips,
            source_urls=previous_plan.source_references,
        )

        weather_forecast = await self.amap_tool.get_weather_forecast(
            request.destination,
            request.start_date,
            request.days,
        )
        plan = await self._generate_itinerary(
            request, extracted, image_output,
            PreferenceMemory(user_id=request.user_id),
            weather_text=self._format_weather_forecast(weather_forecast),
        )

        spots = [
            AnalysisSpot(name=name, type="attraction", desc=name,
                        weather="待查询", hotel_nearby=[], address="",
                        images=[], status="推荐")
            for name in attractions[:8]
        ]
        plan = await self._attach_detailed_plan(
            request=request,
            plan=plan,
            spots=spots,
            weather_forecast=weather_forecast,
        )

        trace = [
            AgentTrace(
                agent=self.name, status="completed",
                message=f"修改计划：{notes[:100]}",
                started_at=started, finished_at=utc_now(),
                metadata={"modification": True, "spot_count": len(spots)},
            )
        ]
        return (
            AnalysisAgentOutput(
                destination=request.destination, days=request.days, spots=spots, trace=trace,
            ),
            plan,
            trace,
        )


# Backward compatibility alias
ItineraryAgent = AnalysisAgent
