import sys
from pathlib import Path

# Ensure backend root is importable when running blocks/*.py directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Union
from pathlib import Path
from app.services.unsplash_service import get_unsplash_service

def normalize_itinerary(data: Union[dict, list]) -> dict:
    """标准化行程数据，兼容不同结构"""
    if isinstance(data, list):
        return {"days": data}
    if "days" not in data and "daily_plans" in data:
        data["days"] = data.pop("daily_plans")
    return data

def normalize_weather(data: Union[dict, list]) -> List[dict]:
    """标准化天气数据"""
    if isinstance(data, dict):
        if "forecasts" in data:
            return data["forecasts"]
        if "weather_info" in data:
            return data["weather_info"]
        return [data]
    return data

def parse_location(loc_str: str) -> Optional[Dict[str, float]]:
    """解析location字符串为经纬度字典"""
    if not loc_str or not isinstance(loc_str, str):
        return None
    try:
        lng, lat = map(float, loc_str.split(","))
        return {"lng": lng, "lat": lat}
    except:
        return None

def get_photo_url(photos: List[dict], index: int = 0) -> Optional[str]:
    """从Unsplash照片列表中获取指定索引的URL"""
    if not photos or index >= len(photos):
        return None
    return photos[index].get("url")

def render_html():
    # 读取输入文件
    input_dir = Path("blocks/output")
    
    try:
        with open(input_dir / "itinerary_plan.json", "r", encoding="utf-8") as f:
            itinerary_data = normalize_itinerary(json.load(f))
    except Exception as e:
        print(f"Error loading itinerary_plan.json: {e}")
        return

    try:
        with open(input_dir / "unsplash_images.json", "r", encoding="utf-8") as f:
            photos = json.load(f)
    except Exception as e:
        print(f"Error loading unsplash_images.json: {e}")
        photos = []

    try:
        with open(input_dir / "weather.json", "r", encoding="utf-8") as f:
            weather_data = normalize_weather(json.load(f))
    except Exception as e:
        print(f"Error loading weather.json: {e}")
        weather_data = []

    # 准备天气数据映射
    weather_by_date = {w["date"]: w for w in weather_data if isinstance(w, dict) and "date" in w}

    # 生成HTML
    html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{itinerary_data.get("city", "上海")} {itinerary_data.get("start_date", "")}-{itinerary_data.get("end_date", "")} 行程</title>
        <style>
            body {{
                font-family: 'Arial', sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                text-align: center;
                margin-bottom: 30px;
            }}
            .photo-gallery {{
                display: flex;
                justify-content: space-between;
                margin-bottom: 30px;
                flex-wrap: wrap;
            }}
            .photo-gallery img {{
                width: 32%;
                height: 300px;
                object-fit: cover;
                border-radius: 8px;
                margin-bottom: 10px;
            }}
            .day-card {{
                background: #f9f9f9;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 30px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }}
            .day-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #eee;
                padding-bottom: 10px;
                margin-bottom: 15px;
            }}
            .weather-info {{
                display: flex;
                align-items: center;
                font-size: 0.9em;
                color: #666;
            }}
            .weather-icon {{
                margin-right: 5px;
                font-size: 1.2em;
            }}
            .attraction, .meal, .hotel {{
                background: white;
                padding: 15px;
                border-radius: 6px;
                margin-bottom: 15px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            .attraction h3, .meal h3, .hotel h3 {{
                margin-top: 0;
                color: #2c3e50;
            }}
            .location {{
                color: #7f8c8d;
                font-size: 0.9em;
                margin: 5px 0;
            }}
            .price {{
                color: #e74c3c;
                font-weight: bold;
            }}
            .map-container {{
                height: 300px;
                margin-top: 15px;
                border-radius: 6px;
                overflow: hidden;
            }}
            .budget-summary {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-top: 30px;
            }}
            .budget-item {{
                display: flex;
                justify-content: space-between;
                margin-bottom: 8px;
            }}
            .total-budget {{
                font-weight: bold;
                border-top: 1px solid #ddd;
                padding-top: 10px;
                margin-top: 10px;
            }}
            .suggestions {{
                background: #e8f4fd;
                padding: 15px;
                border-radius: 8px;
                margin: 20px 0;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{itinerary_data.get("city", "上海")} {itinerary_data.get("start_date", "")}-{itinerary_data.get("end_date", "")} 行程</h1>
            <p>历史文化与美食之旅 · 预算 {itinerary_data.get("budget", {}).get("total", 0)}元</p>
        </div>

        <!-- Unsplash图片展示 -->
        <div class="photo-gallery">
            {''.join(f'<img src="{photo["url"]}" alt="{photo.get("description", "")}" title="Photo by {photo.get("photographer", "")}">' 
                    for photo in photos[:3])}
        </div>

        <!-- 总体建议 -->
        {f'<div class="suggestions"><h3>总体建议</h3><p>{itinerary_data.get("overall_suggestions", "")}</p></div>' 
         if itinerary_data.get("overall_suggestions") else ''}

        <!-- 每日行程 -->
        {''.join(render_day(day, weather_by_date.get(day["date"])) 
                for day in itinerary_data.get("days", []) if isinstance(day, dict))}

        <!-- 预算汇总 -->
        <div class="budget-summary">
            <h3>预算汇总</h3>
            {render_budget(itinerary_data.get("budget", {}))}
        </div>

        <!-- 高德地图脚本 -->
        <script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_KEY"></script>
        <script>
            // 初始化地图
            function initMap() {{
                var map = new AMap.Map('map-container', {{
                    zoom: 12,
                    center: [121.47, 31.23]  // 上海中心坐标
                }});

                // 为每个景点添加标记
                {generate_map_markers(itinerary_data.get("days", []))}
            }}

            // 如果AMap加载完成，初始化地图
            if (window.AMap) {{
                initMap();
            }} else {{
                window.onload = initMap;
            }}
        </script>
    </body>
    </html>
    """

    # 保存HTML文件
    output_path = input_dir / "itinerary.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"成功生成行程HTML: {output_path}")

def render_day(day: dict, weather: Optional[dict] = None) -> str:
    """渲染单日行程HTML"""
    weather = weather or day.get("weather", {})
    weather_html = render_weather(weather)
    
    hotel_html = ""
    if isinstance(day.get("hotel"), dict):
        hotel = day["hotel"]
        hotel_html = f"""
        <div class="hotel">
            <h3>住宿: {hotel.get("name", "未知酒店")}</h3>
            <div class="location">地址: {hotel.get("address", "未知地址")}</div>
            <div>价格区间: {hotel.get("price_range", "未知")} · 评分: {hotel.get("rating", "未知")}</div>
            <p>{hotel.get("distance", "")} · {hotel.get("type", "未知类型")}</p>
        </div>
        """
    
    attractions_html = ""
    if isinstance(day.get("attractions"), list):
        attractions_html = "".join(
            f"""
            <div class="attraction">
                <h3>{att.get("name", "未知景点")}</h3>
                <div class="location">地址: {att.get("address", "未知地址")}</div>
                <div>游览时间: {att.get("visit_duration", 0)}分钟 · 类别: {att.get("category", "未知")}</div>
                <div class="price">门票: {att.get("ticket_price", 0)}元</div>
                <p>{att.get("description", "")}</p>
                {render_location_map(att.get("location"))}
            </div>
            """
            for att in day["attractions"] if isinstance(att, dict)
        )
    
    meals_html = ""
    if isinstance(day.get("meals"), list):
        meals_html = "".join(
            f"""
            <div class="meal">
                <h3>{meal.get("type", "餐").title()}: {meal.get("name", "未知餐厅")}</h3>
                <div class="price">预计花费: {meal.get("estimated_cost", 0)}元</div>
                <p>{meal.get("description", "")}</p>
            </div>
            """
            for meal in day["meals"] if isinstance(meal, dict)
        )
    
    return f"""
    <div class="day-card">
        <div class="day-header">
            <h2>{day.get("date", "未知日期")} · 第{day.get("day_index", 0)+1}天: {day.get("description", "")}</h2>
            {weather_html}
        </div>
        
        <div class="transportation">
            <h3>交通方式</h3>
            <p>{day.get("transportation", "未知")}</p>
        </div>
        
        {hotel_html}
        {attractions_html}
        {meals_html}
    </div>
    """

def render_weather(weather: dict) -> str:
    """渲染天气信息HTML"""
    if not weather:
        return '<div class="weather-info">天气: 未知</div>'
    
    day_weather = weather.get("dayweather", "未知")
    night_weather = weather.get("nightweather", "未知")
    day_temp = weather.get("daytemp", "未知")
    night_temp = weather.get("nighttemp", "未知")
    
    return f"""
    <div class="weather-info">
        <span class="weather-icon">☀️</span>
        白天: {day_weather} {day_temp}°C · 
        <span class="weather-icon">🌙</span>
        夜间: {night_weather} {night_temp}°C
    </div>
    """

def render_budget(budget: dict) -> str:
    """渲染预算信息HTML"""
    if not budget:
        return "<p>无预算信息</p>"
    
    items = [
        ("景点门票", budget.get("total_attractions", 0)),
        ("酒店住宿", budget.get("total_hotels", 0)),
        ("餐饮费用", budget.get("total_meals", 0)),
        ("交通费用", budget.get("total_transportation", 0)),
    ]
    
    return "".join(
        f"""
        <div class="budget-item">
            <span>{name}:</span>
            <span>{value}元</span>
        </div>
        """
        for name, value in items
    ) + f"""
    <div class="budget-item total-budget">
        <span>总预算:</span>
        <span>{budget.get("total", 0)}元</span>
    </div>
    """

def render_location_map(location: str) -> str:
    """渲染位置地图HTML"""
    if not location:
        return ""
    
    loc = parse_location(location)
    if not loc:
        return f"<p>坐标: {location}</p>"
    
    return f"""
    <div class="map-container" id="map-{hash(location)}"></div>
    <script>
        if (window.AMap) {{
            var map = new AMap.Map('map-{hash(location)}', {{
                zoom: 15,
                center: [{loc['lng']}, {loc['lat']}]
            }});
            new AMap.Marker({{
                position: [{loc['lng']}, {loc['lat']}],
                map: map
            }});
        }}
    </script>
    """

def generate_map_markers(days: List[dict]) -> str:
    """生成地图标记的JavaScript代码"""
    markers = []
    for day in days:
        if not isinstance(day, dict):
            continue
        for att in day.get("attractions", []):
            if not isinstance(att, dict):
                continue
            loc = parse_location(att.get("location", ""))
            if loc:
                markers.append(f"""
                    new AMap.Marker({{
                        position: [{loc['lng']}, {loc['lat']}],
                        map: map,
                        title: "{att.get('name', '景点')}"
                    }});
                """)
    
    return "\n".join(markers)

if __name__ == "__main__":
    render_html()