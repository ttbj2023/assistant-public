# Reranker 外部服务

独立的 Qwen3-Reranker-0.6B 推理服务, 部署在 assistant 同机, 经 HTTP 提供文档重排能力. 本文档是 assistant 项目侧的概览与接入指南.

## 是什么

- **模型**: `Qwen/Qwen3-Reranker-0.6B` (0.6B 参数, 32K context, Apache-2.0)
- **架构**: 生成式 reranker (`Qwen3ForCausalLM`), 通过 `yes` / `no` logprob 计算相关性, **不是** SequenceClassification
- **协议**: Jina/Cohere 风格 `POST /rerank` (OpenAI 无 rerank 标准端点)
- **项目位置**: 独立仓库, 不在 assistant 内
  - WSL 开发机: `/home/workspace/reranker-service/`
  - Mac 生产: `~/project/reranker-service/`

## 部署形态

**host venv + 系统服务自启** (非 Docker). 原因: Apple Silicon Docker 容器是 Linux ARM64, 拿不到 Apple AMX/Metal, 30 docs 推理需 16.8s; 改 host 部署后通过 Accelerate/Metal 降到 1.4s (**12x 提速**).

| 环境 | 部署方式 | 推理设备 | 监听 |
|---|---|---|---|
| Mac M4 生产 | launchd | MPS (Metal) | `127.0.0.1:8768` |
| x86 开发机 | systemd --user (linger) | CPU | `127.0.0.1:8768` |

均 `enabled` + linger/RunAtLoad, 重启机器自动起来.

## 接口

### `POST /rerank` (核心)

```http
POST /rerank
Content-Type: application/json

{
  "query": "...",
  "documents": ["...", "..."],
  "top_n": 3,
  "return_documents": true,
  "instruction": "Given a web search query, retrieve relevant passages that answer the query"
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `query` | 是 | - | 用户查询 |
| `documents` | 是 | - | 候选文档列表, 至少 1 条 |
| `top_n` | 否 | 全部 | 返回前 N 条, 按 score 降序 |
| `return_documents` | 否 | `true` | 是否回带 `document` 原文 |
| `instruction` | 否 | 通用检索 | 任务指令 (建议英文, 1-5% 精度影响) |
| `model` | 否 | 占位 | 目前忽略 |

响应:

```json
{
  "model": "Qwen/Qwen3-Reranker-0.6B",
  "results": [
    {"index": 0, "relevance_score": 0.9995, "document": "..."},
    {"index": 2, "relevance_score": 0.0142, "document": "..."}
  ],
  "usage": {"prompt_tokens": 337}
}
```

关键约定:
- `results` 按 `relevance_score` **降序**
- `index` 指向原始 `documents` 数组位置 (不随排序变)
- score 是 `[0,1]` sigmoid 概率, 越大越相关
- `return_documents=false` 时 `document` 字段省略

### 辅助端点

- `GET /health` → `{"status":"ok","model":"...","loaded":true}` (未加载完返回 503)
- `GET /models` → OpenAI 兼容占位

### 错误响应

```json
{"detail": {"error": "invalid_request", "message": "documents must not be empty"}}
```

| HTTP | error | 场景 |
|---|---|---|
| 400 | `invalid_request` | 业务校验失败 |
| 422 | `validation_error` | pydantic 校验失败 |
| 500 | `internal_error` | 推理异常 |
| 503 | `model_loading` | 启动加载中 |

## 性能 (实测, warm)

| 场景 | M4 host MPS | x86 host CPU |
|---|---|---|
| 1 query x 1 doc | ~130ms | ~290ms |
| 1 query x 4 docs | ~310ms | ~500ms |
| 1 query x 10 docs | ~530ms | ~920ms |
| 1 query x 30 docs | **~1.4s** | **~2.6s** |
| 模型加载 (启动) | ~1.3s | ~2s |

客户端建议超时 **10s** (30 docs 实测 1.4s, 余量充足).

## assistant 接入策略

**关键决策: 分工具接入, 不是全量开关**.

| 工具 | 是否接 rerank | 原因 |
|---|---|---|
| `search_memories` | **不接** | 双路检索 (SQL + 向量) 的 `smart_deduplication` 已经按向量分排序, rerank 冗余. 且该工具一轮可被 LLM 多次并行调用, 任何额外延迟都放大 N 倍. |
| `tea_knowledge` / `knowledge_base` | **接** | cross-encoder 对长文档 chunks 精排价值显著; 调用低频 + 有 semantic_cache 兜底, 单次 1-2s 可接受. |

**当前状态**: `inference.reranker.enabled = false` (默认关). 接入点已就绪:
- `src/knowledge_base/retriever.py:49` + `src/tools/external/tea_knowledge_tool.py:128` (知识库路径)

启用只需配 `.env` 的 `RERANKER_BASE_URL=http://127.0.0.1:8768` 并打开 `enabled`, **search_memories 路径仍不传 reranker** (落实分治策略).

## 常用运维

**Mac (launchd):**
```bash
launchctl list | grep reranker                                            # 状态
launchctl unload ~/Library/LaunchAgents/com.reranker-service.plist       # 停
launchctl load ~/Library/LaunchAgents/com.reranker-service.plist         # 启
tail -f ~/project/reranker-service/logs/launchd.err.log                  # 日志
```

**Linux (systemd --user):**
```bash
systemctl --user status reranker-service        # 状态
systemctl --user restart reranker-service       # 重启
journalctl --user -u reranker-service -f        # 日志
```

## 升级模型 / 改代码

reranker-service 是独立项目, 改动**不影响 assistant**:
- 模型升级: `cd ~/project/reranker-service && .venv/bin/python scripts/download_model.py` 重下, 重启服务
- 代码变更: 改 `server.py` 后 `systemctl --user restart reranker-service` (或 launchd 等价命令)
- WSL ↔ Mac 同步: `rsync -avz --exclude={models,logs,.venv} /home/workspace/reranker-service/ mac:'~/project/reranker-service/'`
