# AI助手调试模式使用指南

## 概述

双层调试功能, 零开销设计:

1. **ToolCallTracker** - 记录LangChain Agent的工具调用和LLM执行事件
2. **PromptCapture** - 捕获完整的prompt内容 (记忆上下文+对话历史)

仅在 `DEBUG=true` 时激活, 对生产环境无性能影响.

## 快速开始

```bash
# 启动调试模式
DEBUG=true python scripts/dev_server.py

# 获取API密钥
API_KEY=$(python scripts/api_key_manager.py show alice | grep -o "sk-project-[a-zA-Z0-9_-]*" | head -1)

# 触发调试事件
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"model": "personal-assistant", "messages": [{"role": "user", "content": "测试调试功能"}]}'
```

## 日志文件

```
logs/
├── tool_calls_2026-05-24_00-14-14.json     # 工具调用追踪
└── prompts/
    └── prompt_2026-05-24T00-01-00_alice_main.json  # Prompt捕获
```

### 事件格式

**工具调用** (`tool_start` / `tool_end` / `tool_error`):
```json
{"ts": "2026-05-24T16:24:37+08:00", "type": "tool_start", "data": {"tool_name": "create_todo", "input_preview": "创建任务...", "duration_ms": 290}}
```

**Prompt捕获**:
```json
{"timestamp": "2026-05-24T08:39:21", "user_id": "alice", "thread_id": "main", "system_prompt": "...", "user_content": "..."}
```

## 日志分析

```bash
# 查看最新日志
ls -t logs/tool_calls_*.json | head -1
ls -t logs/prompts/prompt_*.json | head -1

# 过滤事件
grep '"tool_start"' logs/tool_calls_*.json
grep '"tool_error"' logs/tool_calls_*.json

# 性能分析
grep '"duration_ms"' logs/tool_calls_*.json | jq '.data.duration_ms' | sort -n
grep '"tool_name"' logs/tool_calls_*.json | jq -r '.data.tool_name' | sort | uniq -c

# 查找慢调用 (>1s)
grep '"duration_ms"' logs/tool_calls_*.json | jq 'select(.data.duration_ms > 1000)'

# 查看特定用户的prompt
ls logs/prompts/prompt_*_alice_*.json
```

## 编程接口

```python
from scripts.debug.tool_call_tracker import create_tool_call_tracker
from scripts.debug.prompt_capture import PromptCapture

# 自动根据DEBUG环境变量创建
tool_tracker = create_tool_call_tracker()  # 返回ToolCallTracker或[]
prompt_capture = PromptCapture()           # DEBUG=false时自动禁用

# 手动捕获prompt
prompt_capture.capture_prompt(
    user_content="用户输入",
    system_prompt="系统提示词",
    user_id="alice",
    thread_id="main",
    agent_id="personal-assistant",
)
```

## 模块位置

调试工具独立于核心代码, 位于 `scripts/debug/`:

```
scripts/debug/
├── __init__.py
├── tool_call_tracker.py   # 工具调用追踪器
└── prompt_capture.py      # Prompt捕获工具
```

生产代码仅引用 `src/utils/debug_config.py` (环境变量读取), 调试模块完全解耦.

## 常见问题

**日志文件未生成**: 确认 `DEBUG=true` 已设置, 可用 `python -c "import os; print(os.getenv('DEBUG', '').lower() in {'true', '1', 'yes'})"` 验证.

**工具名称显示 unknown**: 已修复. LangChain的 `on_tool_end`/`on_tool_error` 通过 `kwargs["name"]` 传递工具名, 而非 `kwargs["serialized"]["name"]`.

**没有工具调用记录**: 确认Agent配置中启用了工具, 且用户输入触发了工具调用.

**API认证失败**: `python scripts/api_key_manager.py validate alice` 验证密钥.

## 零开销设计

- `DEBUG=false` 时: ToolCallTracker返回空列表, PromptCapture不执行捕获
- 内存占用: 调试模式下只维护轻量级计时器
- 异步日志: 日志写入不阻塞主业务流程

---
**更新日期**: 2026-07-02
**版本**: 3.0 (v1.9.0)
