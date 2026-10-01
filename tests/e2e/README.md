# E2E 测试

## 设计理念

E2E 测试聚焦于 **集成/单元测试无法覆盖的 HTTP 边界 + Agent 管线完整链路**.
每 个用例必须提供独立价值, 否则不保留.

与手动 E2E (`/jack-chat` 使用真实 LLM) 互补: 自动化 E2E 使用可编程 Mock LLM,
快速 (≤30s/用例), 稳定, 无外部 API 依赖, 为 CI 提供基线保障.

## 架构

- **进程内 ASGI TestClient**: `httpx.ASGITransport(app=fastapi_app)`, 无子进程/无网络
- **E2EMockLLM**: 可编程 Mock, 支持 `bind_tools`/`ainvoke`, 通过 `set_script()` 注入
  预设 AIMessage 序列 (含 tool_calls), 触发真实 Agent 工具循环
- **Mock 范围**: LLM + Embeddings + ContentAnalyzer (环境变量控制), 真实运行
  FastAPI + Agent + Service + DAO + SQLite + ChromaDB
- **串行执行**: `-n 0` 避免共享 test_data 目录竞态

## 用例清单 (19 用例)

| 文件 | 用例 | 独特价值 |
|------|------|----------|
| `test_agent_pipeline_e2e.py` | `test_agent_tool_loop_executes_and_persists` | HTTP→Agent→LLM(tool_calls)→create_todo→DB. 灰盒读取 todo.db |
| `test_agent_pipeline_e2e.py` | `test_implicit_intent_searches_dormant_todo_and_persists` | 全折叠: 隐式记录意图经 search 唤醒休眠 todo 组 → 中间件注入 → create_todo 落库 |
| `test_agent_pipeline_e2e.py` | `test_conversation_history_assembled_across_requests` | 2次HTTP请求, 捕获LLM输入验证历史组装 |
| `test_streaming_api.py` | `test_streaming_concurrent_clients` | 3并发流式请求 ASGI 并发 |
| `test_streaming_api.py` | `test_streaming_long_input` | 2000字符流式处理 |
| `test_api_smoke_e2e.py` | `test_server_health_and_agent_registry` | /health + /v1/models |
| `test_api_smoke_e2e.py` | `test_invalid_request_returns_error` | 422 错误边界 |
| `test_memory_retrieval_tool_e2e.py` | `test_e2e_search_memories_runs_in_agent_loop` | LLM 触发 search_memories → 真实 DualStageRetrievalService → 结果反馈进下一轮 LLM |
| `test_channel_gateway_contract_e2e.py` | `test_channel_headers_provision_wechat_config` | X-Channel 请求头 → 渠道配置自动发现并持久化 |
| `test_channel_gateway_contract_e2e.py` | `test_channel_config_self_heals_on_change` | 渠道账号字段变化 → 配置自愈更新 |
| `test_channel_gateway_contract_e2e.py` | `test_request_without_channel_headers_skips_provisioning` | 无渠道头 → 跳过配置发现 |
| `test_document_input_e2e.py` | `test_file_block_inlined_to_llm_and_marker_persisted` | 小文档内联 `<document>` 块, 持久化仅存 `[file: id]` 标记 |
| `test_document_input_e2e.py` | `test_large_document_marker_only_in_prompt` | 超长文档仅注入标记, 不污染主历史 |
| `test_document_input_e2e.py` | `test_mixed_image_and_file_blocks` | 图文+文档混合 ContentBlock 编排 |
| `test_document_input_e2e.py` | `test_code_file_stored_with_summary_structure` | 代码文件入库 desc=摘要+源码 结构 |
| `test_tool_runtime_container_e2e.py` | `test_e2e_execute_python` | PythonExecutorTool → 真 /execute: print(2+2) 返回 stdout=4 |
| `test_tool_runtime_container_e2e.py` | `test_e2e_render_chart_mermaid` | ChartMaker → 真 /render/chart: mermaid 源码 → 合法 PNG |
| `test_tool_runtime_container_e2e.py` | `test_e2e_export_document_docx` | ExportDocument → 真 /convert/pandoc: Markdown → 合法 DOCX (zip) |
| `test_tool_runtime_container_e2e.py` | `test_e2e_export_document_pdf` | ExportDocument → 真 /convert/pandoc + /render/pdf: Markdown → 合法 PDF |

## E2EMockLLM 用法

```python
from tests.e2e.mock_llm import E2EMockLLM
from langchain_core.messages import AIMessage

# 注入脚本: 第1条带 tool_calls 触发工具, 第2条空 tool_calls 终止循环
E2EMockLLM.set_script([
    AIMessage(content="", tool_calls=[{
        "name": "create_todo",
        "args": {"title": "买牛奶"},
        "id": "call_1",
        "type": "tool_call",
    }]),
    AIMessage(content="已创建待办：买牛奶", tool_calls=[]),
])

# 执行 HTTP 请求...

# 捕获最后一次 LLM 输入 (验证 prompt 组装)
last_input = E2EMockLLM.get_last_input()
```

## 运行

```bash
cd tests/e2e && python -m pytest -v
```
