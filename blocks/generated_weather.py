import sys
from pathlib import Path

# Ensure backend root is importable when running blocks/*.py directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import os
from pathlib import Path
from app.services.amap_service import get_amap_service

def main():
    # 确保输出目录存在
    output_dir = Path("blocks/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 初始化高德服务
    amap = get_amap_service()
    
    # 查询上海天气
    result = amap.call_tool_json("maps_weather", {"city": "上海"})
    
    if not result.get("ok"):
        error = result.get("error", "Unknown error")
        raise Exception(f"Weather API call failed: {error}")
    
    # 验证数据有效性
    weather_data = result.get("data", [])
    if not weather_data:
        raise Exception("Received empty weather data")
    
    # 写入文件
    output_path = output_dir / "weather.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(weather_data, f, ensure_ascii=False, indent=2)
    
    print(f"Successfully fetched {len(weather_data)} days weather data")
    print(f"Saved to: {output_path}")

if __name__ == "__main__":
    main()