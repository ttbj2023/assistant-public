# 路径管理系统

通过 `src/core/path_resolver.py` 提供用户-线程-Agent 三级隔离的数据存储方案. 每个 Agent 拥有独立的 `database/` 和 `vector/` 子目录, 通过文件系统实现天然数据隔离.

## 三级隔离架构

```
data/
├── {user_id}/
│   └── {thread_id}/
│       ├── {agent_id}/              # Agent 物理隔离目录
│       │   ├── database/            # SQLite 数据库
│       │   └── vector/              # 向量存储
│       └── shared/                  # 跨 Agent 共享资源 (附件等)
│           └── files/images/
```

**规范**: 基础路径 `data/` (可经 `BASE_DATA_PATH` 自定义); 用户/线程/Agent 三级隔离; 共享资源入 `shared/`; ID 经 `IDValidator` 安全化避免特殊字符.

## 核心 API

`UserDataPathResolver` 单例, 优先用便捷函数而非实例方法. 函数签名、参数语义与示例见 `src/core/path_resolver.py` docstring.

主要入口:
- `get_thread_base_path(user_id, thread_id)`
- `get_database_path(user_id, thread_id, db_name, *, agent_id)`
- `get_vector_path(user_id, thread_id, *, agent_id)`
- `get_user_path_resolver()` 获取 resolver 实例, 用于 `get_storage_path()` 等高级接口

## 安全特性

**ID 验证**: 所有 user_id / thread_id 经 `IDValidator` 验证, 移除危险字符确保文件系统安全. 实现见 `src/core/validation/id_validator.py`.

**测试环境检测**: `ENVIRONMENT=testing` 时自动切换到 `./test_data`, 不自动创建生产目录.

## 数据库文件分离

对话历史与置顶记忆等数据按业务生命周期分离到不同 SQLite 数据库, 具体表结构与 db 划分见 DAO 层源码. 分离原因: 业务逻辑隔离 / 性能优化 (独立备份) / 测试便利.

## 测试环境

`ENVIRONMENT=testing` 时数据落到 `./test_data/{user_id}/{thread_id}/{agent_id}/database/`, 由 `UserDataPathResolver` 统一管理, 会话结束自动清理. 测试身份通过 `test_user` / `test_thread_id` fixture 注入, 详见 [测试设计规范](development/testing.md).

```
# 生产: ./data/{user_id}/{thread_id}/{agent_id}/database/
# 测试: ./test_data/{user_id}/{thread_id}/{agent_id}/database/
```

## 最佳实践

1. **path_resolver 是唯一真相来源**: fixture 和 Service 层都用 `get_database_path()` 等函数, 确保路径一致. **禁止 Mock path_resolver** (会导致 fixture 与 Service 层路径不一致, 测试失败).
2. **优先用便捷函数**: `get_database_path(...)` 而非 `get_user_path_resolver().get_database_path(...)` (更简洁).
3. **路径自动缓存**: 重复调用走缓存; 必要时 `resolver.clear_cache()`.

## 调试

将 `src.core.path_resolver` logger 级别设为 DEBUG, 或调用 `get_user_path_resolver().get_base_path_info()` / `get_cache_stats()` 查看路径与缓存统计.

## 故障排除

| 错误 | 解决 |
|------|------|
| `PermissionError` 创建基础路径 | 父目录需写权限, 或设 `BASE_DATA_PATH` |
| `FileNotFoundError` 父目录不存在 | 解析器自动创建子目录, 确保基础路径父目录存在 |
| `ValueError` user_id 不能为空 | 传入有效非空字符串 ID |
