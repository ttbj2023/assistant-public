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
                "唤醒后提供四个子工具: schedule_message_wechat/schedule_message_email/"
                "list_scheduled_messages/cancel_scheduled_message."
            ),
            "keywords": [
                "定时",
                "提醒",
                "消息",
                "通知",
                "微信",
                "邮件",
                "提醒我",
                "叫醒",
                "闹钟",
            ],
            "members": [
                "schedule_message_wechat",
                "schedule_message_email",
                "list_scheduled_messages",
                "cancel_scheduled_message",
            ],
            "prompt_hint": (
                "凡到点主动送达或脱离会话发消息, 一律用本组; "
                "挂在事件上的提醒应先建日程再设消息并传 related_event_id 关联; "
                "任务性提醒(待办/处理事项)走待办工具+本组, 不建日程; "
                "单次发送不支持周期重复, 周期性提醒建重复日程由手机日历承担; "
                "创建后以返回的 message_id 与发送时间如实汇报"
            ),
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
            "keywords": ["待办", "任务", "todo", "计划", "备忘", "记一下"],
            "members": ["create_todo", "list_todos", "update_todo", "delete_todo"],
            "prompt_hint": (
                "写操作(create_todo/update_todo/delete_todo)完成后, 必须以数据库最新真实状态"
                "为准向用户汇报任务情况; 写工具返回中的 current_todos 是结构化任务列表(list of dict), "
                "即为该真实状态, 亦可额外调用 list_todos 复核; 严禁凭记忆或猜测描述任务, "
                "严禁在未实际执行写操作时声称已完成创建/更新/删除"
            ),
        },
        "calendar_manager_group": {
            "name": "calendar_manager_group",
            "summary": "日程管理, 创建/查询/修改日程(支持重复规则), 可订阅到手机日历",
            "description": (
                "日程管理工具.\n"
                "支持创建/查询/更新/删除日程, 支持全天事件与重复日程(每天/每周/每月/每年), "
                "日程可经 ICS 订阅同步到手机日历.\n"
                "唤醒后提供四个子工具: "
                "create_calendar_event/list_calendar_events/"
                "update_calendar_event/delete_calendar_event."
            ),
            "keywords": [
                "日程",
                "日历",
                "安排",
                "行程",
                "会议",
                "约",
                "改期",
                "开会",
                "日历订阅",
            ],
            "members": [
                "create_calendar_event",
                "list_calendar_events",
                "update_calendar_event",
                "delete_calendar_event",
            ],
            "prompt_hint": (
                "时间输入使用用户时区的本地时间(ISO格式); 全天事件只传日期(如 2026-10-01)并置 "
                "all_day=true. 写操作完成后, 必须以工具返回的 event 数据(数据库真实状态)向用户"
                "汇报; 严禁凭记忆描述日程; 修改/删除前先 list_calendar_events 定位 event_id. "
                "日程仅作记录与查看, 系统不因日程到点主动通知用户; 用户陈述带明确时间点的事件"
                "(会议/约见/生日/航班)先登记日程, 需到点提醒再配合定时消息工具; "
                "改期/删除日程会自动同步关联的定时消息, 以工具返回的真实级联结果汇报"
            ),
        },
        "stock_watch_group": {
            "name": "stock_watch_group",
            "summary": "A股个股实时股价查询与价格监控告警, 突破阈值时消息提醒",
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
        "health_data_group": {
            "name": "health_data_group",
            "summary": "健康数据查询, 覆盖体征/睡眠/运动/饮食/体检报告/购物清单",
            "description": (
                "用户健康数据查询工具组.\n"
                "唤醒后提供快照/每日明细/指标趋势/时段对比/运动/饮食/体检报告/购物清单八个子工具.\n"
                "数据来自外部设备导入和对话自动提取."
            ),
            "keywords": [
                "健康",
                "体重",
                "心率",
                "睡眠",
                "步数",
                "运动",
                "饮食",
                "营养",
                "体检",
                "报告",
                "购物",
                "食材",
                "血压",
                "血糖",
                "HRV",
                "卡路里",
                "趋势",
                "对比",
            ],
            "members": [
                "view_health_snapshot",
                "query_daily_health",
                "query_metric_trend",
                "compare_health_periods",
                "list_workout_records",
                "list_meal_records",
                "view_medical_report",
                "list_shopping_items",
            ],
        },
        "msgraph_sync_group": {
            "name": "msgraph_sync_group",
            "summary": "Outlook(Microsoft) 同步: 授权连接与同步状态查询",
            "description": (
                "Microsoft Graph 同步工具组.\n"
                "把 TODO 与日程同步到用户本人的 Outlook 个人账户, "
                "手机原生 To Do / Outlook 应用可见可改 (日历为只读镜像).\n"
                "唤醒后提供两个子工具: msgraph_connect(发起授权) / "
                "msgraph_sync_status(查询状态)."
            ),
            "keywords": [
                "Outlook",
                "微软",
                "Microsoft",
                "To Do",
                "同步",
                "授权",
                "绑定账户",
            ],
            "members": ["msgraph_connect", "msgraph_sync_status"],
        },
    },
    "internal_tools": {
        "create_todo": {
            "name": "create_todo",
            "class_path": "src.tools.internal.create_todo_tool.CreateTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "list_todos": {
            "name": "list_todos",
            "class_path": "src.tools.internal.list_todos_tool.ListTodosTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "update_todo": {
            "name": "update_todo",
            "class_path": "src.tools.internal.update_todo_tool.UpdateTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "delete_todo": {
            "name": "delete_todo",
            "class_path": "src.tools.internal.delete_todo_tool.DeleteTodoTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "create_calendar_event": {
            "name": "create_calendar_event",
            "class_path": "src.tools.internal.create_calendar_event_tool.CreateCalendarEventTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "list_calendar_events": {
            "name": "list_calendar_events",
            "class_path": "src.tools.internal.list_calendar_events_tool.ListCalendarEventsTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "update_calendar_event": {
            "name": "update_calendar_event",
            "class_path": "src.tools.internal.update_calendar_event_tool.UpdateCalendarEventTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "delete_calendar_event": {
            "name": "delete_calendar_event",
            "class_path": "src.tools.internal.delete_calendar_event_tool.DeleteCalendarEventTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "msgraph_connect": {
            "name": "msgraph_connect",
            "class_path": "src.tools.internal.msgraph_connect_tool.MsGraphConnectTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "msgraph_sync_status": {
            "name": "msgraph_sync_status",
            "class_path": "src.tools.internal.msgraph_sync_status_tool.MsGraphSyncStatusTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "search_memories": {
            "name": "search_memories",
            "class_path": "src.tools.internal.async_memory_retrieval_tool.AsyncMemoryRetrievalTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {"max_results": 20, "enable_vector_search": True},
        },
        "get_round_detail": {
            "name": "get_round_detail",
            "class_path": "src.tools.internal.async_round_detail_tool.AsyncRoundDetailTool",
            "enabled": True,
            "timeout": 30.0,
            "config": {},
        },
        "view_health_snapshot": {
            "name": "view_health_snapshot",
            "class_path": "src.tools.internal.view_health_snapshot_tool.ViewHealthSnapshotTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "query_daily_health": {
            "name": "query_daily_health",
            "class_path": "src.tools.internal.query_daily_health_tool.QueryDailyHealthTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "query_metric_trend": {
            "name": "query_metric_trend",
            "class_path": "src.tools.internal.query_metric_trend_tool.QueryMetricTrendTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "compare_health_periods": {
            "name": "compare_health_periods",
            "class_path": "src.tools.internal.compare_health_periods_tool.CompareHealthPeriodsTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "list_workout_records": {
            "name": "list_workout_records",
            "class_path": "src.tools.internal.list_workout_records_tool.ListWorkoutRecordsTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "list_meal_records": {
            "name": "list_meal_records",
            "class_path": "src.tools.internal.list_meal_records_tool.ListMealRecordsTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "view_medical_report": {
            "name": "view_medical_report",
            "class_path": "src.tools.internal.view_medical_report_tool.ViewMedicalReportTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "list_shopping_items": {
            "name": "list_shopping_items",
            "class_path": "src.tools.internal.list_shopping_items_tool.ListShoppingItemsTool",
            "enabled": True,
            "timeout": 45.0,
            "config": {},
        },
        "health_data_manager": {
            "name": "health_data_manager",
            "class_path": "src.tools.internal.health_data_manager_tool.HealthDataManagerTool",
            "enabled": False,
            "timeout": 45.0,
            "config": {},
        },
        "scheduled_messenger": {
            "name": "scheduled_messenger",
            # 配置载体: 保留供 ScheduledMessageHelper.load_shared_config 读取
            # SMTP/限额等共享配置. enabled=False 因已拆分为渠道子工具, 不再独立创建
            "class_path": "src.tools.internal.schedule_message_wechat_tool.ScheduleMessageWechatTool",
            "enabled": False,
            "timeout": 15.0,
            "config": {
                "max_pending_messages": 50,
                "max_schedule_ahead_hours": 8760,
            },
        },
        "schedule_message_wechat": {
            "name": "schedule_message_wechat",
            "class_path": "src.tools.internal.schedule_message_wechat_tool.ScheduleMessageWechatTool",
            "enabled": True,
            "timeout": 15.0,
            "config": {},
        },
        "schedule_message_email": {
            "name": "schedule_message_email",
            "class_path": "src.tools.internal.schedule_message_email_tool.ScheduleMessageEmailTool",
            "enabled": True,
            "timeout": 15.0,
            "config": {},
        },
        "list_scheduled_messages": {
            "name": "list_scheduled_messages",
            "class_path": "src.tools.internal.list_scheduled_messages_tool.ListScheduledMessagesTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "cancel_scheduled_message": {
            "name": "cancel_scheduled_message",
            "class_path": "src.tools.internal.cancel_scheduled_message_tool.CancelScheduledMessageTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "price_alert": {
            "name": "price_alert",
            # 配置载体: 保留 base_url 供 PriceAlertEngine / QueryStockPriceTool 读取
            # enabled=False 因已拆分为3子工具, 不再独立创建.
            "class_path": "src.tools.internal.create_price_alert_tool.CreatePriceAlertTool",
            "enabled": False,
            "timeout": 15.0,
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
            "config": {},
        },
        "list_price_alerts": {
            "name": "list_price_alerts",
            "class_path": "src.tools.internal.list_price_alerts_tool.ListPriceAlertsTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "cancel_price_alert": {
            "name": "cancel_price_alert",
            "class_path": "src.tools.internal.cancel_price_alert_tool.CancelPriceAlertTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "query_stock_price": {
            "name": "query_stock_price",
            "class_path": "src.tools.internal.query_stock_price_tool.QueryStockPriceTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "search_available_tools": {
            "name": "search_available_tools",
            "class_path": "src.tools.internal.search_available_tools.SearchAvailableTools",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "load_skill": {
            "name": "load_skill",
            "class_path": "src.tools.skills.load_skill_tool.LoadSkillTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "read_file": {
            "name": "read_file",
            "class_path": "src.tools.internal.read_file_tool.ReadFileTool",
            "enabled": True,
            "timeout": 15.0,
            "config": {},
        },
        "analyze_image": {
            "name": "analyze_image",
            "class_path": "src.tools.internal.analyze_image_tool.AnalyzeImageTool",
            "enabled": True,
            "timeout": 60.0,
            "config": {},
            "skip_when_capabilities": ["image_input"],
        },
        "regenerate_download_link": {
            "name": "regenerate_download_link",
            "class_path": "src.tools.internal.regenerate_download_link_tool.RegenerateDownloadLinkTool",
            "enabled": True,
            "timeout": 15.0,
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
            "config": {
                "timeout": 120.0,
            },
        },
        "generate_video": {
            "name": "generate_video",
            "class_path": "src.tools.internal.video_generation_tool.VideoGenerationTool",
            "enabled": True,
            "timeout": 600.0,
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
            "config": {},
        },
    },
    "external_tools": {
        "weather_query": {
            "name": "weather_query",
            "class_path": "src.tools.external.weather_tool.WeatherQueryTool",
            "enabled": True,
            "timeout": 10.0,
            "config": {},
        },
        "mermaid_chart": {
            "name": "mermaid_chart",
            "class_path": "src.tools.external.chart_maker.mermaid_chart_tool.MermaidChartTool",
            "enabled": True,
            "timeout": 60.0,
            "config": {},
        },
        "vega_chart": {
            "name": "vega_chart",
            "class_path": "src.tools.external.chart_maker.vega_chart_tool.VegaChartTool",
            "enabled": True,
            "timeout": 60.0,
            "config": {},
        },
        "markmap_chart": {
            "name": "markmap_chart",
            "class_path": "src.tools.external.chart_maker.markmap_chart_tool.MarkmapChartTool",
            "enabled": True,
            "timeout": 60.0,
            "config": {},
        },
        "export_document": {
            "name": "export_document",
            "class_path": "src.tools.external.export_document.tool.ExportDocumentTool",
            "enabled": True,
            "timeout": 120.0,
            "config": {},
        },
        "python_executor": {
            "name": "python_executor",
            "class_path": "src.tools.external.python_executor_tool.PythonExecutorTool",
            "enabled": True,
            "timeout": 35.0,
            "config": {
                "default_timeout_seconds": 5.0,
                "max_timeout_seconds": 30.0,
                "max_code_chars": 20000,
                "max_stdin_chars": 20000,
                "max_stdout_chars": 20000,
                "max_stderr_chars": 12000,
            },
        },
        "finance_data": {
            "name": "finance_data",
            "class_path": "src.tools.external.datapro.finance_data_tool.FinanceDataTool",
            "enabled": True,
            "timeout": 120.0,
            "config": {},
        },
        "business_registry": {
            "name": "business_registry",
            "class_path": "src.tools.external.datapro.business_registry_tool.BusinessRegistryTool",
            "enabled": True,
            "timeout": 120.0,
            "config": {},
        },
        "enterprise_risk": {
            "name": "enterprise_risk",
            "class_path": "src.tools.external.datapro.enterprise_risk_tool.EnterpriseRiskTool",
            "enabled": True,
            "timeout": 120.0,
            "config": {},
        },
        "tea_knowledge": {
            "name": "tea_knowledge",
            "class_path": "src.tools.external.tea_knowledge_tool.TeaKnowledgeTool",
            "enabled": True,
            "timeout": 60.0,
            "config": {},
            "companions": ["kb_read_image"],
        },
        "kb_read_image": {
            "name": "kb_read_image",
            "class_path": "src.tools.external.kb_read_image_tool.KbReadImageTool",
            "enabled": True,
            "timeout": 60.0,
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
