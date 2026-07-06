# 健康数据子系统

Health Agent 的健康数据提取与审计机制. 通过后台提取器 + 周期审计的双层架构, 从日常对话中持续采集结构化健康数据 (体重 / 饮食 / 食品包装 / 购物 / 运动 / 体检报告).

## 后台提取器

`src/agent/agents_implementations/health_assistant/health_data_background_extractor.py`

- 单次 LLM 调用完成检测 + 分类 + 转录, 纯转录不做去重判断
- Fire-and-forget 模式: 不阻塞主对话, 失败仅记录日志
- 仅在非审计轮次运行 (第 1-9, 11-19 轮等)

## 审计任务

`src/agent/agents_implementations/health_assistant/health_data_audit.py`

- 每 10 轮触发, 替代该轮常规提取器 (轮次驱动架构)
- 一次调用完成: 提取本轮新数据 + 审查最近 15 轮历史数据
- 模型输出 `extractions` (CUD) + `operations` (对历史数据的 create/update/delete), 逐条执行
- Prompt 模板: `src/agent/agents_implementations/health_assistant/prompts/health_data_audit.yaml` (随模块内嵌, `Path(__file__).parent / "prompts"` 加载)

## 提取模块

`src/inference/health_data_extraction/`

- 基于 doubao-seed-2.0-mini, 支持 6 种数据类型: 体重 / 饮食 / 食品包装 / 购物 / 运动 / 体检报告
- Prompt 模板: `src/inference/health_data_extraction/prompts/health_extraction.yaml` (随模块内嵌, `Path(__file__).parent / "prompts"` 加载)
