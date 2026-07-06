"""用户消息附件格式化 (纯展示逻辑, 无副作用).

从 src/core/chat_helpers.py 拆分至 utils, 供 api/session/agent 各层共享,
避免上层 (agent) 对编排层 (session) 的反向依赖. 仅做文本拼接, 不触发任何 IO.

attachments 参数采用 duck typing (只读 file_type/brief/file_id/internal_path/detail 属性),
运行时不依赖 storage 层, 保持 utils 作为纯净横切工具层.
"""

from __future__ import annotations

from typing import Any

# 图片扩展名集合, 用于判断导出文件是否以内联图片渲染.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})


def format_user_message_with_attachments(
    user_text: str,
    attachments: list[Any],
) -> str:
    """格式化用户消息, 附加图片/文档/音视频附件信息.

    纯文本格式化, 不依赖任何外部服务. 消息格式化是展示层职责.

    Args:
        user_text: 用户原始文本
        attachments: 附件列表

    Returns:
        格式化后的消息文本

    """
    if not attachments:
        return user_text

    attachment_texts = []
    for attachment in attachments:
        if attachment.file_type == "image":
            brief = attachment.brief or "图片"
            marker = (
                f"[file: {attachment.file_id}] {brief}"
                if attachment.file_id
                else f"[img: {attachment.internal_path} - {brief}]"
            )
            attachment_texts.append(marker)
        elif attachment.file_type == "document":
            brief = attachment.brief or attachment.internal_path
            marker = (
                f"[file: {attachment.file_id}] {brief}"
                if attachment.file_id
                else f"[file: {attachment.internal_path} - {brief}]"
            )
            attachment_texts.append(marker)
        elif attachment.file_type == "audio":
            attachment_texts.append(f"[audio: {attachment.internal_path}]")
        elif attachment.file_type == "video":
            desc = attachment.brief or "视频"
            attachment_texts.append(f"[video: {attachment.internal_path} - {desc}]")

    if attachment_texts:
        attachment_info = " ".join(attachment_texts)
        return f"{user_text} {attachment_info}" if user_text else attachment_info

    return user_text


def build_media_lines(exported_files: list[dict]) -> str:
    """为 OpenClaw 构建 MEDIA: 指令行, 供微信等渠道识别文件下载链接."""
    lines = []
    for file_info in exported_files:
        lines.append(f"MEDIA:{file_info['url']}")
    return "\n".join(lines)


def build_file_links(exported_files: list[dict]) -> str:
    """为 Web 前端构建文件链接行: 图片用 ![]() 内联, 其他用 []() 下载链接."""
    lines = []
    for file_info in exported_files:
        url = file_info["url"]
        filename = file_info.get("filename", "file")
        fmt = (file_info.get("format") or "").lower()
        if fmt in IMAGE_EXTENSIONS:
            lines.append(f"![{filename}]({url})")
        else:
            lines.append(f"[{filename}]({url})")
    if not lines:
        return ""
    return "\n---\n" + "\n".join(lines)


__all__ = [
    "IMAGE_EXTENSIONS",
    "build_file_links",
    "build_media_lines",
    "format_user_message_with_attachments",
]
