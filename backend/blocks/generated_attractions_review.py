import sys
from pathlib import Path

# Ensure backend root is importable when running blocks/*.py directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from app.services.amap_service import get_amap_service

amap = get_amap_service()

def clean_attractions():
    # 读取原始文件
    with open('blocks/output/attractions.json', 'r', encoding='utf-8') as f:
        attractions = json.load(f)
    
    original_count = len(attractions)
    print(f"清洗前数量: {original_count}")
    
    # 定义保留的景点ID白名单
    keep_ids = {
        'B00155FHOA',  # 上海城隍庙
        'B001558F5I',  # 朱家角古镇
        'B00155LZNL',  # 新场古镇
        'B001556VY1',  # 枫泾古镇
        'B0KDX7ZH7C',  # 倉城歷史文化風貌區
        'B00155G34C',  # 淀山湖风景区（保留作为备选）
        'B00157AW8O',  # 上海迪士尼度假区
        'B0FFJ2RIHT',  # 上海红十字历史文化陈列馆
        'B00156R4GP',  # 新场历史文化陈列馆
        'B00155NC8X'   # 高桥历史文化陈列馆
    }
    
    # 过滤景点
    cleaned = [a for a in attractions if a['id'] in keep_ids]
    removed_count = original_count - len(cleaned)
    
    # 补充豫园（历史文化+美食代表）
    supplement_count = 0
    if len(cleaned) < 12:
        new_poi = amap.call_tool_json('maps_text_search', {'keywords': '豫园', 'city': '上海市'})
        if new_poi and 'pois' in new_poi and len(new_poi['pois']) > 0:
            detail = amap.call_tool_json('maps_search_detail', {'id': new_poi['pois'][0]['id']})
            if detail:
                cleaned.append({
                    'id': detail['id'],
                    'name': detail['name'],
                    'location': detail['location'],
                    'address': detail.get('address', ''),
                    'city': '上海市',
                    'type': detail.get('type', ''),
                    'rating': detail.get('rating', ''),
                    'open_time': detail.get('open_time', ''),
                    'opentime2': detail.get('opentime2', '')
                })
                supplement_count += 1
    
    # 确保不超过12个
    if len(cleaned) > 12:
        cleaned = cleaned[:12]
    
    # 写回文件
    with open('blocks/output/attractions.json', 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    
    print(f"删除数量: {removed_count}")
    print(f"补充数量: {supplement_count}")
    print(f"最终数量: {len(cleaned)}")
    print(f"文件路径: blocks/output/attractions.json")

if __name__ == '__main__':
    clean_attractions()