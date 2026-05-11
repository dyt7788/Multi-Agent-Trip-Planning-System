"""Deterministic itinerary construction node."""

from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from app.models.schemas import (
    DayItinerary,
    ExtractedTravelInfo,
    ImageAgentOutput,
    ItineraryPlan,
    PreferenceMemory,
    TimeSlot,
    TripPlanRequest,
)
from TravelCore.text import dedupe, stable_id

# 通用 slot 描述模板（仅在无具体描述时使用）
_SLOT_DESC = {
    "morning": [
        "清晨游览{spot}，避开人流并留足拍照时间。",
        "上午安排{spot}，适合深度参观和慢节奏体验。",
        "先到{spot}打底，体力充沛时完成核心游览。",
    ],
    "lunch": [
        "午餐选择{spot}，衔接上午路线减少折返。",
        "在{spot}补充体力，顺路体验本地风味。",
        "{spot}适合中午短暂停留，控制用餐时间。",
    ],
    "afternoon": [
        "下午前往{spot}，和上午路线形成互补体验。",
        "{spot}安排在午后，节奏更松弛也便于避峰。",
        "午后游览{spot}，丰富当天的城市层次。",
    ],
    "dinner": [
        "晚间去{spot}，用餐后可顺带感受夜色。",
        "{spot}适合作为当天收尾，节奏轻松不赶路。",
        "晚上安排{spot}，把餐饮和夜游体验结合起来。",
    ],
}


class ItineraryBuilder:
    def build(
        self,
        request: TripPlanRequest,
        extracted: ExtractedTravelInfo,
        images: ImageAgentOutput,
        preference: PreferenceMemory,
        spot_descriptions: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> ItineraryPlan:
        attractions = dedupe(
            extracted.attractions
            + [
                obs.inferred_location
                for obs in images.observations
                if obs.scene_type == "attraction" and obs.inferred_location
            ],
            24,
        )
        restaurants = dedupe(extracted.restaurants, 16)
        activities = dedupe(extracted.activities, 16)
        route_suggestions = extracted.route_suggestions or ["按地理邻近原则安排同日路线。"]
        tips = dedupe(extracted.tips, 12)

        daily_budget = round(float(request.budget or 0) / request.days, 2) if request.budget else 0
        itinerary: List[DayItinerary] = []
        used_spots: set[str] = set()
        used_food: set[str] = set()
        for day in range(1, request.days + 1):
            date_value = request.start_date + timedelta(days=day - 1) if request.start_date else None
            morning = self._pick_unique(attractions, used_spots, f"{request.destination}核心景点")
            afternoon = self._pick_unique(
                attractions + activities,
                used_spots,
                f"{request.destination}城市体验",
            )
            lunch = self._pick_unique(restaurants, used_food, f"{request.destination}本地午餐")
            dinner = self._pick_unique(restaurants, used_food, f"{request.destination}晚间餐饮")
            theme = self._theme(day, request, preference, morning, afternoon)
            slots = [
                TimeSlot(
                    time="09:00",
                    title=morning,
                    category="attraction",
                    description=self._slot_desc("morning", morning, spot_descriptions, extracted),
                    estimated_cost=self._slot_cost(daily_budget, 0.22),
                    duration_minutes=150,
                ),
                TimeSlot(
                    time="12:00",
                    title=lunch,
                    category="food",
                    description=self._slot_desc("lunch", lunch, spot_descriptions, extracted),
                    estimated_cost=self._slot_cost(daily_budget, 0.15),
                    duration_minutes=75,
                ),
                TimeSlot(
                    time="14:00",
                    title=afternoon,
                    category="activity" if afternoon in activities else "attraction",
                    description=self._slot_desc("afternoon", afternoon, spot_descriptions, extracted),
                    estimated_cost=self._slot_cost(daily_budget, 0.25),
                    duration_minutes=180,
                ),
                TimeSlot(
                    time="18:30",
                    title=dinner,
                    category="food" if dinner in restaurants else "activity",
                    description=self._slot_desc("dinner", dinner, spot_descriptions, extracted),
                    estimated_cost=self._slot_cost(daily_budget, 0.18),
                    duration_minutes=120,
                ),
            ]
            itinerary.append(
                DayItinerary(
                    day=day,
                    date=date_value,
                    theme=theme,
                    slots=slots,
                    route_notes=route_suggestions[(day - 1) % len(route_suggestions)],
                    daily_budget=daily_budget,
                )
            )

        highlights = dedupe(attractions[:6] + activities[:4], 10)
        packing_tips = tips[:5] + self._image_tips(images)
        risk_notes = self._risk_notes(request, images)
        plan_id = stable_id(request.user_id, request.destination, request.days, request.start_date, request.free_text)
        summary = (
            f"{request.destination}{request.days}天旅行计划，综合攻略检索、图片分析和用户偏好生成。"
        )
        return ItineraryPlan(
            trip_id=plan_id,
            destination=request.destination,
            days=request.days,
            summary=summary,
            itinerary=itinerary,
            highlights=highlights,
            restaurants=restaurants[:8],
            packing_tips=dedupe(packing_tips, 10),
            risk_notes=risk_notes,
            total_budget=request.budget,
            source_references=extracted.source_urls,
        )

    @staticmethod
    def _slot_desc(
        period: str,
        spot_name: str,
        spot_descs: Optional[Dict[str, Dict[str, str]]],
        extracted: ExtractedTravelInfo,
    ) -> str:
        """生成个性化 slot 描述。优先使用 LLM 生成的景点描述，否则用模板。"""
        if spot_descs and spot_name in spot_descs:
            info = spot_descs[spot_name]
            desc = info.get(period, "")
            if desc and desc.strip():
                return desc
        templates = _SLOT_DESC.get(period)
        if not templates:
            return f"根据攻略信息安排{spot_name}的游览。"
        index = sum(ord(char) for char in f"{period}:{spot_name}") % len(templates)
        return templates[index].format(spot=spot_name)

    @staticmethod
    def _pick_unique(items: List[str], used: set[str], fallback: str) -> str:
        for item in items:
            if item and item not in used:
                used.add(item)
                return item

        candidate = fallback
        suffix = 2
        while candidate in used:
            candidate = f"{fallback}{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _slot_cost(daily_budget: float, ratio: float) -> float:
        return round(daily_budget * ratio, 2) if daily_budget else 0

    @staticmethod
    def _theme(
        day: int,
        request: TripPlanRequest,
        preference: PreferenceMemory,
        morning: str,
        afternoon: str,
    ) -> str:
        prefs = dedupe(request.preferences + preference.preferences, 3)
        if prefs:
            focus = prefs[(day - 1) % len(prefs)]
            return f"Day {day}: {focus}路线 - {morning}到{afternoon}"
        return f"Day {day}: {morning} + {afternoon}"

    @staticmethod
    def _image_tips(images: ImageAgentOutput) -> List[str]:
        if any(obs.scene_type == "map" for obs in images.observations):
            return ["图片中包含地图/路线信息，建议出行前再次核对导航时间。"]
        if any(obs.scene_type == "restaurant" for obs in images.observations):
            return ["图片中出现餐饮线索，可把识别出的餐厅加入备选清单。"]
        return []

    @staticmethod
    def _risk_notes(request: TripPlanRequest, images: ImageAgentOutput) -> List[str]:
        notes = ["真实营业时间、门票和预约规则会变化，出发前需要二次确认。"]
        if request.days >= 7:
            notes.append("长线旅行建议每3天安排一次低强度半日，降低疲劳累积。")
        if not images.observations and (request.image_urls or request.xhs_post_urls):
            notes.append("图片链接未能成功分析，可能受登录、反爬或图片权限影响。")
        return notes
