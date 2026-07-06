"""会话编排层 (Session Orchestration).

介于 api 与 agent/storage/files 之间, 承载消息执行编排逻辑:
- SessionMessageQueue: per-session 消息队列 (顺序处理 + 合并)
- chat_helpers: 消息附件准备/轮次分配等编排 (依赖 files/inference/storage)
- openclaw_message_splitter: OpenClaw 长消息拆分补发

从 src/core/ 迁出, 使 core 回归叶子层 (不再依赖 files/inference/storage),
消除 core <-> {files, inference, storage} 的循环依赖.

依赖方向 (单向向下):
api -> session -> {agent, files, storage.service, inference} -> storage.dao -> core
"""
