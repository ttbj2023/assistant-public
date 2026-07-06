"""图片视觉描述生成模块 - 调用视觉模型生成图片的 brief 和 detail.

从 src/storage/service/attachment_service.py 拆分而来.
视觉描述本质是模型推理职责, 不应属于存储层.

阶段 3 将在此模块实现 ImageDescriber:
- 接收图片文件路径 + MIME 类型
- 调用视觉模型 (Doubao 主模型)
- 解析响应返回 (brief, detail) 元组
- 失败时返回默认描述, 不抛异常 (推理失败不应阻塞存储流程)
"""
