# 配置系统

**版本**: v2 | **更新**: 2026-07-02

本项目配置体系已经收口为五类来源, 不再使用“环境变量覆盖任意配置字段”的三层口径。

## 来源边界

| 来源 | 放什么 | 入口 |
|------|--------|------|
| `config.yaml` | 非敏感、稳定的应用行为配置 | `src/config/*_config.py` |
| `agent.yaml` | Agent 身份、提示词、模型选择、工具分配、记忆预算 | `src/config/agent_config.py` + `AgentFactory` |
| `.env` | 密钥、provider 端点、部署拓扑、测试/调试开关 | `runtime_env.py`, `credentials_registry.py`, `provider_registry.py` |
| catalog/registry | 内置工具、模型/provider 元数据、凭据变量名 | `tool_catalog.py`, `provider_registry.py`, `credentials_registry.py` |
| `static_users.yaml` | 静态用户和 API Key 映射 | `src/auth/static_users.yaml` |

## 强制规则

- 新增业务配置: 先在对应 `*_config.py` 定义 Pydantic 字段, 再更新 `docs/config-yaml-reference.md` 和测试。
- 新增运行时环境变量: 只能加到 `src/config/runtime_env.py` 的白名单和具名 helper。
- 新增密钥: 只能加到 `credentials_registry.py` 或 provider registry, 不进 `config.yaml`。
- 新增 provider: 更新 `provider_registry.py` 和模型元数据。
- 新增内置工具: 更新 `tool_catalog.py`; `config.yaml` 只覆盖 enabled/timeout/config/prompt_hint。
- 禁止在 `src/` 里新增裸 `os.getenv` / `os.environ`。例外仅限 `runtime_env.py`, `credentials_registry.py`, `provider_registry.py`, 以及 MCP/YAML 占位符解析器。

## 校验与迁移

```bash
python scripts/config_doctor.py --validate
python scripts/config_doctor.py --strict --check-env
python scripts/config_doctor.py --migrate --write
```

`--migrate --write` 会先生成 `config.yaml.bak-YYYYMMDD-HHMMSS`, 再迁移本机未入库的 `config.yaml`。工具不会打印 secret 值。

配置治理检查已接入静态分析与 CI 测试套件:

```bash
python scripts/static_analysis.py          # 核心模式(默认)含 config_doctor
python scripts/run_test_suite.py --quick   # config_doctor 为 CI 硬门禁
```

注: `--codex` 模式跳过 config_doctor, 保持 sandbox 轻量.

> ⚠️ **生产部署前必检 `.env`**: `config.yaml` 已纳入版本控制, `git pull` 自动同步, CI 通过 `config_doctor --strict` 真正覆盖生产配置校验。但 `.env` (密钥/Provider 端点/部署拓扑/SMTP 凭据) 仍 `.gitignore` 忽略, 各环境独立维护, 是高频故障源——例如 `SMTP_HOST` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_ADDRESS` 漏配(config.yaml.smtp 段全部留空走 .env 回退)会导致定时消息邮件渠道**静默失败**; `OPENCLAW_GATEWAY_TOKEN` / `notification_defaults` 漏配会导致微信补发/定时消息**静默失败**。**每次生产部署前, 在宿主机跑 `python scripts/config_doctor.py --strict` 自检, 并人工核对 `.env` 与 `.env.example` 的字段差异。**

## 环境变量分类

运行时/部署:
`ENVIRONMENT`, `DEBUG`, `BASE_DATA_PATH`, `PYTEST_XDIST_WORKER_ID`, `TEST_PROCESS_PREFIX`, `API_PORT`, `ENABLE_STATIC_USER_MANAGEMENT`, `ENABLE_TOOL_CALL_DISPLAY`, `FILE_SERVER_BASE_URL`, `FILE_URL_TTL_DAYS`, `TOOL_RUNTIME_BASE_URL`, `QUOTE_SERVICE_BASE_URL`, `OPENCLAW_GATEWAY_URL`.

密钥:
`FILE_SIGNING_SECRET`, `OPENCLAW_GATEWAY_TOKEN`, `BAIDU_API_KEY`, `ZHIPU_API_KEY`, `ARK_AGENT_PLAN_API_KEY`, provider API keys, 地图 API keys, SMTP credentials.

Provider 端点:
各 `*_BASE_URL` 由 `provider_registry.py` 管理, 只覆盖 provider endpoint, 不覆盖应用配置字段。

## 常用入口

- API: `from src.config.api_config import get_config`
- Auth: `from src.config.auth_config import get_config`
- Core: `from src.config.core_config import get_config`
- Storage: `from src.config.storage_config import get_config`
- Inference: `from src.config.inference_config import get_config`
- Tools: `from src.config.tools_config import get_config`
- Retry: `from src.config.retry_config import get_retry_config`
- Logging: `from src.config.logging_config import get_logging_config`
- SMTP: `from src.config.smtp_config import get_config`

完整 YAML 字段见 [config-yaml-reference.md](./config-yaml-reference.md)。
