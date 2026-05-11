"""End-to-end test for the multi-agent travel planning system (non-interactive)."""

import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.config import get_settings
from app.models.schemas import TripPlanRequest
from TotalAgent.agent import TotalAgent
from Memory import Memory


async def test_full_flow():
    """Test the complete travel planning pipeline."""

    print("=" * 60)
    print("Full Flow Test: Multi-Agent Travel Planning")
    print("=" * 60)

    settings = get_settings()

    print("\n[Config]")
    print(f"  SiliconFlow API Key: {'set' if settings.siliconflow_api_key else 'NOT SET'}")
    print(f"  Tavily API Key: {'set' if settings.tavily_api_key else 'NOT SET'}")
    print(f"  Amap API Key: {'set' if settings.amap_api_key else 'NOT SET'}")
    print(f"  Unsplash Key: {'set' if settings.unsplash_access_key else 'NOT SET'}")

    if not settings.siliconflow_api_key:
        print("\nWARNING: SILICONFLOW_API_KEY not set, LLM calls will fail")

    request = TripPlanRequest(
        user_id="test_user_001",
        destination="苏州",
        days=2,
        preferences={
            "景点类型": ["历史", "园林", "自然"],
            "预算": "中等",
            "旅行风格": "轻松",
        },
        mode="初次规划",
        travel_style="轻松",
    )

    print(f"\n[Request]")
    print(f"  Destination: {request.destination}")
    print(f"  Days: {request.days}")
    print(f"  Preferences: {request.preferences}")

    total_agent = TotalAgent(settings)
    memory = Memory(settings)
    total_agent.memory = memory

    try:
        result = await total_agent.plan_trip(request)

        print("\n[Result]")
        print(f"  Success: {result.success}")
        print(f"  Message: {result.message}")

        if result.total:
            print(f"  TotalAgent: action={result.total.action}")

        if result.strategy:
            print(f"  StrategyAgent: scenic_types={result.strategy.scenic_types}")
            print(f"  StrategyAgent: required_info={result.strategy.required_info}")

        if result.query:
            print(f"  QueryAgent: found {len(result.query.spots_summary)} spots")
            if result.query.spots_summary:
                print(f"    Spots: {[s.name for s in result.query.spots_summary[:5]]}")

        if result.images:
            print(f"  ImageAgent: {len(result.images.observations)} images")

        if result.analysis:
            print(f"  AnalysisAgent: {len(result.analysis.spots)} enriched spots")
            if result.analysis.spots:
                for spot in result.analysis.spots[:3]:
                    print(f"    - {spot.name} (type={spot.type}, status={spot.status}, images={len(spot.images)})")

        if result.report:
            print(f"  ReportAgent: HTML report ({len(result.report.html_report)} chars)")
            print(f"  ReportAgent: modifiable spots={result.report.modifiable_spots[:5]}")

        if result.plan:
            print(f"  Plan: {result.plan.destination} {result.plan.days}-day trip")
            print(f"  Trip ID: {result.plan.trip_id}")
            print(f"  Itinerary: {len(result.plan.itinerary)} days")
            print(f"  Highlights: {result.plan.highlights[:3]}")

        if result.preference:
            print(f"  Preferences: {result.preference.preferences}")

        print(f"\n  Trace: {[t.agent for t in result.trace]}")
        print("\n" + "=" * 60)
        print("Full Flow Test PASSED!")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_agents_individually():
    """Test each agent individually."""

    print("\n" + "=" * 60)
    print("Individual Agent Tests")
    print("=" * 60)

    settings = get_settings()

    # StrategyAgent
    print("\n[StrategyAgent]")
    from StrategyAgent.agent import StrategyAgent

    request = TripPlanRequest(
        user_id="test_001",
        destination="杭州",
        days=2,
        preferences=["历史", "自然"],
    )

    strategy_agent = StrategyAgent(settings)
    try:
        strategy_output = await strategy_agent.run(request)
        print(f"  OK: scenic_types={strategy_output.scenic_types}")
        print(f"  OK: required_info={strategy_output.required_info}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    # QueryAgent
    print("\n[QueryAgent]")
    from QueryEngine.agent import QueryAgent

    query_agent = QueryAgent(settings)
    try:
        query_output = await query_agent.run(strategy_output)
        print(f"  OK: spots_summary={len(query_output.spots_summary)}")
        print(f"  OK: sources={len(query_output.sources)}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    # ImageAgent
    print("\n[ImageAgent]")
    from ImageAgent.agent import ImageAgent

    image_agent = ImageAgent(settings)
    try:
        image_output = await image_agent.run(request)
        print(f"  OK: observations={len(image_output.observations)}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    # AnalysisAgent
    print("\n[AnalysisAgent]")
    from AnalysisAgent.agent import AnalysisAgent

    analysis_agent = AnalysisAgent(settings)
    try:
        analysis_output, plan, trace = await analysis_agent.run(
            request,
            strategy_output,
            query_output,
            image_output,
            strategy_output.memory,
        )
        print(f"  OK: spots={len(analysis_output.spots)}")
        print(f"  OK: itinerary={len(plan.itinerary)} days")
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ReportAgent
    print("\n[ReportAgent]")
    from ReportEngine.agent import ReportAgent

    report_agent = ReportAgent(settings)
    try:
        report_output, report_trace = await report_agent.run(plan, False, analysis_output)
        print(f"  OK: HTML length={len(report_output.html_report)} chars")
        print(f"  OK: modifiable_spots={report_output.modifiable_spots[:3]}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    print("\n" + "=" * 60)
    print("Individual Agent Tests PASSED!")
    print("=" * 60)
    return True


async def test_memory_system():
    """Test the memory system."""

    print("\n" + "=" * 60)
    print("Memory System Test")
    print("=" * 60)

    settings = get_settings()
    memory = Memory(settings)
    user_id = "test_memory_user"

    # Short-term memory
    print("\n[Short-term Memory]")
    memory.set_short_term(user_id, {
        "action": "plan_trip",
        "destination": "北京",
        "days": 3,
    })
    short_term = memory.get_short_term(user_id)
    print(f"  Set: action=plan_trip, destination=北京")
    print(f"  Get: {short_term}")
    assert short_term.get("destination") == "北京", "Short-term memory failed"
    print("  OK")

    # Long-term memory
    print("\n[Long-term Memory]")
    from app.models.schemas import PreferenceUpdateRequest

    long_term = memory.get_long_term(user_id)
    print(f"  New user: preferences={long_term.preferences}")

    update = PreferenceUpdateRequest(
        preferences=["历史", "自然", "美食"],
        budget_level="中等",
        pace="轻松",
    )
    updated = memory.set_long_term(user_id, update.model_dump(mode="json"))
    print(f"  Updated: preferences={updated.preferences}")

    long_term = memory.get_long_term(user_id)
    print(f"  Read back: preferences={long_term.preferences}")
    assert "历史" in long_term.preferences, "Long-term memory failed"
    print("  OK")

    print("\n" + "=" * 60)
    print("Memory System Test PASSED!")
    print("=" * 60)
    return True


async def main():
    """Run all tests."""

    print("\n" + "=" * 60)
    print("Multi-Agent Travel Planning System - Test Suite")
    print("=" * 60)

    results = {}

    # 1. Memory system
    results["memory"] = await test_memory_system()

    # 2. Individual agents
    results["individual_agents"] = await test_agents_individually()

    # 3. Full flow
    results["full_flow"] = await test_full_flow()

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")

    all_passed = all(results.values())
    print("\n" + ("All tests PASSED!" if all_passed else "Some tests FAILED!"))
    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
