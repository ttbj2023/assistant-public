"""文件管理路径常量 - 集中管理所有文件存储相关的路径片段.

之前这些字符串字面量散落在 7+ 个文件中 (attachment_service / image_generation_tool /
video_generation_tool / chart_maker / export_document / chat_helpers / path_resolver),
任何目录方案变更都需要全仓搜索修改. 本模块集中暴露这些常量, 调用方从此处导入.

迁移策略:
    阶段 0 仅定义常量, 不替换调用方 (避免一次性大改动).
    后续阶段在重构各调用方时, 逐步把字面量替换为 `from src.files.paths import ...`.

物理目录结构 (相对于线程 shared 根目录):
    files/images/              用户上传图片 (FileRepository.store_image)
    files/images/generated/    AI 生成图片 (ImageGenerationTool)
    files/videos/generated/    AI 生成视频 (VideoGenerationTool)
    files/exports/             工具导出文档 (ExportDocumentTool / SkillExecutorTool)
    files/exports/charts/      图表渲染产物 (ChartMakerTool)
    files/documents/           文档附件 (预留)

描述文件目录 (相对于 user_base, 用户级, 与主文件位置解耦):
    files/desc/                每个文件配套的 .desc.md 描述 (file_id 索引)
"""

from __future__ import annotations

# === 线程 shared 目录下的子路径片段 ===
# 调用方用法: get_shared_storage_path(user_id, thread_id, FILES_IMAGES)

FILES_IMAGES = "files/images"
FILES_IMAGES_GENERATED = "files/images/generated"
FILES_VIDEOS_GENERATED = "files/videos/generated"
FILES_EXPORTS = "files/exports"
FILES_CHARTS = "files/exports/charts"
FILES_DOCUMENTS = "files/documents"

# === 用户级描述文件目录 (相对于 user_base) ===
# 调用方用法: get_user_path_resolver().get_user_base_path(user_id) / FILES_DESC
FILES_DESC = "files/desc"

# === path_resolver 内部目录片段 (与 path_resolver.py 中的字面量保持一致) ===
# 注意: 这些是 path_resolver 内部约定, 此处仅作集中索引, 不替换 path_resolver 内部使用
SHARED = "shared"
DATABASE = "database"
VECTOR = "vector"

__all__ = [
    "DATABASE",
    "FILES_CHARTS",
    "FILES_DESC",
    "FILES_DOCUMENTS",
    "FILES_EXPORTS",
    "FILES_IMAGES",
    "FILES_IMAGES_GENERATED",
    "FILES_VIDEOS_GENERATED",
    "SHARED",
    "VECTOR",
]
