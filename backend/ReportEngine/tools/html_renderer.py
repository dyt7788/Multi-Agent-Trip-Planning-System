"""HTML itinerary renderer."""

from __future__ import annotations

from app.models.schemas import ItineraryPlan
from TravelCore.text import html_escape


class HtmlReportRenderer:
    def render(self, plan: ItineraryPlan) -> str:
        detail = plan.detailed_plan
        detailed_days = detail.days if detail else []
        weather_by_day = detail.weather_info if detail else []
        days = "\n".join(
            self._day(
                day,
                detailed_days[index] if index < len(detailed_days) else None,
                weather_by_day[index] if index < len(weather_by_day) else None,
            )
            for index, day in enumerate(plan.itinerary)
        )
        weather = self._weather_section(detail)
        budget_detail = self._budget_section(detail)
        highlights = "".join(f"<li>{html_escape(item)}</li>" for item in plan.highlights)
        restaurants = "".join(f"<li>{html_escape(item)}</li>" for item in plan.restaurants)
        tips = "".join(f"<li>{html_escape(item)}</li>" for item in plan.packing_tips)
        risks = "".join(f"<li>{html_escape(item)}</li>" for item in plan.risk_notes)
        sources = "".join(
            f'<li><a href="{html_escape(url)}">{html_escape(url)}</a></li>'
            for url in plan.source_references[:12]
        )
        budget = f"{plan.total_budget:.0f} CNY" if plan.total_budget else "Not specified"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html_escape(plan.destination)} 行程报告</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2937;
      --muted: #667085;
      --line: #d0d5dd;
      --paper: #ffffff;
      --accent: #0f766e;
      --soft: #f2f4f7;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #f7f8fa;
    }}
    main {{
      width: min(1040px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }}
    header {{
      padding: 28px 0 20px;
      border-bottom: 2px solid var(--accent);
    }}
    h1 {{ margin: 0 0 10px; font-size: 34px; letter-spacing: 0; }}
    h2 {{ margin: 30px 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ line-height: 1.7; }}
    .meta {{ color: var(--muted); display: flex; flex-wrap: wrap; gap: 10px 18px; }}
    .section {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      margin-top: 18px;
    }}
    .day {{ margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--line); }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin: 14px 0;
    }}
    .mini {{
      background: var(--soft);
      border-radius: 8px;
      padding: 12px;
    }}
    .mini h4 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); }}
    .mini p {{ margin: 4px 0; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--soft);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 700; }}
    .slot {{
      display: grid;
      grid-template-columns: 84px 1fr 92px;
      gap: 14px;
      padding: 12px 0;
      border-top: 1px solid var(--soft);
    }}
    .time {{ color: var(--accent); font-weight: 700; }}
    .tag {{ color: var(--muted); text-align: right; }}
    ul {{ padding-left: 20px; }}
    a {{ color: var(--accent); overflow-wrap: anywhere; }}
    @media (max-width: 640px) {{
      h1 {{ font-size: 26px; }}
      .slot {{ grid-template-columns: 72px 1fr; }}
      .tag {{ grid-column: 2; text-align: left; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html_escape(plan.destination)} {plan.days} 日行程报告</h1>
      <p>{html_escape(plan.summary)}</p>
      <div class="meta">
        <span>行程 ID: {html_escape(plan.trip_id)}</span>
        <span>预算总览: {html_escape(budget)}</span>
      </div>
    </header>
    <section class="section">
      <h2>天气与预算</h2>
      {weather}
      {budget_detail}
    </section>
    <section class="section">
      <h2>每日行程</h2>
      {days}
    </section>
    <section class="section">
      <h2>亮点推荐</h2>
      <ul>{highlights}</ul>
      <h2>餐饮推荐</h2>
      <ul>{restaurants}</ul>
      <h2>打包提示</h2>
      <ul>{tips}</ul>
      <h2>风险提示</h2>
      <ul>{risks}</ul>
      <h2>来源</h2>
      <ul>{sources}</ul>
    </section>
  </main>
</body>
</html>"""

    def _day(self, day, detail_day=None, weather=None) -> str:
        date_text = f" · {day.date.isoformat()}" if day.date else ""
        weather_text = ""
        if weather:
            temps = []
            if weather.day_temp is not None:
                temps.append(f"白天 {weather.day_temp}℃")
            if weather.night_temp is not None:
                temps.append(f"夜间 {weather.night_temp}℃")
            weather_text = (
                f"<p><strong>天气：</strong>{html_escape(weather.day_weather)} / "
                f"{html_escape(weather.night_weather)}"
                f"{' · ' + html_escape(', '.join(temps)) if temps else ''}"
                f"{' · ' + html_escape(weather.wind_direction + weather.wind_power) if weather.wind_direction or weather.wind_power else ''}</p>"
            )
        detail_block = self._detail_day(detail_day)
        slots = "\n".join(
            f"""
            <div class="slot">
              <div class="time">{html_escape(slot.time)}</div>
              <div>
                <strong>{html_escape(slot.title)}</strong>
                <p>{html_escape(slot.description)}</p>
              </div>
              <div class="tag">{html_escape(slot.category)}<br />{html_escape(slot.estimated_cost)} CNY</div>
            </div>
            """
            for slot in day.slots
        )
        return f"""
        <article class="day">
          <h3>第 {html_escape(day.day)} 天{html_escape(date_text)} - {html_escape(day.theme)}</h3>
          <p>{html_escape(day.route_notes)}</p>
          {weather_text}
          {detail_block}
          {slots}
        </article>
        """

    def _detail_day(self, detail_day) -> str:
        if not detail_day:
            return ""
        hotel = detail_day.hotel
        attractions = "".join(
            f"<li>{html_escape(item.name)} · {html_escape(item.address)} · "
            f"{html_escape(item.visit_duration)} min · {html_escape(item.ticket_price)} CNY</li>"
            for item in detail_day.attractions
        )
        meals = "".join(
            f"<li>{html_escape(meal.type)}: {html_escape(meal.name)} · "
            f"{html_escape(meal.estimated_cost)} CNY</li>"
            for meal in detail_day.meals
        )
        return f"""
        <div class="detail-grid">
          <div class="mini">
            <h4>酒店</h4>
            <p><strong>{html_escape(hotel.name)}</strong></p>
            <p>{html_escape(hotel.address)}</p>
            <p>{html_escape(hotel.price_range)} · {html_escape(hotel.rating)} · {html_escape(hotel.distance)}</p>
          </div>
          <div class="mini">
            <h4>景点</h4>
            <ul>{attractions}</ul>
          </div>
          <div class="mini">
            <h4>餐饮</h4>
            <ul>{meals}</ul>
          </div>
        </div>
        """

    def _weather_section(self, detail) -> str:
        if not detail or not detail.weather_info:
            return "<p>暂无天气信息。</p>"
        rows = "".join(
            f"""
            <tr>
              <td>{html_escape(item.date.isoformat() if item.date else '')}</td>
              <td>{html_escape(item.day_weather)}</td>
              <td>{html_escape(item.night_weather)}</td>
              <td>{html_escape(item.day_temp)}</td>
              <td>{html_escape(item.night_temp)}</td>
              <td>{html_escape(item.wind_direction)} {html_escape(item.wind_power)}</td>
            </tr>
            """
            for item in detail.weather_info
        )
        return f"""
        <table>
          <thead>
            <tr>
              <th>日期</th><th>白天</th><th>夜间</th><th>白天温度</th><th>夜间温度</th><th>风力风向</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p>{html_escape(detail.overall_suggestions)}</p>
        """

    def _budget_section(self, detail) -> str:
        if not detail:
            return ""
        budget = detail.budget
        return f"""
        <div class="detail-grid">
          <div class="mini"><h4>景点门票</h4><p>{html_escape(budget.total_attractions)} CNY</p></div>
          <div class="mini"><h4>酒店费用</h4><p>{html_escape(budget.total_hotels)} CNY</p></div>
          <div class="mini"><h4>餐饮费用</h4><p>{html_escape(budget.total_meals)} CNY</p></div>
          <div class="mini"><h4>交通费用</h4><p>{html_escape(budget.total_transportation)} CNY</p></div>
          <div class="mini"><h4>合计</h4><p>{html_escape(budget.total)} CNY</p></div>
        </div>
        """
