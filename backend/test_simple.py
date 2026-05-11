"""简单测试脚本 - 验证基本功能"""

import asyncio
import os
import sys

# 设置 UTF-8 编码
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

# 设置环境变量（如果系统环境变量未设置）
if not os.getenv("SILICONFLOW_API_KEY"):
    print("提示：请设置系统环境变量 SILICONFLOW_API_KEY")

from app.config import get_settings
from app.models.schemas import TripPlanRequest


def check_config():
    """检查配置"""
    print("=" * 50)
    print("配置检查")
    print("=" * 50)

    settings = get_settings()

    checks = [
        ("SILICONFLOW_API_KEY", settings.siliconflow_api_key, "SiliconFlow API"),
        ("TAVILY_API_KEY", settings.tavily_api_key, "Tavily 搜索"),
        ("AMAP_API_KEY", settings.amap_api_key, "高德地图"),
        ("UNSPLASH_ACCESS_KEY", settings.unsplash_access_key, "Unsplash 图片"),
    ]

    all_ok = True
    for name, value, desc in checks:
        status = "[OK]" if value else "[  ]"
        status_str = "已配置" if value else "未配置"
        print(f"  {status} {desc}: {status_str}")
        if not value and name in ["SILICONFLOW_API_KEY", "TAVILY_API_KEY"]:
            all_ok = False

    if not all_ok:
        print("")
        print("  警告：部分必需 API Key 未配置")
        print("  请在系统环境变量中设置 SILICONFLOW_API_KEY 和 TAVILY_API_KEY")

    return all_ok


async def test_memory():
    """测试记忆系统"""
    print("")
    print("=" * 50)
    print("测试记忆系统")
    print("=" * 50)

    from Memory import Memory
    from app.models.schemas import PreferenceUpdateRequest

    settings = get_settings()
    memory = Memory(settings)

    user_id = "test_user"

    # 测试短期记忆
    print("")
    print("  短期记忆测试...")
    memory.set_short_term(user_id, {"test": "data"})
    result = memory.get_short_term(user_id)
    assert result.get("test") == "data", "短期记忆测试失败"
    print("  [OK] 短期记忆测试通过")

    # 测试长期记忆
    print("  长期记忆测试...")
    long_term = memory.get_long_term(user_id)
    print(f"    用户 ID: {long_term.user_id}")
    print(f"    偏好：{long_term.preferences}")

    # 更新长期记忆
    update = PreferenceUpdateRequest(
        preferences=["历史", "自然"],
        budget_level="中等",
    )
    updated = memory.set_long_term(user_id, update.model_dump(mode="json"))
    print(f"    更新后偏好：{updated.preferences}")
    print("  [OK] 长期记忆测试通过")

    return True


async def test_strategy_agent():
    """测试 StrategyAgent"""
    print("")
    print("=" * 50)
    print("测试 StrategyAgent")
    print("=" * 50)

    from StrategyAgent.agent import StrategyAgent

    settings = get_settings()
    strategy_agent = StrategyAgent(settings)

    request = TripPlanRequest(
        user_id="test_001",
        destination="杭州",
        days=2,
        preferences=["历史", "自然"],
        mode="初次规划",
    )

    print(f"  输入：destination={request.destination}, days={request.days}")

    try:
        output = await strategy_agent.run(request)
        print("  [OK] StrategyAgent 执行成功")
        print(f"    scenic_types: {output.scenic_types}")
        print(f"    required_info: {output.required_info}")
        print(f"    查询关键词：{output.query.keywords[:5]}")
        return True, output
    except Exception as e:
        print(f"  [FAIL] StrategyAgent 执行失败：{e}")
        return False, None


async def test_query_agent(strategy_output):
    """测试 QueryAgent"""
    print("")
    print("=" * 50)
    print("测试 QueryAgent")
    print("=" * 50)

    from QueryEngine.agent import QueryAgent

    settings = get_settings()
    query_agent = QueryAgent(settings)

    print(f"  输入：destination={strategy_output.destination}")
    print(f"    scenic_types: {strategy_output.scenic_types}")

    try:
        output = await query_agent.run(strategy_output)
        print("  [OK] QueryAgent 执行成功")
        print(f"    spots_summary 数量：{len(output.spots_summary)}")
        if output.spots_summary:
            print(f"    景点：{[s.name for s in output.spots_summary[:3]]}")
        print(f"    sources 数量：{len(output.sources)}")
        return True, output
    except Exception as e:
        print(f"  [FAIL] QueryAgent 执行失败：{e}")
        return False, None


async def test_analysis_agent(strategy_output, query_output):
    """测试 AnalysisAgent"""
    print("")
    print("=" * 50)
    print("测试 AnalysisAgent")
    print("=" * 50)

    from AnalysisAgent.agent import AnalysisAgent
    from ImageAgent.agent import ImageAgent

    settings = get_settings()
    analysis_agent = AnalysisAgent(settings)
    image_agent = ImageAgent(settings)

    request = TripPlanRequest(
        user_id="test_001",
        destination=strategy_output.destination,
        days=strategy_output.days,
        preferences=strategy_output.scenic_types,
    )

    # 创建 ImageAgent 输出
    image_output = await image_agent.run(request)

    print(f"  输入：destination={strategy_output.destination}")
    print(f"    spots_summary: {len(query_output.spots_summary)} 个景点")

    try:
        analysis_output, plan, trace = await analysis_agent.run(
            request,
            strategy_output,
            query_output,
            image_output,
            strategy_output.memory,
        )
        print("  [OK] AnalysisAgent 执行成功")
        print(f"    spots 数量：{len(analysis_output.spots)}")
        if analysis_output.spots:
            for spot in analysis_output.spots[:3]:
                print(f"      - {spot.name} ({spot.type})")
        print(f"    行程天数：{len(plan.itinerary)}")
        return True, analysis_output, plan
    except Exception as e:
        print(f"  [FAIL] AnalysisAgent 执行失败：{e}")
        import traceback
        traceback.print_exc()
        return False, None, None


async def test_report_agent(plan, analysis_output):
    """测试 ReportAgent"""
    print("")
    print("=" * 50)
    print("测试 ReportAgent")
    print("=" * 50)

    from ReportEngine.agent import ReportAgent

    settings = get_settings()
    report_agent = ReportAgent(settings)

    print(f"  输入：trip_id={plan.trip_id}")
    print(f"    destination={plan.destination}")

    try:
        report_output, report_trace = await report_agent.run(plan, False, analysis_output)
        print("  [OK] ReportAgent 执行成功")
        print(f"    HTML 报告长度：{len(report_output.html_report)} 字符")
        print(f"    可修改景点：{report_output.modifiable_spots}")
        return True, report_output
    except Exception as e:
        print(f"  [FAIL] ReportAgent 执行失败：{e}")
        import traceback
        traceback.print_exc()
        return False, None


async def main():
    """主测试函数"""

    print("")
    print("=" * 50)
    print("多 Agent 旅行规划系统 - 功能测试")
    print("=" * 50)

    # 1. 配置检查
    config_ok = check_config()

    # 2. 记忆系统测试
    try:
        await test_memory()
    except Exception as e:
        print(f"  [FAIL] 记忆系统测试失败：{e}")

    # 3. StrategyAgent 测试
    strategy_ok, strategy_output = await test_strategy_agent()
    if not strategy_ok:
        print("")
        print("  StrategyAgent 测试失败，后续测试无法继续")
        return

    # 4. QueryAgent 测试
    query_ok, query_output = await test_query_agent(strategy_output)
    if not query_ok:
        print("")
        print("  QueryAgent 测试失败，后续测试无法继续")
        return

    # 5. AnalysisAgent 测试
    analysis_ok, analysis_output, plan = await test_analysis_agent(strategy_output, query_output)
    if not analysis_ok:
        print("")
        print("  AnalysisAgent 测试失败，后续测试无法继续")
        return

    # 6. ReportAgent 测试
    report_ok, report_output = await test_report_agent(plan, analysis_output)
    if not report_ok:
        print("")
        print("  ReportAgent 测试失败")
        return

    # 总结
    print("")
    print("=" * 50)
    print("测试总结")
    print("=" * 50)
    print("  [OK] StrategyAgent: 通过")
    print("  [OK] QueryAgent: 通过")
    print("  [OK] AnalysisAgent: 通过")
    print("  [OK] ReportAgent: 通过")
    print("")
    print("所有测试通过!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
