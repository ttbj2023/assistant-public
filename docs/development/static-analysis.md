# 静态分析体系 V2.0

基于 asyncio 并行架构的统一静态分析体系. 分析范围: 仅 `src/` 目录.

## 核心特性

- **统一并行架构**: asyncio 并行执行所有工具
- **智能缓存**: 基于文件修改时间, 避免重复分析 (`--no-cache` 强制刷新)
- **工作区支持**: 自动检测主项目根目录, 支持 git worktree
- **双模式分层**: 核心模式 (CI门禁, 阻断性) + 完整模式 (探索工具, 改进信号, 非阻断)
- **JSON 报告**: 工具级 + 汇总报告, 支持 CI/CD 集成

> 完整模式不是追求 100% 严格, 而是提供**有价值的指导**: 发现真正问题 (逻辑/安全/性能) + 推广有价值的实践 + 避免 AI/ML 项目误报.

## 工具链

| 工具 | 用途 |
|------|------|
| **Ruff** | 代码质量检查、格式化、导入排序 |
| **MyPy** | 静态类型检查 |
| **Bandit** | 安全漏洞扫描 |
| **Safety** | 依赖安全检查 (核心模式跳过) |
| **依赖关系检查** | 核心模块使用统计 |
| **格式化工具** | 代码格式化 + 中文标点修复 |

> 工具版本、select/ignore 规则、完整模式配置详见 `pyproject.toml` / `config/ruff_full.toml` / `config/mypy_full.toml`.

## 分级配置

| 模式 | 定位 | 配置 | 质量门禁 |
|------|------|------|---------|
| **核心模式** (默认/quick) | CI门禁, 阻断性 (core精选规则, 正常代码全绿) | `pyproject.toml` | critical==0 + strict<5 + warning<30 |
| **完整模式** (full) | 探索工具, 改进信号, 非阻断 | `config/ruff_full.toml`, `config/mypy_full.toml` | 无门禁, exit 0, 仅出报告 |

### 严重程度

| 级别 | 说明 |
|------|------|
| CRITICAL | 安全漏洞、类型错误, 核心模式阻止 CI; full 仅报告 |
| STRICT | 代码风格、最佳实践, 仅核心模式阻止 CI; full 作改进信号 |
| WARNING | 建议修复, 不阻止 CI |
| INFO / STYLE | 仅供参考 |

## 命令行接口

```bash
python scripts/static_analysis.py --help
```

- **analyze** (默认): Ruff + MyPy + Bandit + (Safety, 核心模式跳过) + 依赖关系检查
- **format**: 代码格式化与中文标点修复
- **全局选项**: `--core-mode` / `--full-mode` / `--verbose` / `--no-cache` / `--without-safety` / `--output`

## 常用工作流

```bash
# 日常开发: 快速检查
python scripts/static_analysis.py analyze --quick

# 提交前: 核心模式门禁检查
python scripts/static_analysis.py analyze

# 格式修复
python scripts/static_analysis.py format --fix

# 发布前: 完整模式分析 (探索, 非阻断, 出改进信号报告)
python scripts/static_analysis.py --full-mode --verbose
```

## 报告

`--output` 生成 JSON 报告到 `reports/`. 门禁状态、执行时间等字段见报告结构, 以源码输出为准.

## 故障排除

```bash
pip install -e ".[dev]"                                       # 工具安装
rm -rf .mypy_cache .ruff_cache .cache                         # 清理缓存
python scripts/static_analysis.py analyze --no-cache          # 强制重新分析
python scripts/static_analysis.py analyze --without-safety    # 跳过慢速工具
```

> Codex 沙盒专用: `python scripts/static_analysis.py --codex`.
