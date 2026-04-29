import sys
from pathlib import Path

# Ensure backend root is importable when running blocks/*.py directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from app.services.amap_service import get_amap_service

def main():
    amap = get_amap_service()
    hotels = []
    
    # 读取景点数据
    try:
        with open('blocks/output/attractions.json', 'r', encoding='utf-8') as f:
            attractions = json.load(f)
    except FileNotFoundError:
        print("Error: attractions.json not found")
        return
    
    # 处理每个景点
    for attraction in attractions:
        location = attraction.get('location')
        if not location:
            continue
            
        # 搜索周边酒店
        result = amap.call_tool_json("maps_around_search", {
            "location": location,
            "radius": "2000",
            "keywords": "酒店"
        })
        
        if not result.get("ok"):
            print(f"Error searching hotels near {attraction['name']}: {result.get('error')}")
            continue
            
        pois = result.get("data", {}).get("pois", [])
        if not pois:
            # 尝试更换关键词
            result = amap.call_tool_json("maps_around_search", {
                "location": location,
                "radius": "2000",
                "keywords": "宾馆"
            })
            if not result.get("ok"):
                continue
            pois = result.get("data", {}).get("pois", [])
            if not pois:
                continue
        
        # 获取每家酒店详情
        count = 0
        for poi in pois[:3]:  # 每个景点最多3家酒店
            hotel_id = poi.get("id")
            if not hotel_id:
                continue
                
            detail_result = amap.call_tool_json("maps_search_detail", {"id": hotel_id})
            if not detail_result.get("ok"):
                print(f"Error getting hotel detail {hotel_id}: {detail_result.get('error')}")
                continue
                
            hotel_data = detail_result.get("data", {})
            if not hotel_data:
                continue
                
            hotels.append({
                'id': hotel_data.get('id', ''),
                'name': hotel_data.get('name', ''),
                'location': hotel_data.get('location', ''),
                'address': hotel_data.get('address', ''),
                'business_area': hotel_data.get('business_area', []),
                'city': hotel_data.get('city', ''),
                'type': hotel_data.get('type', ''),
                'alias': hotel_data.get('alias', []),
                'cost': hotel_data.get('cost', []),
                'star': hotel_data.get('star', []),
                'opentime2': hotel_data.get('opentime2', []),
                'rating': hotel_data.get('rating', []),
                'lowest_price': hotel_data.get('lowest_price', []),
                'hotel_ordering': hotel_data.get('hotel_ordering', '0'),
                'open_time': hotel_data.get('open_time', [])
            })
            count += 1
            if count >= 3:
                break
    
    # 保存酒店数据
    if hotels:
        with open('blocks/output/hotels.json', 'w', encoding='utf-8') as f:
            json.dump(hotels, f, ensure_ascii=False, indent=2)
        print(f"Successfully saved {len(hotels)} hotels to blocks/output/hotels.json")
    else:
        print("No hotel data found")

if __name__ == "__main__":
    main()