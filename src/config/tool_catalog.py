"""内置工具 catalog.

内置工具的类路径,默认描述和默认分组属于代码注册表, 不再要求每个环境在
config.yaml 重复声明. config.yaml 只覆盖启用状态,timeout,prompt_hint 和 config.
"""

from __future__ import annotations

import copy
from typing import Any

_BUILTIN_TOOLS_CONFIG: dict[str, Any] = {
    "tool_groups": {
        "scheduled_messenger_group": {
            "name": "scheduled_messenger_group",
            "summary": (
                "消息发送与提醒, 通过微信或邮件发送通知(支持定时), "
                "Agent唯一脱离对话循环的通信渠道"
            ),
            "description": (
                "消息发送与提醒, Agent唯一能脱离对话循环向用户发送消息的渠道.\n"
                "通过微信或邮件发送通知/提醒/报告, 支持定时发送.\n"
                "唤醒后提供创建/查看/取消定时消息三个子工具."
            ),
            "keywords": [
                "定时",
                "提醒",
                "消息",
                "通知",
                "微信",
                "邮件",
                "提醒我",
            ],
            "members": [
                "schedule_message",
                "list_scheduled_messages",
                "cancel_scheduled_message",
            ],
        },
        "todo_manager_group": {
            "name": "todo_manager_group",
            "summary": "待办任务管理, 记录并跟踪用户的事项进度",
            "description": (
                "TODO任务管理工具.\n"
                "支持创建/查看/更新/删除任务, 可设置优先级/状态/截止日期.\n"
                "唤醒后提供四个子工具: "
                "create_todo/list_todos/update_todo/delete_todo."
            ),
            "keywords": ["待办", "任务", "todo", "计划", "提醒事项"],
            "members": ["create_todo", "list_todos", "update_todo", "delete_todo"],
            "prompt_hint": (
                "写操作(create_todo/update_todo/delete_todo)完成后, 必须以数据库最新真实状态"
                "为准向用户汇报任务情况; 写工具返回中的 current_todos 是结构化任务列表(list of dict), "
                "即为该真实状态, 亦可额外调用 list_todos 复核; 严禁凭记忆或猜测描述任务, "
                "严禁在未实际执行写操作时声称已完成创建/更新/删除"
            ),
        },
        "stock_watch_group": {
            "name": "stock_watch_group",
            "summary": "A股个股实时行情查询与价格监控告警, 突破阈值时消息提醒",
            "description": (
                "A股个股实时行情与价格监控工具.\n"
                "支持查询实时行情(现价/涨跌幅/五档), 设定价格阈值在突破或跌破时消息提醒.\n"
                "唤醒后提供查询行情/创建监控/查看监控/取消监控四个子工具."
            ),
            "keywords": [
                "股票",
                "股价",
                "现价",
                "行情",
                "监控",
                "告警",
                "提醒",
                "突破",
                "A股",
                "报价",
                "多少钱",
                "到价",
                "跌破",
                "涨到",
                "涨跌",
                "盘口",
                "盯盘",
            ],
            "members": [
                "query_stock_price",
                "create_price_alert",
                "list_price_alerts",
                "cancel_price_alert",
            ],
        },
        "memory_recall_group": {
            "name": "memory_recall_group",
            "summary": "历史对话检索, 按内容搜索或按轮次取原文(索引区下钻)",
            "description": (
                "历史对话记忆检索工具组, 唤醒后提供搜索与取详情两个子工具.\n"
                "search_memories: 按关键词搜索历史对话, 返回概览钩子(轮次+主题+摘要).\n"
                "get_round_detail: 按轮次号取回完整原文(从钩子/索引区下钻)."
            ),
            "keywords": [
                "记忆",
                "历史",
                "之前",
                "回忆",
                "上次",
                "搜索对话",
                "记得",
                "聊过",
                "说过",
                "前面",
                "上一轮",
            ],
            "members": ["search_memories", "get_round_detail"],
            "prompt_hint": (
                "search_memories 返回概览钩子(轮次+主题+摘要), 需要完整内容时用 "
                "get_round_detail 按轮次号取原文. 先 search 定位, 再 get_round_detail 取细节"
            ),
        },
    },
    "internal_tools": {
        "create_todo": {
            "name": "create_todo",
            "class_path": "src.tools.internal.create_todo_tool.CreateTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "description": "创建TODO任务",
            "config": {},
        },
        "list_todos": {
            "name": "list_todos",
            "class_path": "src.tools.internal.list_todos_tool.ListTodosTool",
            "enabled": True,
            "timeout": 30.0,
            "description": "查看TODO任务列表",
            "config": {},
        },
        "update_todo": {
            "name": "update_todo",
            "class_path": "src.tools.internal.update_todo_tool.UpdateTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "description": "更新TODO任务",
            "config": {},
        },
        "delete_todo": {
            "name": "delete_todo",
            "class_path": "src.tools.internal.delete_todo_tool.DeleteTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "description": "删除TODO任务",
            "config": {},
        },
        "search_memories": {
            "name": "search_memories",
            "class_path": "src.tools.internal.async_memory_retrieval_tool.AsyncMemoryRetrievalTool",
            "enabled": True,
            "timeout": 45.0,
            "description": "异步记忆检索和对话历史搜索工具",
            "config": {"max_results": 20, "enable_vector_search": True},
        },
        "get_round_detail": {
            "name": "get_round_detail",
            "class_path": "src.tools.internal.async_round_detail_tool.AsyncRoundDetailTool",
            "enabled": True,
            "timeout": 30.0,
            "description": "按轮次号获取对话完整原文(索引区下钻 fetch 工具)",
            "config": {},
        },
        "health_data_manager": {
            "name": "health_data_manager",
            "class_path": "src.tools.internal.health_data_manager_tool.HealthDataManagerTool",
            "enabled": True,
            "timeout": 45.0,
            "description": "健康数据管理工具, 支持用户隔离的健康数据访问和CRUD操作",
            "config": {
                "max_records": 1000,
                "enable_batch_operations": True,
            },
        },
        "scheduled_messenger": {
            "name": "scheduled_messenger",
            # 配置载体: 保留供 ScheduledMessengerBase._load_shared_config 读取
            # SMTP/限额等共享配置. enabled=False 因已拆分为3子工具, 不再独立创建
            "class_path": "src.tools.internal.schedule_message_tool.ScheduleMessageTool",
            "enabled": False,
            "timeout": 15.0,
            "description": "定时消息共享配置载体(已拆分为3子工具)",
            "config": {
                "max_pending_messages": 50,
                "max_schedule_ahead_hours": 168,
                "default_channel": "wechat",
            },
        },
        "schedule_message": {
            "name": "schedule_message",
            "class_path": "src.tools.internal.schedule_message_tool.ScheduleMessageTool",
            "enabled": True,
            "timeout": 15.0,
            "description": "创建定时消息/提醒",
            "config": {},
        },
        "list_scheduled_messages": {
            "name": "list_scheduled_messages",
            "class_path": "src.tools.internal.list_scheduled_messages_tool.ListScheduledMessagesTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "查看待发送的定时消息",
            "config": {},
        },
        "cancel_scheduled_message": {
            "name": "cancel_scheduled_message",
            "class_path": "src.tools.internal.cancel_scheduled_message_tool.CancelScheduledMessageTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "取消一条定时消息",
            "config": {},
        },
        "price_alert": {
            "name": "price_alert",
            # 配置载体: 保留 base_url 供 PriceAlertEngine / QueryStockPriceTool 读取
            # (openclaw 渠道默认已统一到 openclaw.notification_defaults).
            # enabled=False 因已拆分为3子工具, 不再独立创建.
            "class_path": "src.tools.internal.create_price_alert_tool.CreatePriceAlertTool",
            "enabled": False,
            "timeout": 15.0,
            "description": "价格监控共享配置载体(已拆分为3子工具)",
            "config": {
                # 开发默认值; 生产由 QUOTE_SERVICE_BASE_URL env 覆盖
                "base_url": "http://127.0.0.1:8767",
            },
        },
        "create_price_alert": {
            "name": "create_price_alert",
            "class_path": "src.tools.internal.create_price_alert_tool.CreatePriceAlertTool",
            "enabled": True,
            "timeout": 15.0,
            "description": "创建A股个股价格监控, 突破阈值时微信提醒",
            "config": {},
        },
        "list_price_alerts": {
            "name": "list_price_alerts",
            "class_path": "src.tools.internal.list_price_alerts_tool.ListPriceAlertsTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "查看活跃的价格监控规则",
            "config": {},
        },
        "cancel_price_alert": {
            "name": "cancel_price_alert",
            "class_path": "src.tools.internal.cancel_price_alert_tool.CancelPriceAlertTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "取消一条价格监控规则",
            "config": {},
        },
        "query_stock_price": {
            "name": "query_stock_price",
            "class_path": "src.tools.internal.query_stock_price_tool.QueryStockPriceTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "查询A股个股实时行情(现价/涨跌幅/五档)",
            "config": {},
        },
        "search_available_tools": {
            "name": "search_available_tools",
            "class_path": "src.tools.internal.search_available_tools.SearchAvailableTools",
            "enabled": True,
            "timeout": 10.0,
            "description": "搜索可用工具, 帮助Agent发现休眠工具",
            "config": {},
        },
        "load_skill": {
            "name": "load_skill",
            "class_path": "src.tools.skills.load_skill_tool.LoadSkillTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "加载指定技能的完整使用说明(领域知识), 渐进式披露入口",
            "config": {},
        },
        "read_file": {
            "name": "read_file",
            "class_path": "src.tools.internal.read_file_tool.ReadFileTool",
            "enabled": True,
            "timeout": 15.0,
            "description": "按文件ID读取文件描述内容 (图片画面描述/文档摘要)",
            "config": {},
        },
        "analyze_image": {
            "name": "analyze_image",
            "class_path": "src.tools.internal.analyze_image_tool.AnalyzeImageTool",
            "enabled": True,
            "timeout": 60.0,
            "description": "按具体需求分析用户上传过的图片原图 (OCR/表格/细节)",
            "config": {},
            "skip_when_capabilities": ["image_input"],
        },
        "regenerate_download_link": {
            "name": "regenerate_download_link",
            "class_path": "src.tools.internal.regenerate_download_link_tool.RegenerateDownloadLinkTool",
            "enabled": True,
            "timeout": 15.0,
            "description": "按文件ID重新生成文件下载链接",
            "config": {},
            "prompt_hint": (
                "对话历史中 [file: file_id] 代表任意附件(图片/文档/系统生成文件), "
                "file_id 是 8 位 hex; 用户引用附件时提取 file_id, "
                "工具参数名用 file_id; "
                "系统会自动在回复末尾附上下载链接, 无需在正文中重复"
            ),
        },
        "generate_image": {
            "name": "generate_image",
            "class_path": "src.tools.internal.image_generation_tool.ImageGenerationTool",
            "enabled": True,
            "timeout": 120.0,
            "description": "根据文字提示词生成图片, 保存为共享图片附件并返回下载链接",
            "config": {
                "timeout": 120.0,
            },
        },
        "generate_video": {
            "name": "generate_video",
            "class_path": "src.tools.internal.video_generation_tool.VideoGenerationTool",
            "enabled": True,
            "timeout": 600.0,
            "description": "根据文字提示词生成有声视频, 保存为共享视频附件并返回下载链接 (耗时1-3分钟)",
            "config": {
                "model_id": "ark-agent-plan:doubao-seedance-2.0",
                "timeout": 600.0,
            },
        },
        "wechat_publish": {
            "name": "wechat_publish",
            "class_path": "src.tools.internal.wechat_publish.tool.WechatPublishTool",
            "enabled": True,
            "timeout": 300.0,
            "description": "将Markdown文章发布到微信公众号草稿箱",
            "config": {},
        },
    },
    "external_tools": {
        "weather_query": {
            "name": "weather_query",
            "class_path": "src.tools.external.weather_tool.WeatherQueryTool",
            "enabled": True,
            "timeout": 10.0,
            "description": "查询指定城市实时天气(温度/湿度/风力/空气质量), 毫秒级响应",
            "config": {},
        },
        "mermaid_chart": {
            "name": "mermaid_chart",
            "class_path": "src.tools.external.chart_maker.mermaid_chart_tool.MermaidChartTool",
            "enabled": True,
            "timeout": 60.0,
            "description": "渲染mermaid流程图/时序图/甘特图为PNG图片",
            "config": {},
        },
        "vega_chart": {
            "name": "vega_chart",
            "class_path": "src.tools.external.chart_maker.vega_chart_tool.VegaChartTool",
            "enabled": True,
            "timeout": 60.0,
            "description": "渲染Vega-Lite数据图表(折线/柱状/饼/散点/堆叠)为PNG图片",
            "config": {},
        },
        "markmap_chart": {
            "name": "markmap_chart",
            "class_path": "src.tools.external.chart_maker.markmap_chart_tool.MarkmapChartTool",
            "enabled": True,
            "timeout": 60.0,
            "description": "渲染markmap思维导图(Markdown层级结构)为PNG图片",
            "config": {},
        },
        "export_document": {
            "name": "export_document",
            "class_path": "src.tools.external.export_document.tool.ExportDocumentTool",
            "enabled": True,
            "timeout": 120.0,
            "description": "文档导出, Markdown → PDF/DOCX, 支持4种风格模板",
            "config": {},
        },
        "python_executor": {
            "name": "python_executor",
            "class_path": "src.tools.external.python_executor_tool.PythonExecutorTool",
            "enabled": True,
            "timeout": 35.0,
            "description": "精确计算与数据分析工具, 预装numpy/pandas; code必须是可执行Python代码, 结果需print()输出; 经 tool-runtime 执行",
            "config": {
                "default_timeout_seconds": 5.0,
                "max_timeout_seconds": 30.0,
                "max_code_chars": 20000,
                "max_stdin_chars": 20000,
                "max_stdout_chars": 20000,
                "max_stderr_chars": 12000,
            },
        },
        "professional_database": {
            "name": "professional_database",
            "class_path": "src.tools.external.professional_database_tool.ProfessionalDatabaseTool",
            "enabled": True,
            "timeout": 120.0,
            "description": "专业数据库查询(金融/工商/风险), DataPro数据源+DeepSeek大上下文整理",
            "config": {},
        },
    },
    "mcp_servers": {},
    # Skill配置(默认空, 各环境config.yaml按需配置)
    "skills": {},
}


def get_builtin_tools_config() -> dict[str, Any]:
    """返回内置工具 catalog 的深拷贝."""
    return copy.deepcopy(_BUILTIN_TOOLS_CONFIG)
