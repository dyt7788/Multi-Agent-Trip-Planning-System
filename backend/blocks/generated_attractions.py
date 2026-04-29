import sys
from pathlib import Path

# Ensure backend root is importable when running blocks/*.py directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from pathlib import Path
from app.services.amap_service import get_amap_service

def search_pois(keywords, city="上海"):
    """搜索POI并返回有效结果列表"""
    amap = get_amap_service()
    result = amap.call_tool_json("maps_text_search", {"keywords": keywords, "city": city})
    if not result.get("ok"):
        print(f"搜索失败: {result.get('error')}")
        return []
    return result.get("data", {}).get("pois", [])

def get_poi_details(poi_id):
    """获取POI详细信息"""
    amap = get_amap_service()
    detail = amap.call_tool_json("maps_search_detail", {"id": poi_id})
    if not detail.get("ok"):
        print(f"获取详情失败: {detail.get('error')}")
        return None
    return detail.get("data", {})

def save_attractions(attractions):
    """保存景点数据到文件"""
    output_path = Path("blocks/output/attractions.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(attractions, f, ensure_ascii=False, indent=2)
    print(f"已保存 {len(attractions)} 个景点到 {output_path}")

def main():
    # 搜索热门景点
    hot_pois = search_pois("上海热门景点")
    # 搜索历史文化景点
    culture_pois = search_pois("上海历史文化")
    
    # 合并并去重
    all_pois = []
    seen_ids = set()
    
    # 先添加热门景点(60%)
    hot_count = min(9, len(hot_pois))  # 12*0.6≈7, 15*0.6≈9
    for poi in hot_pois[:hot_count]:
        if poi["id"] not in seen_ids:
            all_pois.append(poi)
            seen_ids.add(poi["id"])
    
    # 再添加文化景点(40%)
    culture_count = min(6, len(culture_pois))  # 12*0.4≈5, 15*0.4≈6
    for poi in culture_pois[:culture_count]:
        if poi["id"] not in seen_ids:
            all_pois.append(poi)
            seen_ids.add(poi["id"])
    
    # 获取详细信息
    attractions = []
    for poi in all_pois:
        detail = get_poi_details(poi["id"])
        if detail:
            attractions.append(detail)
    
    # 保存结果
    save_attractions(attractions)

if __name__ == "__main__":
    main()