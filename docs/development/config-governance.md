# 配置治理规范

**更新**: 2026-06-30

## 新增配置决策树

1. 这个值是否是密钥、token、password、API key?
   放 `.env`, 并登记到 `credentials_registry.py` 或 `provider_registry.py`.

2. 这个值是否描述部署拓扑或测试/调试运行环境?
   放 `.env`, 并在 `runtime_env.py` 增加白名单和具名 helper.

3. 这个值是否属于某个 Agent 的身份、提示词、工具分配、记忆预算或主模型选择?
   放对应 `agent.yaml`.

4. 这个值是否是内置工具的代码定义、类路径、默认描述或默认组成员?
   放 `tool_catalog.py`.

5. 这个值是否是非敏感、稳定的应用行为参数?
   放 `config.yaml` 对应的 Pydantic config 模块.

无法归类时, 先补充本规范, 再编码。

## 禁止事项

- 禁止在 `src/` 新增裸 `os.getenv`, `os.environ.get`, `os.environ[...]`.
- 禁止把 secret 写入 `config.yaml`, 文档、日志或测试 fixture.
- 禁止新增“通用 env overlay”或 `TOOLS_...` 这类任意嵌套字段覆盖机制.
- 禁止在业务代码里手写读取 `config.yaml`; 必须走 `src/config/*_config.py`.
- 禁止在 `agent.yaml` 引用未注册工具、工具组或 skill.

## 必做项

新增或修改配置时同步完成:

- Pydantic schema 或 registry/catalog.
- `docs/configuration.md` 或 `docs/config-yaml-reference.md`.
- 单元测试.
- `python scripts/config_doctor.py --strict --check-env`.

## 常见变更模板

新增业务字段:

1. 修改对应 `src/config/*_config.py`.
2. 更新 `docs/config-yaml-reference.md`.
3. 添加单元测试验证 YAML 覆盖和非法值.

新增密钥:

1. 修改 `credentials_registry.py`.
2. 更新 `.env.example`.
3. 调用方使用 `get_credential()` / `require_credential()`.

新增 runtime env:

1. 修改 `runtime_env.py` 的白名单和 helper.
2. 更新 `.env.example`.
3. 调用方使用 helper, 不直接读取 `os.getenv`.

新增内置工具:

1. 实现工具类.
2. 在 `tool_catalog.py` 增加默认定义.
3. 如需被 Agent 使用, 更新对应 `agent.yaml`.
4. 运行 `config_doctor --strict`.
