# Agent Prompt 和工具实现指南

本文档详细说明每个 Agent 的输入数据、输出格式、Prompt 和使用的工具。

---

## 1. TotalAgent (总协调 Agent)

**模型**: SiliconFlow Qwen2.5-7B-Instruct

### 输入数据格式
```json
{
  "user_id": "123",
  "destination": "北京",
  "days": 3,
  "preferences": {
    "景点类型": ["历史", "自然"],
    "酒店级别": "4 星",
    "预算": "中等"
  },
  "mode": "初次规划/修改报告",
  "travel_style": "轻松",
  "start_date": "2026-06-01",
  "free_text": "用户自由输入"
}
```

### 输出数据格式
```json
{
  "action": "plan_trip/modify_report",
  "agent_input": {
    "destination": "北京",
    "days": 3,
    "preferences": {...},
    "user_history": {...}
  }
}
```

### System Prompt
位于：`TotalAgent/prompts/total_agent.py`

核心职责：
- 接收用户请求
- 分析模式（初次规划/修改报告）
- 读取短期记忆和长期记忆
- 调度下游 Agent

### 工具
TotalAgent 不直接调用外部工具，只负责路由和调度。

---

## 2. StrategyAgent (策略 Agent)

**模型**: SiliconFlow Qwen2.5-7B-Instruct

### 输入数据格式
```json
{
  "user_id": "123",
  "destination": "北京",
  "days": 3,
  "preferences": {
    "景点类型": ["历史", "自然"],
    "酒店级别": "4 星",
    "预算": "中等"
  },
  "travel_style": "轻松",
  "mode": "初次规划"
}
```

### 输出数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "scenic_types": ["历史", "自然"],
  "required_info": ["攻略", "酒店", "预算"],
  "query": {
    "destination": "北京",
    "days": 3,
    "scenic_types": ["历史", "自然"],
    "keywords": ["历史", "自然", "攻略", "酒店", "预算"],
    "limit": 6
  },
  "memory": {
    "user_id": "123",
    "preferences": ["历史", "自然"],
    "budget_level": "中等",
    "pace": "轻松"
  }
}
```

### System Prompt
位于：`StrategyAgent/prompts/strategy_agent.py`

核心职责：
- 分析用户输入和历史偏好
- 提取景点类型关键词
- 确定需要搜索的信息类型
- 生成 QueryAgent 使用的标准化查询 JSON

### 景点类型映射
| 用户输入 | 标准类型 |
|---------|---------|
| 历史古迹 | 历史 |
| 自然风光 | 自然 |
| 博物馆/纪念馆 | 博物馆 |
| 美食/小吃 | 美食 |
| 亲子游/儿童 | 亲子 |
| 购物/商圈 | 购物 |
| 夜景/灯光 | 夜景 |
| 户外/徒步 | 户外 |
| 艺术/展览 | 艺术 |

### 信息类型判断
- 必须有："攻略"
- 有 start_date → "最新信息"
- 有酒店要求 → "酒店"
- 有预算要求 → "预算"
- mode 是修改报告 → "局部更新"

---

## 3. QueryAgent (查询 Agent)

**模型**: SiliconFlow Qwen2.5-14B-Instruct

### 输入数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "scenic_types": ["历史", "自然"],
  "required_info": ["攻略", "酒店"],
  "keywords": ["北京", "历史", "自然", "攻略"],
  "limit": 6
}
```

### 输出数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "spots_summary": [
    {
      "name": "故宫",
      "keywords": ["明清皇宫", "历史", "博物馆"],
      "brief_desc": "故宫是中国明清两代的皇家宫殿，是世界上现存规模最大、保存最为完整的木质结构古建筑群。"
    }
  ],
  "raw_summary": "北京旅游攻略总结文本，包含主要景点介绍、游玩建议等...",
  "sources": [
    {
      "title": "北京三日游攻略",
      "url": "https://example.com/guide",
      "snippet": "文章内容摘要..."
    }
  ]
}
```

### System Prompt
位于：`QueryEngine/prompts/query_agent.py`

### 搜索策略
根据景点类型生成搜索词：
- 历史 → "目的地 + 历史古迹 + 攻略"
- 自然 → "目的地 + 自然风光 + 景点"
- 美食 → "目的地 + 美食推荐 + 小吃"
- 博物馆 → "目的地 + 博物馆 + 参观"

### 工具实现：Tavily 搜索

**文件**: `QueryEngine/tools/search.py`

```python
class SearchTool:
    async def search(self, query: str, limit: int) -> List[WebSource]:
        # 1. 选择搜索策略（攻略/实时/社区）
        strategy = self._choose_strategy(query)
        
        # 2. 调用 Tavily API
        payload = {
            "api_key": self.tavily_api_key,
            "query": self._build_query(query, strategy),
            "topic": "general",
            "search_depth": "basic",
            "max_results": limit,
        }
        
        # 3. 发送请求
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json=payload
            )
        
        # 4. 解析结果
        results = response.json().get("results", [])
        return [WebSource(...) for result in results]
```

**环境变量**:
```env
TAVILY_API_KEY=your_tavily_api_key
TAVILY_API_URL=https://api.tavily.com/search
```

**注意**: QueryAgent 使用 Tavily API 进行联网搜索，不使用爬虫。

---

## 4. AnalysisAgent (分析 Agent)

**模型**: SiliconFlow Qwen2.5-32B-Instruct

### 输入数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "spots_summary": [
    {
      "name": "故宫",
      "keywords": ["明清皇宫", "历史", "博物馆"],
      "brief_desc": "故宫是中国明清两代的皇家宫殿..."
    }
  ],
  "raw_summary": "北京旅游攻略总结...",
  "user_preferences": {
    "预算": "中等",
    "旅行风格": "轻松"
  }
}
```

### 输出数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "spots": [
    {
      "name": "故宫",
      "type": "历史",
      "desc": "故宫是中国明清两代的皇家宫殿，是世界上现存规模最大、保存最为完整的木质结构古建筑群。",
      "weather": "晴 25°C",
      "hotel_nearby": ["北京饭店", "王府井希尔顿酒店"],
      "address": "北京市东城区景山前街 4 号",
      "images": ["url1", "url2"],
      "status": "推荐/用户确认/备选/不推荐"
    }
  ],
  "itinerary": {
    "trip_id": "trip_20260511_xxx",
    "summary": "北京 3 日游",
    "days": [
      {
        "day": 1,
        "theme": "历史文化之旅",
        "slots": [
          {
            "time": "上午",
            "title": "故宫博物院",
            "category": "attraction",
            "description": "参观故宫，欣赏古代建筑和艺术珍品",
            "estimated_cost": 60,
            "duration_minutes": 180
          }
        ]
      }
    ]
  }
}
```

### System Prompt
位于：`AnalysisAgent/prompts/analysis_agent.py`

### 景点状态说明
| 状态 | 说明 |
|------|------|
| 推荐 | 符合用户偏好，强烈推荐 |
| 用户确认 | 用户明确要求去的景点 |
| 备选 | 可以作为备选方案 |
| 不推荐 | 不符合用户偏好或当前不适合 |

### 工具实现

#### 1. 高德地图 API

**文件**: `AnalysisAgent/tools/external_api.py`

```python
class AmapTool:
    async def get_place_info(self, name: str, city: str) -> PlaceInfo:
        """查询景点详细信息"""
        url = "https://restapi.amap.com/v3/place/text"
        params = {
            "keywords": name,
            "city": city,
            "key": self.api_key,
        }
        # 返回：地址、经纬度、电话、评分

    async def get_weather(self, city: str) -> WeatherInfo:
        """查询城市天气"""
        url = "https://restapi.amap.com/v3/weather/info"
        params = {
            "city": city,
            "key": self.api_key,
        }
        # 返回：天气、温度、风向、湿度

    async def get_hotels_nearby(self, location: str, radius: int) -> List[HotelInfo]:
        """查询附近酒店"""
        url = "https://restapi.amap.com/v3/place/text"
        params = {
            "keywords": "酒店",
            "location": location,  # 经度，纬度
            "radius": radius,
            "key": self.api_key,
        }
        # 返回：酒店名称、地址、价格范围、评分、距离
```

**环境变量**:
```env
AMAP_API_KEY=your_amap_api_key
```

#### 2. Unsplash API

```python
class UnsplashTool:
    async def search_images(self, query: str, limit: int) -> List[str]:
        """搜索图片"""
        url = "https://api.unsplash.com/search/photos"
        params = {
            "query": query,
            "per_page": limit,
        }
        headers = {
            "Authorization": f"Client-ID {access_key}",
        }
        # 返回：图片 URL 列表
```

**环境变量**:
```env
UNSPLASH_ACCESS_KEY=your_unsplash_key
```

#### 3. 备用天气 API (OpenWeatherMap)

```python
class WeatherTool:
    async def get_weather(self, city: str) -> WeatherInfo:
        """查询天气（备用）"""
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": api_key,
            "units": "metric",
            "lang": "zh_cn",
        }
        # 返回：天气描述、温度
```

**环境变量**:
```env
OPENWEATHER_API_KEY=your_openweather_key
```

---

## 5. ReportAgent (报告 Agent)

**模型**: SiliconFlow Qwen2.5-7B-Instruct

### 输入数据格式
```json
{
  "destination": "北京",
  "days": 3,
  "spots": [
    {
      "name": "故宫",
      "type": "历史",
      "desc": "...",
      "weather": "晴 25°C",
      "hotel_nearby": ["北京饭店"],
      "address": "...",
      "images": ["url1", "url2"],
      "status": "推荐"
    }
  ],
  "itinerary": {
    "trip_id": "trip_xxx",
    "summary": "北京 3 日游",
    "days": [...]
  }
}
```

### 输出数据格式
```json
{
  "html_report": "<!DOCTYPE html><html>...</html>",
  "modifiable_spots": ["故宫", "颐和园"],
  "artifacts": [
    {
      "type": "html",
      "path": "/path/to/report.html",
      "url": "/api/v1/reports/trip_xxx.html",
      "generated_at": "2024-01-01T12:00:00"
    }
  ]
}
```

### System Prompt
位于：`ReportEngine/prompts/report_agent.py`

### HTML 报告结构要求
1. 响应式设计，支持移动端和桌面端
2. 包含部分：
   - 标题：目的地 + 天数
   - 行程概览
   - 每日详细行程
   - 景点列表（带图片）
   - 实用信息（天气、酒店、交通）
3. 使用内联 CSS
4. UTF-8 编码

### 工具实现：HTML 渲染器

**文件**: `ReportEngine/tools/html_renderer.py`

```python
class HtmlReportRenderer:
    def render(self, plan: ItineraryPlan) -> str:
        # 1. 生成 HTML 头部和样式
        html = """<!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <title>{destination} {days}-Day Travel Plan</title>
          <style>...</style>
        </head>
        <body>...
        """
        
        # 2. 生成每日行程
        for day in plan.itinerary:
            html += self._render_day(day)
        
        # 3. 添加亮点、餐厅、提示
        html += self._render_sections(plan)
        
        return html
```

---

## 6. Memory System (记忆系统)

### 短期记忆
**存储**: 内存/Redis
**生命周期**: 当前会话（1 小时）

```python
{
  "user_id": "123",
  "current_action": "plan_trip",
  "destination": "北京",
  "days": 3,
  "spots": [...],
  "trip_id": "trip_20260511_xxx"
}
```

### 长期记忆
**存储**: SQLite
**生命周期**: 持久化

```python
{
  "user_id": "123",
  "preferences": ["历史", "自然", "美食"],
  "disliked": ["主题公园"],
  "budget_level": "中等",
  "pace": "轻松",
  "notes": "喜欢早起出行",
  "history": [...]
}
```

### 接口
**文件**: `Memory/__init__.py`

```python
class Memory:
    def get_short_term(self, user_id: str) -> dict
    def set_short_term(self, user_id: str, data: dict)
    def get_long_term(self, user_id: str) -> PreferenceMemory
    def set_long_term(self, user_id: str, data: dict)
    def record_trip(self, user_id: str, trip_data: dict)
    def get_trip_history(self, user_id: str, limit: int) -> list
```

---

## 环境变量配置

```env
# SiliconFlow API
SILICONFLOW_API_KEY=sk-xxx
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

# 各 Agent 模型（可选）
TOTAL_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct
STRATEGY_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct
QUERY_AGENT_MODEL=Qwen/Qwen2.5-14B-Instruct
ANALYSIS_AGENT_MODEL=Qwen/Qwen2.5-32B-Instruct
REPORT_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct

# Tavily 搜索（QueryAgent 使用）
TAVILY_API_KEY=xxx
TAVILY_API_URL=https://api.tavily.com/search

# 高德地图（AnalysisAgent 使用）
AMAP_API_KEY=xxx

# Unsplash 图片（AnalysisAgent 使用）
UNSPLASH_ACCESS_KEY=xxx

# 天气 API（备用）
OPENWEATHER_API_KEY=xxx

# Redis（可选，用于短期记忆）
REDIS_URL=redis://localhost:6379/0
```

---

## 数据流总结

```
用户请求
    │
    ▼
TotalAgent (路由)
    │
    ├─→ [读取记忆]
    │
    ▼
StrategyAgent (策略)
    │
    ├─→ 生成查询 JSON
    │
    ▼
QueryAgent (查询)
    │
    ├─→ Tavily 搜索
    │
    ▼
AnalysisAgent (分析)
    │
    ├─→ 高德地图 API (地点、天气、酒店)
    ├─→ Unsplash API (图片)
    │
    ▼
ReportAgent (报告)
    │
    ├─→ HTML 生成
    │
    ▼
[更新记忆] → 返回响应
```

---

## 下一步

1. **提供 API Key**: 配置 `.env` 文件中的各项 API Key
2. **测试流程**: 运行完整流程测试各 Agent 协作
3. **优化 Prompt**: 根据实际测试结果调整 Prompt
4. **错误处理**: 添加 API 调用失败的降级逻辑
