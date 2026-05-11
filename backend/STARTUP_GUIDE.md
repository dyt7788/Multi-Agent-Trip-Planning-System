# 启动指南

## 环境配置

### 1. 设置系统环境变量

你提到已经将 API Key 保存到用户环境变量中了，请确保包含以下必需的配置：

**必需的环境变量：**
```
SILICONFLOW_API_KEY=sk-your-api-key-here
TAVILY_API_KEY=your-tavily-api-key
```

**可选的环境变量（增强功能）：**
```
AMAP_API_KEY=your-amap-api-key
UNSPLASH_ACCESS_KEY=your-unsplash-key
OPENWEATHER_API_KEY=your-openweather-key
REDIS_URL=redis://localhost:6379/0
```

### 2. 验证环境变量

在命令行中运行以下命令验证环境变量是否设置成功：

**Windows PowerShell:**
```powershell
echo $env:SILICONFLOW_API_KEY
echo $env:TAVILY_API_KEY
```

**Windows CMD:**
```cmd
echo %SILICONFLOW_API_KEY%
echo %TAVILY_API_KEY%
```

如果显示的是实际的值（不是空），说明环境变量设置成功。

---

## 启动服务

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动 FastAPI 服务

```bash
python run.py
```

或者使用 uvicorn 直接启动：

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 访问服务

服务启动后，访问以下地址：

- **API 文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health

---

## 测试流程

### 方法一：使用测试脚本

```bash
# 运行简单测试（推荐）
python test_simple.py

# 运行完整流程测试
python test_full_flow.py
```

### 方法二：使用 API 文档界面

1. 访问 http://localhost:8000/docs
2. 找到 `POST /api/v1/trips/plan` 端点
3. 点击 "Try it out"
4. 输入以下测试数据：

```json
{
  "user_id": "test_001",
  "destination": "苏州",
  "days": 2,
  "preferences": {
    "景点类型": ["历史", "园林", "自然"],
    "预算": "中等",
    "旅行风格": "轻松"
  },
  "mode": "初次规划",
  "travel_style": "轻松"
}
```

5. 点击 "Execute" 执行请求

### 方法三：使用 curl 命令

```bash
curl -X POST http://localhost:8000/api/v1/trips/plan \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"test_001\",
    \"destination\": \"苏州\",
    \"days\": 2,
    \"preferences\": {
      \"景点类型\": [\"历史\", \"园林\", \"自然\"],
      \"预算\": \"中等\"
    },
    \"mode\": \"初次规划\"
  }"
```

---

## 预期结果

### 成功响应示例

```json
{
  "success": true,
  "message": "Trip plan generated.",
  "total": {
    "action": "plan_trip",
    "agent_input": {...}
  },
  "strategy": {
    "destination": "苏州",
    "days": 2,
    "scenic_types": ["历史", "园林", "自然"],
    "required_info": ["攻略"],
    "query": {...}
  },
  "query": {
    "spots_summary": [
      {"name": "拙政园", "keywords": ["园林"], "brief_desc": "..."},
      {"name": "虎丘", "keywords": ["历史"], "brief_desc": "..."}
    ],
    "sources": [...]
  },
  "analysis": {
    "spots": [
      {
        "name": "拙政园",
        "type": "园林",
        "status": "推荐",
        "desc": "..."
      }
    ]
  },
  "report": {
    "html_report": "<!DOCTYPE html>...",
    "modifiable_spots": ["拙政园", "虎丘"]
  },
  "plan": {
    "trip_id": "trip_20260511_xxx",
    "destination": "苏州",
    "days": 2,
    "itinerary": [...]
  }
}
```

---

## 故障排查

### 问题 1: 环境变量未生效

**症状**: 测试脚本显示 "未配置" 警告

**解决方案**:
1. 确认环境变量已正确设置
2. 重启命令行/终端窗口
3. 如果使用 IDE，重启 IDE

### 问题 2: Tavily API 调用失败

**症状**: QueryAgent 返回 fallback 结果

**解决方案**:
1. 检查 `TAVILY_API_KEY` 是否正确
2. 访问 https://app.tavily.com/ 验证 API Key
3. 检查网络连接

### 问题 3: SiliconFlow API 调用失败

**症状**: LLM 返回 fallback 结果或空响应

**解决方案**:
1. 检查 `SILICONFLOW_API_KEY` 是否正确
2. 访问 https://cloud.siliconflow.cn/ 验证 API Key 余额
3. 检查 API  endpoint: https://api.siliconflow.cn/v1/chat/completions

### 问题 4: 数据库文件不存在

**症状**: Memory 系统报错

**解决方案**:
```bash
# 手动创建数据目录
mkdir -p data
```

---

## 下一步

1. **配置所有 API Key**: 确保 SILICONFLOW_API_KEY 和 TAVILY_API_KEY 已配置

2. **测试完整流程**: 运行 `python test_simple.py` 验证所有 Agent

3. **查看日志**: 启动服务时添加 `--log-level debug` 查看详细日志

4. **自定义 Prompt**: 根据需要修改各 Agent 的 Prompt 文件

5. **集成外部 API**: 配置 AMAP_API_KEY 和 UNSPLASH_ACCESS_KEY 增强功能

---

## 参考文档

- [README.md](./README.md) - 项目说明
- [PROMPT_AND_TOOLS_GUIDE.md](./PROMPT_AND_TOOLS_GUIDE.md) - Prompt 和工具实现
- [完成总结.md](./完成总结.md) - 实现总结
