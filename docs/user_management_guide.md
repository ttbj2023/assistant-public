# 用户管理指南

基于 API Key 的静态用户管理架构, 提供安全、可预测的用户访问控制.

## 核心特性

- **API Key 认证**: Bearer Token / X-API-Key Header / Query Parameter 三种传递方式
- **静态配置**: YAML 配置文件管理用户和权限 (`src/auth/static_users.yaml`)
- **用户-线程隔离**: 每个 API Key 绑定特定用户和线程, 文件系统级隔离
- **使用审计**: API Key 使用统计与追踪

## 配置

```bash
ENABLE_STATIC_USER_MANAGEMENT=true   # 开启 API Key 认证 (默认)
```

```yaml
# src/auth/static_users.yaml
users:
  alice:
    user_id: alice
    display_name: Alice Smith
    email: alice@example.com
    status: active  # active/inactive
    threads:
      - api_key: sk-project-alice-main-abc123def456  # 用管理工具生成
        thread_id: main
        description: 主工作线程
        is_active: true
        expires_at: null  # 可选过期时间
```

**重要**: API Key 必须通过管理工具生成 (格式 `sk-project-{user_hash}-{thread_hash}-{random_suffix}`), 每个密钥唯一绑定用户和线程.

## API 密钥管理工具

`scripts/api_key_manager.py` 提供完整命令行管理:

```bash
python scripts/api_key_manager.py list                                    # 查看所有用户
python scripts/api_key_manager.py create-user alice --display-name "Alice Smith"
python scripts/api_key_manager.py create alice main "主工作线程"           # 生成 API 密钥
python scripts/api_key_manager.py validate sk-project-alice-main-abc123   # 验证密钥
python scripts/api_key_manager.py stats                                   # 使用统计
```

## API 认证

三种方式 (推荐 Bearer Token):

```bash
# Bearer Token (推荐)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-project-alice-main-abc123def456" \
  -d '{"model": "personal-assistant", "messages": [{"role": "user", "content": "你好"}]}'

# X-API-Key Header
curl -H "X-API-Key: sk-project-alice-main-abc123def456" ...

# Query Parameter
curl "http://localhost:8000/v1/chat/completions?api_key=sk-project-alice-main-abc123def456" ...
```

## 开发者集成

```python
from src.auth import get_auth_manager

auth_manager = get_auth_manager()
user = auth_manager.authenticate_api_key("sk-project-alice-main-abc123def456")
if user:
    print(f"认证成功: {user.user_id}, 线程: {user.thread_id}")
```

> 用户/密钥管理优先用 `api_key_manager.py` CLI 工具, 避免在代码中操作.

## 数据隔离

每个用户-线程组合拥有独立数据存储 (`./data/{user_id}/{thread_id}/`), 文件系统级隔离, 详见 [路径管理系统](./path-management.md).

## 故障排除

| 错误 | 原因 |
|------|------|
| 401 未授权 | API Key 无效或过期 |
| 403 访问拒绝 | 用户被禁用 |
| 配置加载失败 | 检查 `src/auth/static_users.yaml` |
| 管理未启用 | 设 `ENABLE_STATIC_USER_MANAGEMENT=true` |

## 最佳实践

- 生产环境保持静态用户管理开启, 定期轮换 API Key
- 按用途分离线程, 避免硬编码密钥
- 用户ID 用小写字母, 线程ID 描述用途
