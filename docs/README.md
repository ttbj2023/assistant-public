# 项目文档索引

**项目版本**: v1.9.0 | **文档更新**: 2026-07-14

## 📚 核心文档导航

### 🎯 快速入门
3. **[README.md](../README.md)** - 项目介绍和快速开始
4. **[调试模式](./debugging.md)** - 双层调试功能使用指南

### ⚙️ 配置和部署
- **[配置系统文档](./configuration.md)** - 五类配置来源与强制规则
- **[config.yaml 参考](./config-yaml-reference.md)** - YAML 字段完整参考
- **[配置治理规范](./development/config-governance.md)** - 新增配置归类与禁止事项
- **[用户管理指南](./user_management_guide.md)** - API Key认证和用户隔离
- **[路径管理](./path-management.md)** - 数据隔离路径系统

### 🛠️ 开发指南
- **[测试系统](./development/testing.md)** - 三层测试架构（单元/集成/E2E）
- **[单元测试规范](./development/unit_test_design_specification.md)** - v1.9.0测试策略 (含 Mock 体系章节)
- **[集成测试规范](./development/integration_test_design_specification.md)** - v1.9.0协作测试
- **[双路检索架构](./development/memory-system-dual-retrieval.md)** - SQL为主向量为辅
- **[静态分析](./development/static-analysis.md)** - V2.0并行架构
- **[配置治理规范](./development/config-governance.md)** - 新增配置归类与禁止事项
- **[缓存设计](./development/cache-design.md)** - 三层缓存与生命周期管理
- **[工具系统设计规范](./development/tool-design-specification.md)** - 字段语义/筛选消费链路/写作规范
- **[Skills 接入设计文档](./development/skills-integration.md)** - 三级渐进式披露/关联工具注入/运行时

### 📝 项目记录
- **[更新日志](./changelog.md)** - 版本历史和变更记录

## 🎯 文档使用建议

### 新手入门
```
```

### 开发参考
```
测试：development/testing.md
Mock：development/unit_test_design_specification.md
调试：debugging.md
```

### 运维管理
```
用户：user_management_guide.md
路径：path-management.md
```

---

**文档维护**: 季度审查, 版本同步, 保持一致性
**最后更新**: 2026-07-14
**项目版本**: v1.9.0
