"""搜索同义词映射 - 覆盖 LLM 常用中文查询词到英文关键词的映射.

用于 search_available_tools 的同义词扩展匹配,
当 LLM 不使用工具名而是用描述性语言查询时提供召回保障.
"""

from __future__ import annotations

BUILTIN_SYNONYMS: dict[str, list[str]] = {
    # 图像相关
    "画图": ["image", "picture", "draw"],
    "绘图": ["image", "picture", "draw"],
    "作图": ["image", "draw"],
    "文生图": ["image", "generate"],
    # 下载/文件相关
    "下载": ["download", "link", "regenerate"],
    # 代码相关
    "代码": ["python", "code", "executor"],
    "编程": ["python", "code", "executor"],
    "脚本": ["python", "code", "executor"],
    # 天气相关
    "下雨": ["weather"],
    "预报": ["weather"],
    # 地理相关
    "地图": ["geo", "map", "navigator"],
    "导航": ["geo", "navigator", "route"],
    "出行": ["geo", "navigator", "travel"],
    "附近": ["geo", "navigator", "poi"],
    "周边": ["geo", "navigator", "poi"],
    "餐厅": ["geo", "navigator", "poi"],
    # 消息相关
    "通知": ["messenger", "message"],
    "微信": ["messenger"],
    "邮件": ["messenger"],
    "提醒我": ["scheduled", "messenger", "timer"],
    # 导出相关
    "导出": ["export", "document", "pdf"],
    "报告": ["export", "document"],
    # 网页相关
    "抓取": ["web", "fetch"],
    "网页内容": ["web", "fetch"],
    # 研究相关
    "调查": ["research"],
    "查找资料": ["research"],
}
