# 基于HelloAgents的智能旅行助手 🌍✈️

基于HelloAgents框架构建的智能旅行规划助手,集成高德地图MCP服务,提供个性化的旅行计划生成。

## ✨ 功能特点

- 🤖 **AI驱动的旅行规划**: 基于HelloAgents框架的SimpleAgent,智能生成详细的多日旅程
- 🗺️ **高德地图集成**: 通过MCP协议接入高德地图服务,支持景点搜索、路线规划、天气查询
- 🧠 **智能工具调用**: Agent自动调用高德地图MCP工具,获取实时POI、路线和天气信息
- 🧩 **交互方式**: Streamlit AiPy聊天界面
- 📱 **完整功能**: 包含住宿、交通、餐饮和景点游览时间推荐

## 🏗️ 技术栈

### 后端
 - Python 3.10+
 - HelloAgents (SimpleAgent/MCPTool)
 - AMap MCP Server (POI/路线/天气)
 - Streamlit (AiPy交互)
 - ChromaDB (对话存储,可选)
 - OpenAI SDK (兼容SiliconFlow/自定义Base URL)

### 前端
 - 本仓库未包含独立前端工程


## 📁 项目结构

```
Multi-Agent-trip-planner-main/
├─ backend/
│  ├─ app/
│  │  ├─ agents/            # 多智能体行程规划
│  │  ├─ aipy/               # AiPy代码生成/执行代理
│  │  ├─ models/             # Pydantic模型
│  │  └─ services/           # AMap/LLM/对话存储
│  ├─ blocks/                # 生成脚本与产物
│  │  └─ output/             # attractions/hotels/itinerary等
│  ├─ data/                  # Chroma本地持久化
│  ├─ streamlit_app.py       # AiPy聊天界面
│  └─ requirements.txt
└─ README.md

```

## 🚀 快速开始

### 前提条件

- Python 3.10+
- Node.js 16+
- 高德地图API密钥 (Web服务API和Web端(JS API))
- LLM API密钥 (OpenAI/DeepSeek等)

### 后端安装

1. 进入后端目录
```bash
cd backend
```

2. 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

> 注意: 本项目依赖 `MCPTool`, 请确保 `hello-agents` 为 `0.x` 版本(例如 `0.2.4`),避免安装到 `1.x` 导致 `ImportError: MCPTool`。

4. 配置环境变量
```bash
cp .env.example .env
# 编辑.env文件,填入你的API密钥
```

建议配置项(示例):
- `AMAP_API_KEY` (必填)
- `SILICONFLOW_API_KEY` （必填或者其它大模型调用api）
- `SILICONFLOW_BASE_URL` / `LLM_BASE_URL` / `OPENAI_BASE_URL` (可选)
- `SILICONFLOW_MODEL` / `LLM_MODEL_ID` / `OPENAI_MODEL` (可选)
- `UNSPLASH_ACCESS_KEY` / `UNSPLASH_SECRET_KEY` (可选,用于HTML渲染)

> 注意: `.env.example` 中的值仅为示例,请替换为你自己的密钥。

1. 启动Streamlit聊天界面
```bash
streamlit run streamlit_app.py
```

1. 打开浏览器访问 `http://localhost:8501`

## 🤖 AiPy 模式 (代码生成与执行)

如果你希望使用“LLM + Python代码生成与执行”的Agent交互模式(类似Code Interpreter),可以直接运行Streamlit应用:

1. 进入后端目录
```bash
cd backend
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

3. 配置 `backend/.env`
```bash
SILICONFLOW_API_KEY=your_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3.6-35B-A3B
```

4. 启动Streamlit聊天界面
```bash
streamlit run streamlit_app.py
```

5. 打开浏览器访问 `http://localhost:8501`


### 多智能体协作流程
- 景点搜索Agent: 使用 `amap_maps_text_search` 获取候选景点
- 景点复审Agent: 去重/过滤无关POI,必要时补充搜索
- 天气查询Agent: 使用 `amap_maps_weather`
- 酒店推荐Agent: 基于景点位置检索酒店
- 行程规划Agent: 汇总景点/天气/酒店生成结构化行程

### MCP工具调用

Agent可以自动调用以下高德地图MCP工具:
- `maps_text_search`: 搜索景点POI
- `maps_weather`: 查询天气
- `maps_direction_walking_by_address`: 步行路线规划
- `maps_direction_driving_by_address`: 驾车路线规划
- `maps_direction_transit_integrated_by_address`: 公共交通路线规划

### AiPy 产物说明
- 生成脚本位于 `backend/blocks/`
- 主要输出位于 `backend/blocks/output/`:
   - `attractions.json` / `hotels.json` / `weather.json`
   - `itinerary_plan.json` / `itinerary.html`
   - `artifacts_manifest.json` (产物清单)

