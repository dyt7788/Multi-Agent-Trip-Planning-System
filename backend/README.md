# 多 Agent 智能旅行规划助手后端

基于 SiliconFlow 多模型的多 Agent 协作旅行规划系统。

## 架构概览

```
用户请求 → TotalAgent(7B) → StrategyAgent(7B) → QueryAgent(14B) → AnalysisAgent(32B) → ReportAgent(7B)
                                                              ↓
                                                          Memory (短期 + 长期)
```

## Agent 列表

| Agent | 模型 | 职责 |
|-------|------|------|
| TotalAgent | Qwen2.5-7B | 总协调、调度下游、记忆管理 |
| StrategyAgent | Qwen2.5-7B | 分析用户意图、生成查询 JSON |
| QueryAgent | Qwen2.5-14B | Tavily 搜索、攻略总结 |
| AnalysisAgent | Qwen2.5-32B | 景点分析、行程规划 |
| ReportAgent | Qwen2.5-7B | HTML 报告生成 |
| ImageAgent | (可选) | 图片分析 |

## 快速启动

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 配置 SILICONFLOW_API_KEY
python run.py
```

## API 使用

```bash
curl -X POST http://localhost:8000/api/v1/trips/plan \
  -H "Content-Type: application/json" \
  -d '{"user_id":"123","destination":"北京","days":3,"preferences":{"景点类型":["历史","自然"]},"mode":"初次规划"}'
```

## 目录结构

```
backend/
├── app/                    # FastAPI 主应用
│   ├── api/
│   │   └── routes/v1.py
│   ├── models/
│   │   └── schemas.py
│   └── config.py
├── TravelCore/             # 核心共享模块
│   ├── llm.py              # SiliconFlow 客户端
│   └── text.py
├── Memory/                 # 记忆系统
├── TotalAgent/             # 总协调 Agent
├── StrategyAgent/          # 策略 Agent
├── QueryEngine/            # 查询引擎
├── AnalysisAgent/          # 分析 Agent
├── ReportEngine/           # 报告引擎
└── ImageAgent/             # 图片分析 (可选)
```

## 配置

```env
# SiliconFlow API 配置
SILICONFLOW_API_KEY=sk-xxx
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

# 可选：各 Agent 独立模型
TOTAL_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct
STRATEGY_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct
QUERY_AGENT_MODEL=Qwen/Qwen2.5-14B-Instruct
ANALYSIS_AGENT_MODEL=Qwen/Qwen2.5-32B-Instruct
REPORT_AGENT_MODEL=Qwen/Qwen2.5-7B-Instruct

# 外部服务 (可选)
TAVILY_API_KEY=xxx
AMAP_API_KEY=xxx
UNSPLASH_ACCESS_KEY=xxx
```

## 参考

- [SiliconFlow 官网](https://www.siliconflow.cn/)
- [SiliconFlow API 文档](https://docs.siliconflow.cn/)
- [Prompt 和工具实现指南](./PROMPT_AND_TOOLS_GUIDE.md)
