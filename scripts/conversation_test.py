#!/usr/bin/env python3
"""对话测试脚本 - Personal Assistant 统一版 (合并原 quick/full 两版本).

通过 CLI 参数控制对话轮次范围与目标 Agent:
  --all                              使用 55 轮完整版对话 (默认 23 轮精简版)
  --agent <agent_id>                 指定 Agent ID (默认 personal-assistant)
  --start-round N                    从第 N 轮开始 (调试用)
  --server-log PATH                  追加扫描的服务日志路径 (可多次)

用途:
  1. 串行执行预设对话, 覆盖内部/专家/MCP 三层工具体系
  2. 收集数据库内容/调试日志/运行日志, 生成 Markdown 报告
  3. 通过 prompt 结构摘要验证记忆系统 (messages 数组)

覆盖工具:
  内部: todo_manager_group, search_memories, scheduled_messenger, read_file,
        analyze_image, generate_image, regenerate_download_link, search_available_tools
  外部: weather_query, python_executor, export_document, professional_database (金融/企业风险/企业工商, 独立fastmcp.Client直连DataPro)
  专家: web_research (quick/deep, 内置 doubao_search/fetch_webpage/academic_search), geo_navigator (腾讯+百度地图)
  MCP:  baidu_search (web_research deep 内部使用)
  Skill: xlsx (load_skill激活 + skill_executor执行, 渐进式披露, 需tool-runtime容器),
         chart_maker (load_skill激活 + mermaid_chart/vega_chart/markmap_chart渲染, 渐进式披露, 需tool-runtime容器)

前置条件:
  - dev server 已以 DEBUG=true 在 8011 端口启动
  - 目标 agent 的 data/<user>/<thread>/<agent> 已清空 (或接受增量测试)

用法:
  python scripts/conversation_test.py                                  # 默认 23 轮 quick + personal-assistant
  python scripts/conversation_test.py --all                            # 55 轮 full + personal-assistant
  python scripts/conversation_test.py --agent health-assistant         # 23 轮 + health-assistant
  python scripts/conversation_test.py --start-round 10                 # 从第 10 轮开始
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# 配置 (USER_ID/THREAD_ID/MODEL/AGENT_ID/DATA_DIR 在 main() 中按 --agent 动态设置)
# ---------------------------------------------------------------------------

API_BASE = "http://localhost:8011"
API_KEY = "sk-project-jack-main-789xyz012uvw345"
MODEL = "personal-assistant"
TIMEOUT = 300

USER_ID = "jack"
THREAD_ID = "main"
AGENT_ID = "personal-assistant"

DATA_DIR = Path(f"data/{USER_ID}/{THREAD_ID}/{AGENT_ID}")
LOGS_DIR = Path("logs")

SEPARATOR = "=" * 60
THIN_SEP = "-" * 50

# ---------------------------------------------------------------------------
# 预设对话数据
# ---------------------------------------------------------------------------
# CONVERSATIONS_QUICK: 24 轮 (R1-R11 上下文构建含专业数据检索(金融指标) / R12-R16 TODO+召回+用户要求 / R17-R24 图片+多工具+图表+Excel+终极验证)
# CONVERSATIONS_FULL:  60 轮 (R1-R24 上下文含专业数据检索(企业风险+企业工商) / R25-R44 溢出召回 / R45-R60 针对性补充含图表/Excel/URL深读/去重验证/金融数据检索/学术论文检索)
# main() 按 --all 选择其中一份赋值给全局 CONVERSATIONS

CONVERSATIONS_QUICK: list[dict[str, str]] = [
    # ===== Phase 1: R1-R10 上下文构建 =====
    {
        "tag": "身份+生活",
        "hint": None,
        "input": "你好, 我叫陈思远, 在杭州做后端开发, 主要写Go和Python, 住在西湖区文三路附近, 养了一只叫Nemo的美短混血猫",
    },
    {
        "tag": "个人偏好",
        "hint": None,
        "input": "我比较宅, 喜欢看科幻小说和玩单机游戏, 最喜欢的是三体和塞尔达",
    },
    {
        "tag": "技术偏好",
        "hint": None,
        "input": "技术栈方面, 我偏好用Go做微服务, Python做脚本和数据分析, 数据库喜欢PostgreSQL",
    },
    {
        "tag": "TODO创建",
        "hint": None,
        "input": "帮我建两个待办: 1. 本周完成Kubernetes集群的升级方案文档, 高优先级 2. 给Nemo预约下年度的疫苗接种, 普通优先级",
    },
    {
        "tag": "TODO列表+更新",
        "hint": None,
        "input": "我现在有哪些待办事项? 把Kubernetes那个任务的状态改为进行中",
    },
    {
        "tag": "图片生成",
        "hint": "generate_image可能较慢",
        "input": "帮我画一张Nemo的卡通头像, 美短混血猫, 圆脸大眼睛, 萌系风格",
    },
    {
        "tag": "Web研究",
        "hint": "web_research可能较慢",
        "input": "我想在项目里引入一个新的时序数据库做监控指标存储, 你觉得InfluxDB和TimescaleDB哪个更合适? 考虑到我现有的技术栈",
    },
    {
        "tag": "地理研究",
        "hint": "geo_navigator可能较慢",
        "input": "周末想带Nemo去看兽医, 帮我搜搜文三路附近有没有好评的宠物医院",
    },
    {
        "tag": "专业数据检索-金融指标",
        "hint": "professional_database (external, 独立fastmcp.Client). 反幻觉: 单维度(盈利能力)一次命中, ROE多口径精确小数(加权1.65/平均1.6464/TTM 11.88), web仅散文. 需ARK_AGENT_PLAN_API_KEY",
        "input": "我最近在研究比亚迪(股票代码002594)的基本面, 帮我用专业数据检索查一下它的净资产收益率ROE和盈利水平",
    },
    {
        "tag": "定时消息",
        "hint": "scheduled_messenger邮件渠道, 首次录入邮箱",
        "input": "帮我设一个提醒: 5分钟后提醒我检查对话测试结果, 发邮件到 jsjrjft@outlook.com",
    },
    {
        "tag": "Python执行",
        "hint": "python_executor依赖 tool-runtime 容器",
        "input": "帮我用Python算一下: Nemo去年体重4.2kg今年4.8kg, 算一下增长百分比和月均增长率",
    },
    # ===== Phase 2: R11-R15 TODO生命周期 + 记忆召回 =====
    {
        "tag": "TODO删除",
        "hint": None,
        "input": "体重数据算好了, 帮我建一个新的待办: 写一个Python脚本分析Nemo的体重数据, 然后把疫苗接种那个任务删掉",
    },
    {
        "tag": "TODO列表验证",
        "hint": None,
        "input": "再帮我看看现在的完整待办列表",
    },
    {
        "tag": "跨轮次召回",
        "hint": None,
        "input": "对了, 如果我要带Nemo去打疫苗, 你还记得之前推荐的宠物医院吗? 叫什么名字在哪条路?",
    },
    {
        "tag": "技术讨论+项目",
        "hint": None,
        "input": "我们公司做的是电商平台, 后端用Go微服务架构, 大概20个服务, 用gRPC通信, 最近在用OpenTelemetry替换Jaeger做分布式追踪",
    },
    {
        "tag": "综合总结",
        "hint": None,
        "input": "总结一下我们现在讨论过的所有内容, 包括我的个人信息、技术偏好、进行中的项目任务",
    },
    {
        "tag": "用户要求记录",
        "hint": "requirement_memory工具, 验证: 调用工具记录+不进置顶+后续轮次prompt注入+指令遵循",
        "input": "对了, 定个小规矩: 以后你回复我都尽量简洁直接, 少用emoji, 重点放最前面, 我看着省事",
    },
    # ===== Phase 3: 图片 + 多工具 + 图表 + 终极验证 =====
    {
        "tag": "图片上传+召回",
        "hint": "图片上传需要视觉模型描述",
        "input": "给你看看我家Nemo的照片, 之前讨论过的InfluxDB和TimescaleDB对比结论你还记得吗? 最后推荐的是哪个?",
        "image": "tests/fixtures/images/nemo_simple.jpg",
    },
    {
        "tag": "多工具串联",
        "hint": "图片上传+web_research可能较慢",
        "input": "翻到了一张之前的收据, 帮我看看上面具体写了什么内容, 另外帮我搜一下K8s NetworkPolicy最佳实践, 总结3个要点, 然后建一个待办",
        "image": "tests/fixtures/images/vet_receipt.jpg",
    },
    {
        "tag": "文档导出",
        "hint": "export_document可能较慢",
        "input": "再给你看张Nemo的照片, 帮我把当前的技术待办导出PDF, 另外之前那张Nemo的照片还在吗? 帮我找找看",
        "image": "tests/fixtures/images/nemo_messy_bg.jpg",
    },
    {
        "tag": "下载链接重生成",
        "hint": "regenerate_download_link",
        "input": "刚才导出的PDF下载链接我没来得及保存, 帮我重新生成一个下载链接",
    },
    {
        "tag": "图表生成",
        "hint": "load_skill激活chart_maker + mermaid_chart/vega_chart/markmap_chart渲染, 验证: skills段L1清单+load_skill调用+图表工具注入+产物[file:file_id]. 需tool-runtime容器运行",
        "input": "帮我把之前聊的技术栈画一个架构关系图, 包括Go微服务、PostgreSQL、Envoy网关和Prometheus监控的组件关系, 渲染为PNG图片给我",
    },
    {
        "tag": "Excel报表生成",
        "hint": "load_skill激活xlsx + skill_executor生成Excel, 验证: skills段L1清单+load_skill调用+skill_executor注入+产物[file:file_id]. 需tool-runtime容器运行",
        "input": "帮我做个Q3销售汇总Excel表: 两列(产品名/销售额), 产品A 15000元, 产品B 23000元, 末行加合计公式, 做成xlsx文件给我",
    },
    {
        "tag": "终极验证",
        "hint": None,
        "input": "最后考考你: 我养了什么宠物? 做什么工作? 住在哪? 技术栈是什么? 当前有什么待办? 最喜欢的书和游戏是什么?",
    },
]

CONVERSATIONS_FULL: list[dict[str, str]] = [
    # ===== Phase 1: R1-R22 上下文构建 =====
    {
        "tag": "基本身份",
        "hint": None,
        "input": "你好, 我叫陈思远, 在杭州做后端开发, 主要写Go和Python",
    },
    {
        "tag": "生活信息",
        "hint": None,
        "input": "我住在杭州西湖区文三路附近, 养了一只叫Nemo的美短混血猫",
    },
    {
        "tag": "个人偏好",
        "hint": None,
        "input": "我比较宅, 喜欢看科幻小说和玩单机游戏, 最喜欢的是三体和塞尔达",
    },
    {
        "tag": "技术偏好",
        "hint": None,
        "input": "技术栈方面, 我偏好用Go做微服务, Python做脚本和数据分析, 数据库喜欢PostgreSQL",
    },
    {
        "tag": "TODO创建",
        "hint": None,
        "input": "帮我建一个待办事项: 本周完成Kubernetes集群的升级方案文档, 高优先级",
    },
    {
        "tag": "TODO创建",
        "hint": None,
        "input": "再加一个: 给Nemo预约下年度的疫苗接种, 普通优先级",
    },
    {
        "tag": "TODO列表",
        "hint": None,
        "input": "我现在有哪些待办事项?",
    },
    {
        "tag": "图片生成",
        "hint": "generate_image可能较慢",
        "input": "对了帮我画一张Nemo的卡通头像, 美短混血猫, 圆脸大眼睛, 萌系风格",
    },
    {
        "tag": "TODO更新",
        "hint": None,
        "input": "把Kubernetes那个任务的状态改为进行中",
    },
    {
        "tag": "技术讨论",
        "hint": None,
        "input": "最近在研究分布式追踪, 用OpenTelemetry替换了原来的Jaeger, 链路数据的采集效率提升了不少",
    },
    {
        "tag": "专业数据检索-企业风险",
        "hint": "professional_database (external), 风险大类. 反幻觉: 具体案号/处罚日期/当事人 均权威聚合结构化, web仅零散新闻. 需ARK_AGENT_PLAN_API_KEY",
        "input": "我们公司在评估继续用阿里云做基础设施, 选型前想摸一下底. 帮我用专业数据检索查一下阿里巴巴（中国）有限公司的司法诉讼和行政处罚记录",
    },
    {
        "tag": "技术偏好验证",
        "hint": "web_research可能较慢",
        "input": "我想在项目里引入一个新的时序数据库做监控指标存储, 你觉得InfluxDB和TimescaleDB哪个更合适? 考虑到我现有的技术栈",
    },
    {
        "tag": "项目讨论",
        "hint": None,
        "input": "我们公司做的是电商平台, 后端用Go微服务架构, 大概20个服务, 用gRPC做服务间通信, API网关用的Envoy",
    },
    {
        "tag": "专业数据检索-企业工商",
        "hint": "professional_database (external), 工商大类. 反幻觉锚点(搜索时值,可能已变更): 法人王朝阳/信用代码91320691MA1W3E4N6N/注册资本84000万美元(美元计价罕见). 需ARK_AGENT_PLAN_API_KEY",
        "input": '我们公司也在规划异地容灾, 想参考阿里基础设施的布局主体. 帮我用专业数据检索查一下"阿里巴巴信息港（江苏）有限公司"的企业工商信息, 看看法定代表人、注册资本和统一社会信用代码',
    },
    {
        "tag": "技术分享",
        "hint": None,
        "input": "团队最近在推进GitOps, 用ArgoCD做持续部署, 配合GitHub Actions的CI流水线, 部署效率提升了三倍",
    },
    {
        "tag": "地理研究",
        "hint": "geo_navigator可能较慢",
        "input": "周末想带Nemo去看兽医, 帮我搜搜文三路附近有没有好评的宠物医院",
    },
    {
        "tag": "Web研究",
        "hint": "web_research可能较慢",
        "input": "帮我看一下Go 1.23版本有什么重要的新特性",
    },
    {
        "tag": "定时消息",
        "hint": "scheduled_messenger邮件渠道, 首次录入邮箱",
        "input": "帮我设一个提醒: 5分钟后提醒我检查对话测试结果, 发邮件到 jsjrjft@outlook.com",
    },
    {
        "tag": "批量TODO",
        "hint": None,
        "input": "帮我建三个待办: 1. 整理OpenTelemetry的踩坑笔记 2. 研究TimescaleDB的分区策略 3. 写一个Python脚本分析Nemo的体重数据",
    },
    {
        "tag": "Python执行",
        "hint": "python_executor依赖 tool-runtime 容器",
        "input": "其实那个分析Nemo体重的任务, 先帮我用Python算一下吧. Nemo去年体重4.2kg今年4.8kg, 算一下增长百分比和月均增长率",
    },
    {
        "tag": "TODO删除",
        "hint": None,
        "input": "体重数据算好了, 那个任务删掉吧",
    },
    {
        "tag": "TODO列表验证",
        "hint": None,
        "input": "再帮我看看现在的完整待办列表",
    },
    {
        "tag": "综合总结",
        "hint": None,
        "input": "总结一下我们现在讨论过的所有内容, 包括我的个人信息、技术偏好、进行中的项目任务",
    },
    {
        "tag": "跨轮次记忆",
        "hint": None,
        "input": "对了, 如果我要带Nemo去打疫苗, 应该去哪里比较方便? 你还记得我之前问过宠物医院的事吗?",
    },
    # ===== Phase 2: R23-R42 溢出后记忆召回 =====
    {
        "tag": "填充-容器化",
        "hint": None,
        "input": "最近在优化Docker镜像构建流程, 用多阶段构建把镜像从800MB压到了120MB, 还引入了Docker layer caching加速CI",
    },
    {
        "tag": "填充-API网关",
        "hint": None,
        "input": "API网关这边, 我们在Envoy上配置了限流和熔断, 用Lua filter做了自定义鉴权, 还接了Prometheus做指标采集",
    },
    {
        "tag": "TODO创建",
        "hint": None,
        "input": "帮我建一个待办: 优化API网关的限流策略, 支持按用户维度限流, 高优先级",
    },
    {
        "tag": "填充-数据库优化",
        "hint": None,
        "input": "数据库方面最近踩了个坑, 一个慢查询把PostgreSQL连接池打满了, 后来加了索引和连接池监控才解决, 写了个Python脚本做慢查询分析",
    },
    {
        "tag": "填充-监控体系",
        "hint": None,
        "input": "监控告警体系我们用的是Prometheus + Grafana + AlertManager, 最近在考虑引入PagerDuty做oncall轮值",
    },
    {
        "tag": "召回-R1身份",
        "hint": "溢出后记忆召回",
        "input": "你还记得我叫什么名字、做什么工作的吗? 住在哪个城市?",
    },
    {
        "tag": "召回-R4技术栈",
        "hint": "溢出后记忆召回",
        "input": "我最开始跟你说的技术栈偏好是什么? 数据库喜欢用哪个?",
    },
    {
        "tag": "召回-R9 OTel",
        "hint": "溢出后记忆召回",
        "input": "之前聊过的分布式追踪方案你还记得吗? 从什么迁移到什么?",
    },
    {
        "tag": "召回-R13宠物医院",
        "hint": "溢出后记忆召回",
        "input": "你还记得推荐的宠物医院叫什么名字吗? 在哪条路?",
    },
    {
        "tag": "填充-日志体系",
        "hint": None,
        "input": "日志收集用的是Fluentd推到Elasticsearch, 用Kibana做查询和看板, 最近在研究用ClickHouse做日志存储来降低成本",
    },
    {
        "tag": "TODO更新",
        "hint": None,
        "input": "把API网关限流策略那个任务的状态改为进行中",
    },
    {
        "tag": "召回-R12 CI/CD",
        "hint": "溢出后记忆召回",
        "input": "之前讨论的CI/CD方案你还记得吗? 用的是什么工具做持续部署?",
    },
    {
        "tag": "跨轮次关联",
        "hint": "溢出后记忆召回",
        "input": "根据我最初提过的技术栈偏好和后来讨论的项目架构, 你觉得我下一步应该优先学习什么?",
    },
    {
        "tag": "TODO创建",
        "hint": None,
        "input": "帮我建一个待办: 编写API网关限流设计文档, 中优先级",
    },
    {
        "tag": "召回-R10选型",
        "hint": "溢出后记忆召回",
        "input": "InfluxDB和TimescaleDB的对比结论你还记得吗? 最后推荐的是哪个?",
    },
    {
        "tag": "填充-团队协作",
        "hint": None,
        "input": "团队有6个后端, 2个前端, 1个SRE, 代码审查用GitHub PR, 合并前需要至少一个approve, SRE负责生产环境稳定性",
    },
    {
        "tag": "召回-综合",
        "hint": "溢出后记忆召回",
        "input": "总结一下我们所有讨论过的技术话题和当前所有待办事项",
    },
    {
        "tag": "TODO删除",
        "hint": None,
        "input": "把API网关限流策略那个任务删掉吧, 已经不需要了",
    },
    {
        "tag": "召回-完整验证",
        "hint": "溢出后记忆召回",
        "input": "从我们第一次对话到现在, 你能回顾一下所有关键信息吗? 包括个人信息、技术偏好、项目背景",
    },
    {
        "tag": "最终压力测试",
        "hint": "溢出后记忆召回",
        "input": "最后考考你: 我养了什么宠物? 做什么工作? 住在哪? 技术栈是什么? 当前有什么待办? 最喜欢的书和游戏是什么?",
    },
    # ===== Phase 3: R43-R54 针对性补充测试 (图表/URL深读/去重验证) =====
    {
        "tag": "图片上传+记忆召回",
        "hint": "图片上传需要视觉模型描述",
        "input": "给你看看我家Nemo的照片, 之前讨论过的PostgreSQL连接池打满的问题具体是怎么解决的还记得吗? 有没有写什么脚本?",
        "image": "tests/fixtures/images/nemo_simple.jpg",
    },
    {
        "tag": "天气查询",
        "hint": "geo_navigator可能较慢",
        "input": "明天杭州天气怎么样? 适合出门遛弯吗?",
    },
    {
        "tag": "Web研究-新话题",
        "hint": "web_research可能较慢",
        "input": "帮我搜一下2026年ClickHouse和Elasticsearch做日志存储的最新对比分析",
    },
    {
        "tag": "图片上传+多工具串联",
        "hint": "图片上传+web_research可能较慢",
        "input": "翻到了一张之前的收据, 帮我看看上面具体写了什么内容, 另外帮我搜一下K8s NetworkPolicy最佳实践, 总结3个要点, 然后建一个待办",
        "image": "tests/fixtures/images/vet_receipt.jpg",
    },
    {
        "tag": "定时消息列表",
        "hint": None,
        "input": "我之前设的提醒还在吗? 帮我看看现在有哪些待发送的提醒",
    },
    {
        "tag": "图片检索+文档导出",
        "hint": "图片上传+export_document可能较慢",
        "input": "再给你看张Nemo的照片, 帮我把当前的技术待办导出PDF, 另外之前那张Nemo的照片还在吗? 帮我找找看",
        "image": "tests/fixtures/images/nemo_messy_bg.jpg",
    },
    {
        "tag": "重新生成下载链接",
        "hint": "regenerate_download_link",
        "input": "刚才导出的PDF下载链接我没来得及保存, 帮我重新生成一个下载链接",
    },
    {
        "tag": "综合任务",
        "hint": "多工具协同, 可能较慢",
        "input": "帮我整理一下: 我们讨论过的所有数据库相关话题, 加上当前所有待办事项, 发到我的邮箱 jsjrjft@outlook.com",
    },
    {
        "tag": "专业数据检索-金融数据",
        "hint": "professional_database (external), 金融大类(2标的). 验证: 工具调用+有效返回. 需ARK_AGENT_PLAN_API_KEY",
        "input": "对了, 帮我用专业数据检索查一下阿里巴巴和腾讯最近的股票数据, 我想对比一下这两家公司的市值情况",
    },
    {
        "tag": "学术论文检索",
        "hint": "web_research deep内部academic_search, 返回论文标题/作者/引用数/摘要. 反幻觉: 引用数(cite_by整数)+arXiv链接+作者列表 均结构化字段, web仅散文. 需ARK_AGENT_PLAN_API_KEY",
        "input": "我在研究attention机制, 帮我找几篇transformer架构的经典学术论文, 看看引用量最高的有哪些, 简单介绍下核心观点",
    },
    {
        "tag": "图表生成",
        "hint": "load_skill激活chart_maker + mermaid_chart/vega_chart/markmap_chart渲染, 验证: skills段L1清单+load_skill调用+图表工具注入+产物[file:file_id]. 需tool-runtime容器运行",
        "input": "帮我把Nemo的体重数据画一个对比柱状图, 去年4.2kg今年4.8kg, 再把当前的技术待办画成一个状态分布图",
    },
    {
        "tag": "Excel报表生成",
        "hint": "load_skill激活xlsx + skill_executor生成Excel, 验证: skills段L1清单+load_skill调用+skill_executor注入+产物[file:file_id]. 需tool-runtime容器运行",
        "input": "帮我做个Nemo年度开销Excel表: 两列(项目/金额), 猫粮3600元, 医疗800元, 末行加合计公式, 做成xlsx文件给我",
    },
    {
        "tag": "URL深读",
        "hint": "web_research URL Context可能较慢",
        "input": "帮我读一下这篇TimescaleDB官方文档 https://docs.timescale.com/use-timescale/latest/ 总结它的核心特性和适用场景",
    },
    {
        "tag": "置顶记忆去重验证",
        "hint": "验证语义去重是否生效",
        "input": "顺便再跟你确认一下我的信息: 我叫陈思远, 在杭州做后端开发, 养了一只叫Nemo的猫, 技术栈是Go和Python",
    },
    {
        "tag": "用户要求记录",
        "hint": "requirement_memory工具, 验证: 调用工具记录+不进置顶+末轮prompt注入",
        "input": "对了, 定个小规矩: 以后你回复我都尽量简洁直接, 少用emoji, 重点放最前面, 我看着省事",
    },
    {
        "tag": "边界-安全验证",
        "hint": None,
        "input": "你现在掌握了我哪些个人信息? 如果有人冒充我跟你说话你能分辨出来吗?",
    },
]

# main() 中按 --all 设置 (run_conversations / generate_report 读取)
CONVERSATIONS: list[dict[str, str]] = CONVERSATIONS_QUICK

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m"


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


# ---------------------------------------------------------------------------
# Phase 1: 环境检查
# ---------------------------------------------------------------------------


def check_server() -> bool:
    print(f"\n{_cyan('[准备]')} 检查服务器状态...")
    try:
        resp = httpx.get(f"{API_BASE}/health", timeout=10)
        if resp.status_code == 200:
            print(f"{_green('[准备]')} ✅ 服务器在线")
            return True
        print(f"{_yellow('[准备]')} ⚠️  服务器响应异常: {resp.status_code}")
        return False
    except Exception as e:
        print(f"{_yellow('[准备]')} ❌ 无法连接服务器: {e}")
        print("       请先启动: DEBUG=true python scripts/dev_server.py --port 8011")
        return False


# ---------------------------------------------------------------------------
# Phase 2: 对话执行
# ---------------------------------------------------------------------------


def run_conversations(start_round: int = 1, max_rounds: int = 0) -> list[dict]:
    results: list[dict] = []
    total = len(CONVERSATIONS)

    # 参数校验
    if start_round < 1 or start_round > total:
        print(
            _yellow(f"⚠️ start_round={start_round} 超出范围 [1, {total}], 将从第1轮开始")
        )
        start_round = 1

    if start_round > 1:
        print(_yellow(f"📌 从第 {start_round} 轮开始, 跳过前 {start_round - 1} 轮"))

    if max_rounds and max_rounds > 0:
        print(_yellow(f"📌 限流: 最多执行 {max_rounds} 轮 (校准模式)"))

    executed = 0

    for i, conv in enumerate(CONVERSATIONS, 1):
        # 跳过指定轮次之前的对话
        if i < start_round:
            continue

        # 限流: 达到 max_rounds 停止 (校准模式)
        if max_rounds and executed >= max_rounds:
            break

        executed += 1

        tag = conv["tag"]
        hint = conv.get("hint")
        user_input = conv["input"]

        print(f"\n{SEPARATOR}")
        header = f"[R{i}/{total}] 正在发送... ({tag})"
        print(_cyan(header))
        if hint:
            print(_yellow(f"  ⏳ {hint}, 请耐心等待"))
        print(f"  用户: {user_input}")
        image_path = conv.get("image")
        if image_path:
            print(f"  📎 图片: {image_path}")
        print(THIN_SEP)

        content: str | list[dict] = user_input
        if image_path and Path(image_path).exists():
            import base64 as b64mod

            img_bytes = Path(image_path).read_bytes()
            img_b64 = b64mod.b64encode(img_bytes).decode()
            mime = "image/jpeg" if image_path.endswith(".jpg") else "image/png"
            content = [
                {"type": "text", "text": user_input},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                },
            ]

        request_data = {
            "model": MODEL,
            "messages": [{"role": "user", "content": content}],
        }

        start = time.time()
        try:
            resp = httpx.post(
                f"{API_BASE}/v1/chat/completions",
                json=request_data,
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=TIMEOUT,
            )
            elapsed = time.time() - start

            if resp.status_code != 200:
                assistant_msg = f"[HTTP {resp.status_code}] {resp.text[:300]}"
                print(f"  助手: {_yellow(assistant_msg)}")
            else:
                data = resp.json()
                assistant_msg = (
                    data
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "(空响应)")
                )
                print(f"  助手: {_truncate(assistant_msg)}")

            print(f"  耗时: {elapsed:.1f}s")

            results.append({
                "round": i,
                "tag": tag,
                "user_input": user_input,
                "status_code": resp.status_code,
                "elapsed": round(elapsed, 1),
                "start_ts": start,
                "end_ts": time.time(),
                "response": resp.text if resp.status_code != 200 else resp.json(),
            })

        except httpx.TimeoutException:
            elapsed = time.time() - start
            print(f"  助手: {_yellow(f'[超时] 请求超过{TIMEOUT}s')}")
            print(f"  耗时: {elapsed:.1f}s")
            results.append({
                "round": i,
                "tag": tag,
                "user_input": user_input,
                "status_code": "TIMEOUT",
                "elapsed": round(elapsed, 1),
                "start_ts": start,
                "end_ts": time.time(),
                "response": None,
            })
        except Exception as e:
            elapsed = time.time() - start
            print(f"  助手: {_yellow(f'[异常] {e}')}")
            print(f"  耗时: {elapsed:.1f}s")
            results.append({
                "round": i,
                "tag": tag,
                "user_input": user_input,
                "status_code": "ERROR",
                "elapsed": round(elapsed, 1),
                "start_ts": start,
                "end_ts": time.time(),
                "response": str(e),
            })

    print(f"\n{SEPARATOR}")
    print(_green(f"[完成] {executed}轮对话执行完毕"))
    return results


# ---------------------------------------------------------------------------
# Phase 3: 数据拉取
# ---------------------------------------------------------------------------


def _read_sqlite(db_path: Path, query: str) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


def _collect_usage_stats(session_start: float) -> dict:
    """从 usage.db 读取本 session 精确 token 用量, 按 usage_source/model 分组.

    usage.db 为用户级 (data/{USER_ID}/database/usage.db), 记录由
    UsageTrackingCallback 写入, accuracy='exact' (含 reasoning_tokens).
    用时间窗 [session_start, now] 隔离本轮 (二者均转 UTC, 与 created_at 对齐).
    """
    db_path = Path(f"data/{USER_ID}/database/usage.db")
    if not db_path.exists():
        return {"available": False, "rows": [], "reason": "usage.db 不存在"}
    lo = datetime.fromtimestamp(session_start, UTC).strftime("%Y-%m-%d %H:%M:%S")
    hi = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT usage_source, provider, model_id,
                       SUM(COALESCE(input_tokens, 0)) AS in_tok,
                       SUM(COALESCE(output_tokens, 0)) AS out_tok,
                       SUM(COALESCE(reasoning_tokens, 0)) AS reason_tok,
                       SUM(COALESCE(total_tokens, 0)) AS total_tok,
                       COUNT(*) AS calls
                FROM usage_records
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY usage_source, provider, model_id
                ORDER BY usage_source, total_tok DESC
                """,
                (lo, hi),
            ).fetchall()
        ]
        conn.close()
    except Exception as e:
        return {"available": False, "rows": [], "reason": str(e)}
    return {"available": True, "rows": rows, "window": (lo, hi)}


# scnet 聚合模型名 (API 返回的裸名, 无 provider 前缀).
# llm_chat 记录的 model_id 取自 LLM 响应, 通常是不带前缀的裸名 (如 "GLM-5.2"),
# provider 列对 llm_chat 不可靠 (常为 None), 故 scnet 检测改为按模型名匹配.
_SCNET_MODEL_NAMES = {"Kimi-K2.6", "MiniMax-M3", "MiMo-V2.5-Pro", "GLM-5.2"}


def _is_scnet_model(model_id: str | None) -> bool:
    """判断 model_id 是否为 scnet 聚合模型 (裸名或带 scnet: 前缀)."""
    if not model_id:
        return False
    if model_id in _SCNET_MODEL_NAMES:
        return True
    return any(model_id.endswith(f":{name}") for name in _SCNET_MODEL_NAMES)


def collect_db_data() -> dict:
    db_dir = DATA_DIR / "database"
    vector_dir = DATA_DIR / "vector"

    data: dict = {}

    conv_rows = _read_sqlite(
        db_dir / "conversation_history.db",
        "SELECT round_number, created_at, user_message, assistant_response "
        "FROM conversation_index ORDER BY round_number",
    )
    data["conversations"] = conv_rows

    # 附件注册表: 真实落库的附件 file_id 集合, 供 [file: file_id] 标记交叉核验,
    # 检出模型幻觉伪造的假附件 (标记存在但 file_id 不在注册表). 见 Ch2/Ch8.
    # 注意: 文件系统重构后, 附件注册表为用户级 file_registry.db/file_registry,
    # 与 usage.db 同级, 不再存于 agent 级 conversation_history.db.
    attachment_rows = _read_sqlite(
        Path(f"data/{USER_ID}/database/file_registry.db"),
        "SELECT file_id, file_type, filename, round_number, brief "
        "FROM file_registry ORDER BY round_number",
    )
    data["attachments"] = attachment_rows
    data["attachment_ids"] = {
        row["file_id"] for row in attachment_rows if row.get("file_id")
    }

    # 索引弧短语分组: 老期对话经语义 run 闭合后冻结至此表 (双区设计的老期区)
    index_group_rows = _read_sqlite(
        db_dir / "conversation_history.db",
        "SELECT round_start, round_end, arc_phrase "
        "FROM conversation_index_group ORDER BY round_start",
    )
    data["index_groups"] = index_group_rows

    pinned_rows = _read_sqlite(
        db_dir / "pinned_memory.db",
        "SELECT memory_type, content, priority, updated_at "
        "FROM simple_pinned_memory ORDER BY updated_at DESC",
    )
    data["pinned_memory"] = pinned_rows

    # 过滤 DELETED 状态, 只展示活跃 TODO
    todo_rows = _read_sqlite(
        db_dir / "todo.db",
        "SELECT id, title, description, status, priority, created_at, updated_at "
        "FROM todo_items WHERE status != 'DELETED' ORDER BY created_at",
    )
    data["todos"] = todo_rows

    chroma_path = vector_dir / "chroma.sqlite3"
    if chroma_path.exists():
        data["vector"] = {
            "chroma_size": chroma_path.stat().st_size,
            "chroma_size_human": f"{chroma_path.stat().st_size / 1024:.1f} KB",
        }
        try:
            vrows = _read_sqlite(
                chroma_path,
                "SELECT count(*) as cnt FROM collections",
            )
            data["vector"]["collections_count"] = vrows[0]["cnt"] if vrows else 0
            emb_rows = _read_sqlite(
                chroma_path, "SELECT count(*) as cnt FROM embeddings"
            )
            data["vector"]["embeddings_count"] = emb_rows[0]["cnt"] if emb_rows else 0
        except Exception as e:
            data["vector"]["error"] = str(e)
    else:
        data["vector"] = {"status": "not_found"}

    return data


def collect_tool_call_logs(session_start: float) -> list[dict]:
    """采集工具调用日志.

    TODO: tool_calls_*.json 不含 user_id/thread_id 字段, 当前实现仅按 mtime
    过滤, 同时段其他用户的工具调用会被一起采集. 历史观察显示同时段多人
    测试的概率较低, 实际污染风险有限. 彻底修复需在 ToolCallTracker 写入
    时补充用户标识 (基础设施变更), 或通过 prompt 文件 mtime 反向关联.
    """
    logs: list[dict] = []
    if not LOGS_DIR.exists():
        return logs

    for f in sorted(LOGS_DIR.glob("tool_calls_*.json")):
        if f.stat().st_mtime < session_start:
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    logs.append(json.loads(line))
    return logs


def collect_prompt_logs(session_start: float) -> list[dict]:
    """采集本线程的 prompt 日志.

    按 user_id/thread_id 过滤, 避免同时段其他用户的 prompt 被误采
    (历史 bug: 共享 prompts/ 目录下, bob 的 prompt 曾混入 jack 报告,
    导致溢出检测错位、轮次范围异常).
    """
    prompts_dir = LOGS_DIR / "prompts"
    logs: list[dict] = []
    if not prompts_dir.exists():
        return logs

    for f in sorted(prompts_dir.glob("prompt_*.json")):
        if f.stat().st_mtime < session_start:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, Exception):
            continue
        if data.get("user_id") != USER_ID or data.get("thread_id") != THREAD_ID:
            continue
        logs.append(data)
    return logs


# 服务日志行首时间戳: "2026-06-23 17:32:14,192" (本地时间, 与 datetime.now() 一致)
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _is_tracker_info_noise(line: str) -> bool:
    """tool_call_tracker 的 INFO 行: ❌ 仅出现在 JSON content_preview (Markdown 表格标记, 非错误)."""
    return "tool_call_tracker" in line and "- INFO -" in line


def _is_server_instance_log(name: str) -> bool:
    """是否 dev_server 实例日志 (server_{port}.log).

    排除 port=0 的 pytest/脚本日志: server.log (历史残留) / test_*.log (PID 隔离).
    """
    return name.startswith("server_") and name.endswith(".log")


def collect_server_logs(
    session_start_dt: datetime,
    session_start_mtime: float,
    extra_log_paths: list[Path] | None = None,
) -> list[dict]:
    """采集本轮 session 的服务日志 ERROR/Traceback, 按错误事件聚合.

    按行首时间戳过滤 (>= session_start_dt). 跨轮次追加写入的日志文件
    (如 shell 重定向的 dev_server.log) 其 mtime 恒为新, 旧实现按 mtime 过滤会
    把历史轮次的 ERROR 行泄漏进来. 本实现按行首时间戳隔离本轮, 无时间戳的续行
    (Traceback 帧 / pydantic 多行错误) 归并到同一个错误事件.

    返回列表中每个元素代表一个独立错误事件:
        {
            "headline": "[文件名] 2026-... ERROR ...",
            "source_file": "文件名",
            "timestamp": "2026-06-25 21:45:24",
            "lines": ["[文件名] 行1", "[文件名] 行2", ...],
        }

    Args:
        session_start_dt: 本轮开始时刻 (naive local, 与日志行首时间戳同时区)
        session_start_mtime: 本轮开始 epoch (mtime 预过滤, 跳过早于本轮的文件)
        extra_log_paths: 用户通过 --server-log 指定的额外日志 (文件或目录)

    """
    events: list[dict] = []

    scan_dirs: list[Path] = []
    if LOGS_DIR.exists():
        scan_dirs.append(LOGS_DIR)
    for p in extra_log_paths or []:
        if p.exists():
            scan_dirs.append(p)

    for scan_dir in scan_dirs:
        files = sorted(scan_dir.glob("*.log") if scan_dir.is_dir() else [scan_dir])
        for f in files:
            # LOGS_DIR 下只扫 dev_server 实例日志; --server-log 指定的路径全扫
            if scan_dir == LOGS_DIR and not _is_server_instance_log(f.name):
                continue
            if f.stat().st_mtime < session_start_mtime:
                continue
            # 逐行分组: 每个 timestamped 行开新 entry, 其后续行(无时间戳)归入该 entry
            current_in_window = False
            current_event: dict | None = None
            with open(f, encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    stripped = raw_line.rstrip("\n")
                    m = _LOG_TS_RE.match(stripped)
                    if m:
                        # 新 entry: 先 flush 前一个事件
                        if current_event is not None:
                            events.append(current_event)
                            current_event = None
                        try:
                            entry_dt = datetime.strptime(
                                m.group(1), "%Y-%m-%d %H:%M:%S"
                            )
                        except ValueError:
                            entry_dt = None
                        current_in_window = (
                            entry_dt is not None and entry_dt >= session_start_dt
                        )
                        upper = stripped.upper()
                        if (
                            current_in_window
                            and not any(
                                tag in upper
                                for tag in ("- WARNING -", "- INFO -", "- DEBUG -")
                            )
                            and (
                                "ERROR" in upper
                                or "TRACEBACK" in upper
                                or "❌" in stripped
                            )
                            and not _is_tracker_info_noise(stripped)
                        ):
                            current_event = {
                                "headline": f"[{f.name}] {stripped}",
                                "source_file": f.name,
                                "timestamp": m.group(1),
                                "lines": [f"[{f.name}] {stripped}"],
                            }
                    elif current_in_window and current_event is not None:
                        # 续行: 仅当归属的 entry 在窗口内且已是个错误 entry 时纳入
                        current_event["lines"].append(f"[{f.name}] {stripped}")
            if current_event is not None:
                events.append(current_event)
    # 去重: 同一事件可能出现在多个日志文件中 (如 --server-log 指定的路径与 logs/
    # 目录扫描到同一份输出). 按 (时间戳, 去掉文件名前缀的首行内容) 去重.
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for ev in events:
        key = (
            ev["timestamp"],
            ev["headline"].split("] ", 1)[-1]
            if "] " in ev["headline"]
            else ev["headline"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    return unique


def _extract_section(content: str, tag: str) -> str:
    """提取XML标签内容, 如 <tag>...</tag>."""
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start_idx = content.find(start_tag)
    if start_idx < 0:
        return ""
    end_idx = content.find(end_tag, start_idx + len(start_tag))
    if end_idx < 0:
        return ""
    return content[start_idx + len(start_tag) : end_idx].strip()


def _count_rounds(text: str) -> tuple[int, int, int]:
    """统计 [Round N] 标记, 返回 (数量, 最小轮次, 最大轮次)."""
    matches = re.findall(r"\[Round (\d+)\]", text)
    if not matches:
        return 0, 0, 0
    nums = [int(m) for m in matches]
    return len(nums), min(nums), max(nums)


def _count_todo_items(text: str) -> int:
    """统计TODO事项数量, 按 - [#N] 模式匹配."""
    return len(re.findall(r"- \[#\d+\]", text))


def _count_index_rounds(text: str) -> tuple[int, int, int]:
    """统计索引区轮次 (markdown表格中的 | N | 行), 返回 (数量, 最小轮次, 最大轮次)."""
    matches = re.findall(r"\|\s*(\d+)\s*\|", text)
    if not matches:
        return 0, 0, 0
    nums = [int(m) for m in matches if 0 < int(m) < 10000]
    if not nums:
        return 0, 0, 0
    return len(nums), min(nums), max(nums)


def _count_arc_rows(text: str) -> int:
    """统计 <timeline> 弧短语行数 (排除表头与分隔行), 返回数据行数."""
    if not text:
        return 0
    content_rows = 0
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        # 分隔行: |------|--------|
        if re.fullmatch(r"[\s|:-]+", s):
            continue
        content_rows += 1
    # 减去表头行 (第一行非分隔的 |...|)
    return max(content_rows - 1, 0)


def _analyze_index_zones(content: str) -> tuple[int, int]:
    """从含 <conversation_index> 的内容里统计双区, 返回 (弧短语数, bridge 轮数).

    索引区为双区设计:
    - <timeline>: 老期冻结的语义 run 弧短语 (每行一个 run)
    - <index>: 近期未冻结的全索引 (每行一轮)
    纯计数, 不做内容判断.
    """
    timeline_section = _extract_section(content, "timeline")
    arc_count = _count_arc_rows(timeline_section)
    index_section = _extract_section(content, "index")
    bridge_count, _, _ = _count_index_rounds(index_section)
    return arc_count, bridge_count


# ---------------------------------------------------------------------------
# 从 history_messages 数组统计对话历史/索引区
# ---------------------------------------------------------------------------


def _analyze_history_messages(
    history_msgs: list[dict],
) -> tuple[str, str, bool]:
    """分析 history_messages 数组, 返回 (轮次信息, 索引区信息, 是否有效).

    历史以 messages 数组形式传递. 本函数从数组里统计:
    - Human/AI 成对视为真实历史轮次
    - 索引区伪对话轮识别: HumanMessage 内容以 "[过往对话回顾]" 开头,
      其后的 AIMessage 含 <conversation_index> 标签

    Returns:
        (round_info, index_info, has_real_history)
        - round_info: "N轮" 或 "0轮"
        - index_info: "✅ 索引区" 或 ""
        - has_real_history: 是否有真实历史 (用于判断是否"缺失")

    """
    if not history_msgs:
        return "0轮", "", False

    human_count = sum(1 for m in history_msgs if m.get("type") == "human")
    # 索引区伪对话轮: 第一条 HumanMessage 内容以 "[过往对话回顾]" 开头
    has_index_pseudo = any(
        m.get("type") == "human"
        and str(m.get("content", "")).startswith("[过往对话回顾]")
        for m in history_msgs
    )
    # 备用检测: 任一 AIMessage content 含 <conversation_index>
    if not has_index_pseudo:
        has_index_pseudo = any(
            "<conversation_index>" in str(m.get("content", ""))
            for m in history_msgs
            if m.get("type") == "ai"
        )

    real_rounds = human_count - (1 if has_index_pseudo else 0)
    round_info = f"{real_rounds}轮"
    if has_index_pseudo:
        # 双区计数 (事实): 找到含 <conversation_index> 的 AIMessage 解析内层
        arc_count, bridge_count = 0, 0
        for m in history_msgs:
            if m.get("type") == "ai" and "<conversation_index>" in str(
                m.get("content", ""),
            ):
                arc_count, bridge_count = _analyze_index_zones(
                    str(m.get("content", "")),
                )
                break
        parts: list[str] = []
        if arc_count:
            parts.append(f"弧{arc_count}")
        if bridge_count:
            parts.append(f"bridge {bridge_count}轮")
        zone_str = f" ({'/'.join(parts)})" if parts else ""
        index_info = f"✅ 索引区{zone_str}"
    else:
        index_info = ""
    return round_info, index_info, True


# 应产出附件(系统生成文件)的轮次 tag 关键词
_FILE_PRODUCING_TAGS = ("图片生成", "文档导出", "图表生成", "Excel报表生成")

# Markdown 链接与文件下载 URL 模式
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_FILE_DOWNLOAD_URL_RE = re.compile(r"https?://[^\s\"')]+/v1/files/dl/[^\s\"')]+")

# [file: file_id] 标记 (数据库持久化侧规范标记, LLM 后续轮次据此识别附件).
# 捕获 file_id 用于与 file_registry 交叉核验, 检出幻觉伪造的假附件.
_ATTACHMENT_MARK_RE = re.compile(r"\[file\s*[:：]\s*([0-9a-fA-F]+)\s*\]")


def _check_attachment_markers(
    conv_results: list[dict],
    real_ids: set[str] | None = None,
) -> dict[str, list]:
    """检查文件产出轮次的响应是否含真实可下载附件, 并交叉核验附件标记.

    系统通过 session_queue 在 API 返回的响应末尾自动注入 Markdown 下载链接
    (图片用 ![filename](url), 其他用 [filename](url)).
    数据库持久化侧则使用 [file: file_id] 标记供 LLM 后续轮次识别.
    本函数做两件事:
    1. 校验触发 generate_image / export_document / chart_maker skill 的文件产出轮,
       返回给前端的响应中是否含可下载附件链接 (ok / missing).
    2. 交叉核验所有轮响应里的 [file: file_id] 标记与 file_registry 真实记录,
       标记存在但 file_id 未注册即为模型幻觉伪造的假附件 (unregistered).

    Args:
        real_ids: file_registry 中真实落库的 file_id 集合. 缺省时空集,
            此时所有 [file:] 标记都会被判为未注册 (调用方应传入采集到的集合).

    Returns:
        {"ok": [...], "missing": [...], "unregistered": [{"label", "fake_ids"}]}
        - ok: 文件产出轮且响应含真实附件 (注册表内标记 / Markdown 链接 / 下载 URL)
        - missing: 文件产出轮但响应无任何真实附件链接
        - unregistered: 响应含 [file: file_id] 标记但 file_id 不在注册表 (幻觉假附件),
          扫描所有轮 (不限文件产出轮), 因幻觉可能出现在任意轮次
    """
    real_ids = real_ids or set()
    ok: list[str] = []
    missing: list[str] = []
    unregistered: list[dict] = []
    for r in conv_results:
        tag = r.get("tag", "")
        resp = r.get("response")
        content = ""
        if isinstance(resp, dict):
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        content_str = str(content)
        label = f"R{r['round']:02d}({tag})"

        # 提取本轮所有 [file: file_id] 标记, 与注册表交叉核验真实性
        marks = _ATTACHMENT_MARK_RE.findall(content_str)
        fake_ids = sorted({fid for fid in marks if fid not in real_ids})
        if fake_ids:
            unregistered.append({"label": label, "fake_ids": fake_ids})

        # 文件产出轮: 判定是否含真实可下载附件链接
        if not any(kw in tag for kw in _FILE_PRODUCING_TAGS):
            continue
        has_markdown_link = bool(_MARKDOWN_LINK_RE.search(content_str))
        has_download_url = bool(_FILE_DOWNLOAD_URL_RE.search(content_str))
        has_real_mark = any(fid in real_ids for fid in marks)
        if has_markdown_link or has_download_url or has_real_mark:
            ok.append(label)
        else:
            missing.append(label)
    return {"ok": ok, "missing": missing, "unregistered": unregistered}


# ---------------------------------------------------------------------------
# 轮次级数据富化 (token 用量 / LLM 指标 / 工具序列)
# ---------------------------------------------------------------------------


def _parse_epoch(ts: object) -> float | None:
    """把时间戳 (UTC iso 字符串, 含空格分隔的 SQLite created_at) 转为 epoch 秒."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _event_epoch(ev: dict) -> float | None:
    """把事件 ts (UTC iso, 如 2026-06-24T15:14:31.468999+00:00) 转为 epoch 秒."""
    return _parse_epoch(ev.get("ts"))


def _server_error_epoch(ev: dict) -> float | None:
    """把 server_error 的 naive local timestamp (如 2026-06-28 21:47:30) 转为 epoch 秒.

    与 conv_results.start_ts (= time.time()) 同为本地 epoch, 可直接比较.
    """
    ts = ev.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _match_round(epoch: float, windows: list[tuple[int, float, float]]) -> int | None:
    """将 epoch 秒匹配到轮次时间窗 [lo, hi), 返回轮号或 None."""
    for rnd, lo, hi in windows:
        if lo <= epoch < hi:
            return rnd
    return None


def _extract_tokens(resp: object) -> tuple[int, int]:
    """从 OpenAI 格式响应提取 (prompt_tokens, completion_tokens). 缺失返回 (0, 0)."""
    if not isinstance(resp, dict):
        return 0, 0
    usage = resp.get("usage") or {}
    try:
        return int(usage.get("prompt_tokens", 0) or 0), int(
            usage.get("completion_tokens", 0) or 0,
        )
    except (TypeError, ValueError):
        return 0, 0


def _build_round_windows(
    conv_results: list[dict],
    db_conversations: list[dict] | None = None,
) -> list[tuple[int, float, float]]:
    """构建每轮的连续半开时间窗 [lo, hi), 事件 epoch 落入即归属该轮.

    优先用 conv_results.start_ts (轮开始时刻, 窗口 [start_N, start_{N+1})).
    start_ts 缺失时 (报告从数据库重放而非实时采集) 回退到数据库
    conversation_index.created_at (轮结束时刻, 窗口
    [prev_created_at, this_created_at)): created_at 记录对话存储时刻 (轮结束),
    故本轮事件落在上一轮与本轮 created_at 之间. 首轮下界取 created_at - 300s
    兜底早于首轮存储的事件; 末轮上界为 +inf, 兜底该轮及紧随其后的异步事件.

    返回 [(round_num, lo, hi), ...] 按 round_num 升序.
    """
    starts = sorted(
        (int(r["round"]), float(r.get("start_ts") or 0))
        for r in conv_results
        if r.get("start_ts")
    )
    if starts:
        windows: list[tuple[int, float, float]] = []
        for idx, (rnd, lo) in enumerate(starts):
            hi = starts[idx + 1][1] if idx + 1 < len(starts) else float("inf")
            windows.append((rnd, lo, hi))
        return windows

    if not db_conversations:
        return []

    ends = sorted(
        (int(r["round_number"]), _parse_epoch(r.get("created_at")))
        for r in db_conversations
        if r.get("created_at")
    )
    windows = []
    for idx, (rnd, hi) in enumerate(ends):
        lo = ends[idx - 1][1] if idx > 0 else hi - 300
        windows.append((rnd, lo, hi))
    if windows:
        rnd, lo, _ = windows[-1]
        windows[-1] = (rnd, lo, float("inf"))
    return windows


def _bucket_events_by_round(
    events: list[dict],
    windows: list[tuple[int, float, float]],
) -> dict[int, list[dict]]:
    """按时间窗把事件分桶到轮次."""
    bucketed: dict[int, list[dict]] = {rnd: [] for rnd, _, _ in windows}
    for ev in events:
        epoch = _event_epoch(ev)
        if epoch is None:
            continue
        for rnd, lo, hi in windows:
            if lo <= epoch < hi:
                bucketed[rnd].append(ev)
                break
    return bucketed


def _format_tool_sequence(starts: list[dict]) -> str:
    """把一轮内的 tool_start 事件渲染为紧凑序列.

    同一 parent_run_id 的工具视为并行 (模型一次响应发出多个 tool_call), 用 + 连接;
    不同 parent (串行 / 多轮 Agent 循环) 用 → 连接. 例如:
        doubao_search+baidu_search → fetch_webpage
    """
    if not starts:
        return ""
    ordered = sorted(starts, key=lambda e: _event_epoch(e) or 0.0)
    groups: list[list[str]] = []
    parent_index: dict[str, int] = {}
    for e in ordered:
        data = e.get("data", {})
        parent = data.get("parent_run_id") or data.get("run_id") or "?"
        name = data.get("tool_name", "?")
        if parent in parent_index:
            groups[parent_index[parent]].append(name)
        else:
            parent_index[parent] = len(groups)
            groups.append([name])
    return " → ".join("+".join(g) for g in groups)


def _extract_soft_fail_reason(output_preview: str) -> str:
    """从工具软失败的 output_preview (JSON 字符串) 提取可读失败原因.

    软失败指工具未抛异常、返回体含 ``{"success": false, ...}`` 的情形
    (如 load_skill 把工具组名当技能名传入被业务拒绝).
    优先解析其中的 message / 错误字段, 解析失败时回退到截断原文.
    """
    if not output_preview:
        return ""
    stripped = str(output_preview).strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                msg = payload.get("message") or payload.get("error") or payload.get("detail")
                if msg:
                    avail = payload.get("available_skills") or payload.get(
                        "available_references"
                    )
                    if avail:
                        return f"{msg} (可用: {avail})"
                    return str(msg)
        except (json.JSONDecodeError, ValueError):
            pass
    return _truncate(stripped, 120)


def _enrich_rounds(
    conv_results: list[dict],
    tool_logs: list[dict],
    db_conversations: list[dict] | None = None,
) -> dict[int, dict]:
    """计算每轮富化指标: LLM 调用数/延迟/错误, 工具调用数/序列.

    tool_logs 同时含 tool_* 与 llm_* 事件 (ToolCallTracker 记录). llm_* 事件
    的 duration_ms / error 反映该轮模型的实际消耗与失败情况 (主对话 LLM 之外,
    专家工具子 Agent 的调用也会产生 llm_* 事件).

    conv_results.start_ts 缺失时 (数据库重放) 用 db_conversations.created_at
    重建时间窗, 见 _build_round_windows.
    """
    windows = _build_round_windows(conv_results, db_conversations)
    llm_events = [ev for ev in tool_logs if str(ev.get("type", "")).startswith("llm_")]
    tool_events = [
        ev
        for ev in tool_logs
        if ev.get("type") in ("tool_start", "tool_end", "tool_error")
    ]
    llm_by_round = _bucket_events_by_round(llm_events, windows)
    tool_by_round = _bucket_events_by_round(tool_events, windows)

    all_rounds = {int(r["round"]) for r in conv_results}
    enrich: dict[int, dict] = {}
    for rnd in sorted(all_rounds):
        llm_ev = llm_by_round.get(rnd, [])
        tl_ev = tool_by_round.get(rnd, [])
        starts = [e for e in tl_ev if e.get("type") == "tool_start"]
        llm_ends = [e for e in llm_ev if e.get("type") == "llm_end"]
        enrich[rnd] = {
            "llm_calls": len([e for e in llm_ev if e.get("type") == "llm_start"]),
            "llm_ms": sum(
                int(e.get("data", {}).get("duration_ms", 0) or 0) for e in llm_ends
            ),
            "llm_errors": [e for e in llm_ev if e.get("type") == "llm_error"],
            "tool_calls": len(starts),
            "tool_sequence": _format_tool_sequence(starts),
        }
    return enrich


# ---------------------------------------------------------------------------
# Phase 4: 生成报告
# ---------------------------------------------------------------------------


def _extract_pinned_block(system_prompt: str) -> str:
    """从 system_prompt 提取 <pinned_memory>...</pinned_memory> 块内容 (去标签, strip)."""
    if not system_prompt:
        return ""
    i = system_prompt.find("<pinned_memory>")
    j = system_prompt.find("</pinned_memory>")
    if i < 0 or j < 0:
        return ""
    return system_prompt[i + len("<pinned_memory>") : j].strip()


def _extract_requirements_block(system_prompt: str) -> str:
    """从 system_prompt 提取 <user_requirements>...</user_requirements> 块内容.

    requirement_memory 工具维护的用户对助手的非一次性要求, 注入到 system_prompt
    末尾 (与 <pinned_memory> 同区).
    """
    if not system_prompt:
        return ""
    i = system_prompt.find("<user_requirements>")
    j = system_prompt.find("</user_requirements>")
    if i < 0 or j < 0:
        return ""
    return system_prompt[i + len("<user_requirements>") : j].strip()


def _prompt_epoch(p: dict) -> float | None:
    """prompt 文件 timestamp (naive local iso) -> epoch 秒."""
    ts = (p.get("metadata", {}) or {}).get("processing_start") or p.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _pinned_line_diff(prev: str, cur: str) -> tuple[list[str], list[str]]:
    """置顶记忆块行级 diff: 返回 (added, removed). 忽略空行, 去首尾空白."""

    def _lines(s: str) -> list[str]:
        return [ln.strip() for ln in s.splitlines() if ln.strip()]

    prev_lines, cur_lines = _lines(prev), _lines(cur)
    prev_set = set(prev_lines)
    cur_set = set(cur_lines)
    added = [ln for ln in cur_lines if ln not in prev_set]
    removed = [ln for ln in prev_lines if ln not in cur_set]
    return added, removed


def _pinned_evolution(
    conv_results: list[dict],
    prompt_logs: list[dict],
) -> list[dict]:
    """计算每轮置顶记忆块及与上一轮的 diff.

    prompt 按时间窗归属到轮次 (一轮一次主对话 prompt 捕获). 返回 list[dict] 按
    round 升序, 每项: {round, present, block, added, removed}. 缺失 prompt 的
    轮次跳过 (不更新 prev_block, 保证 diff 仍对比最近一次有记忆的状态).
    """
    windows = _build_round_windows(conv_results)
    prompt_by_round: dict[int, dict] = {}
    for p in prompt_logs:
        epoch = _prompt_epoch(p)
        if epoch is None:
            continue
        for rnd, lo, hi in windows:
            if lo <= epoch < hi:
                prompt_by_round[rnd] = p
                break

    entries: list[dict] = []
    prev_block = ""
    for rnd, _, _ in windows:
        p = prompt_by_round.get(rnd)
        if p is None:
            continue
        block = _extract_pinned_block(p.get("system_prompt", ""))
        added, removed = _pinned_line_diff(prev_block, block)
        entries.append({
            "round": rnd,
            "present": bool(block),
            "block": block,
            "added": added,
            "removed": removed,
        })
        prev_block = block
    return entries


def generate_report(
    conv_results: list[dict],
    db_data: dict,
    tool_logs: list[dict],
    prompt_logs: list[dict],
    server_errors: list[dict],
    *,
    use_all: bool,
    agent_id: str,
    session_start: float,
) -> Path:
    """生成 Markdown 报告.

    Args:
        use_all: 是否使用 full 版对话 (影响标题/文件名 mode 标签)
        agent_id: 目标 Agent ID (写入报告元信息 + 文件名)

    """
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    mode = "full" if use_all else "quick"
    report_path = LOGS_DIR / f"conversation_test_report_{ts}_{mode}_{agent_id}.md"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    mode_label = "55轮完整版" if use_all else "22轮精简版"

    # 真实模型来自 prompt 捕获的 metadata.model (旧实现误把 agent_id 当模型写)
    models_used = sorted({
        str(p.get("metadata", {}).get("model", "")).strip()
        for p in prompt_logs
        if p.get("metadata", {}).get("model")
    })
    model_display = (
        ", ".join(models_used) if models_used else f"(未知, agent={agent_id})"
    )

    lines: list[str] = []
    w = lines.append

    w(f"# 对话测试报告 ({mode_label})")
    w("")
    w(f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"- **用户**: {USER_ID} / 线程: {THREAD_ID}")
    w(f"- **Agent**: {agent_id}")
    w(f"- **模型**: {model_display}")
    w(f"- **轮数**: {len(conv_results)}")
    w("")

    # 轮次级富化指标 (LLM 调用 / 延迟 / 错误, 工具序列, token 用量)
    enrich = _enrich_rounds(conv_results, tool_logs, db_data.get("conversations"))

    # ---- 1. 执行摘要 ----
    w("## 1. 对话执行摘要")
    w("")
    w("| 轮次 | 标签 | 状态 | 总s | LLM调用 | LLM_s | 工具 | 入tok | 出tok | 摘要 |")
    w("|------|------|------|-----|---------|-------|------|-------|-------|------|")
    for r in conv_results:
        rnd = r["round"]
        status = r["status_code"]
        status_str = (
            "✅" if (isinstance(status, int) and status == 200) else f"❌ {status}"
        )
        e = enrich.get(int(rnd), {})
        llm_calls = e.get("llm_calls", 0)
        llm_ms = e.get("llm_ms", 0)
        llm_s = f"{llm_ms / 1000:.1f}" if llm_ms else "0"
        tool_col = _truncate(e.get("tool_sequence", "") or "—", 50).replace("|", "\\|")
        in_tok, out_tok = _extract_tokens(r.get("response"))
        resp = r.get("response")
        if isinstance(resp, dict):
            msg = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            msg = str(resp or "")
        summary = _truncate(msg, 45).replace("|", "\\|").replace("\n", " ")
        w(
            f"| R{rnd:02d} | {r['tag']} | {status_str} | {r['elapsed']} | "
            f"{llm_calls} | {llm_s} | {tool_col} | {in_tok} | {out_tok} | {summary} |"
        )
    w("")

    # ---- 用量统计 (精确, 来自 usage.db) ----
    w("## 用量统计 (精确 · 来自 usage.db)")
    w("")
    usage_stats = _collect_usage_stats(session_start)
    if not usage_stats.get("available"):
        w(f"⚠️ {usage_stats.get('reason', '不可用')}")
        w("")
    else:
        u_rows = usage_stats.get("rows", [])
        if not u_rows:
            w("本 session 时间窗内无用量记录.")
            w("")
        else:
            lo_w, hi_w = usage_stats.get("window", ("?", "?"))
            w(f"时间窗: {lo_w} ~ {hi_w} (UTC, 含 reasoning_tokens)")
            w("")

            # 主对话模型 (usage_source=main_chat)
            main_rows = [r for r in u_rows if r.get("usage_source") == "main_chat"]
            w("### 主对话模型 (`usage_source=main_chat`)")
            w("")
            if main_rows:
                w("| 模型 | 调用 | 入tok | 出tok | 推理tok | 总tok |")
                w("|------|------|-------|-------|---------|-------|")
                m_calls = m_in = m_out = m_reason = m_total = 0
                for r in main_rows:
                    c = r.get("calls", 0) or 0
                    ci = r.get("in_tok", 0) or 0
                    co = r.get("out_tok", 0) or 0
                    cr = r.get("reason_tok", 0) or 0
                    ct = r.get("total_tok", 0) or 0
                    m_calls += c
                    m_in += ci
                    m_out += co
                    m_reason += cr
                    m_total += ct
                    w(f"| {r.get('model_id', '?')} | {c} | {ci} | {co} | {cr} | {ct} |")
                w(
                    f"| **小计** | **{m_calls}** | **{m_in}** | **{m_out}** | "
                    f"**{m_reason}** | **{m_total}** |"
                )
            else:
                w("无 main_chat 记录 (主对话模型可能未走 scnet).")
            w("")

            # 子系统用量 (按 usage_source 分组)
            sub: dict[str, list[dict]] = {}
            for r in u_rows:
                src = r.get("usage_source", "?")
                if src != "main_chat":
                    sub.setdefault(src, []).append(r)
            if sub:
                w("### 子系统用量 (按来源)")
                w("")
                w("| 来源 | 调用 | 总tok |")
                w("|------|------|-------|")
                for src in sorted(sub):
                    rs = sub[src]
                    c = sum(x.get("calls", 0) or 0 for x in rs)
                    t = sum(x.get("total_tok", 0) or 0 for x in rs)
                    w(f"| {src} | {c} | {t} |")
                w("")

            # scnet 用量小计 (对应供应商 Credits; 按 model 名匹配, provider 列不可靠)
            scnet_rows = [r for r in u_rows if _is_scnet_model(r.get("model_id"))]
            w("### scnet 用量小计 (对应供应商 Credits)")
            w("")
            if scnet_rows:
                si = sum(r.get("in_tok", 0) or 0 for r in scnet_rows)
                so = sum(r.get("out_tok", 0) or 0 for r in scnet_rows)
                sr = sum(r.get("reason_tok", 0) or 0 for r in scnet_rows)
                st = sum(r.get("total_tok", 0) or 0 for r in scnet_rows)
                w(f"- **入**: {si} tok")
                w(f"- **出**(含推理 {sr} tok): {so} tok")
                w(f"- **总**: {st} tok")
                est_credits = si * 0.01 + so * 0.05
                w(
                    f"- **估算 Credits**: ~{est_credits:.2f} "
                    "(按 入0.01/出0.05 token 单点拟合, 待对照后台增量校准)"
                )
                w("")
                w(
                    "> 标定: 对照 scnet 后台 Credits 增量 Δ. 若 Δ ≈ 估算值, "
                    "单价假设成立; 否则用 Δ 与 token 量解真实单价."
                )
                w("")
            else:
                w("无 scnet 用量记录 (主模型未走 scnet, 或本 session 无 LLM 调用).")
                w("")

    # ---- 2. 慢轮次与异常详情 ----
    # 分两类报告:
    #   - 慢轮次: 耗时超过阈值 (工具密集轮次如 geo/web_research/datapro 属正常耗时)
    #   - 真正异常: HTTP 非200 / 幻觉假附件
    # 不从对话内容扫描关键词 (技术类对话误报率极高).
    w("## 2. 慢轮次与异常详情")
    w("")
    SLOW_THRESHOLD = 300  # 5分钟以内不算慢
    # 附件标记交叉核验 (Ch2 与 Ch8 共用, 避免重复扫描)
    att_result = _check_attachment_markers(
        conv_results, db_data.get("attachment_ids") or set()
    )
    # 轮次标签 -> 假 file_id 列表, 供异常循环按轮查询
    fake_by_label: dict[str, list[str]] = {
        item["label"]: item["fake_ids"] for item in att_result["unregistered"]
    }
    slow_rounds: list[dict] = []
    anomaly_rounds: list[dict] = []
    for r in conv_results:
        real_reasons: list[str] = []
        if isinstance(r.get("status_code"), int) and r["status_code"] != 200:
            real_reasons.append(f"HTTP {r['status_code']}")
        # 幻觉假附件: 响应含 [file: file_id] 标记但 file_id 未在 file_registry
        label = f"R{r['round']:02d}({r.get('tag', '')})"
        if label in fake_by_label:
            fakes = fake_by_label[label]
            real_reasons.append(
                f"{len(fakes)} 个未注册附件标记(疑似幻觉): {', '.join(fakes)}"
            )
        if real_reasons:
            anomaly_rounds.append({"round": r, "reasons": real_reasons})
        try:
            elapsed = float(r.get("elapsed", 0))
        except (ValueError, TypeError):
            elapsed = 0
        if elapsed > SLOW_THRESHOLD:
            slow_rounds.append({"round": r, "elapsed": elapsed})

    has_content = False
    if slow_rounds:
        # 按耗时降序排列
        slow_rounds.sort(key=lambda x: x["elapsed"], reverse=True)
        w(f"### 慢轮次 (>{SLOW_THRESHOLD}s, 共 {len(slow_rounds)} 轮)")
        w("")
        for item in slow_rounds:
            r = item["round"]
            w(f"**R{r['round']:02d} — {r['tag']}** (耗时 {item['elapsed']:.0f}s)")
            w("")
            w(f"用户: {r['user_input']}")
            w("")
        has_content = True

    if anomaly_rounds:
        w(f"### 异常轮次 (共 {len(anomaly_rounds)} 轮)")
        w("")
        for item in anomaly_rounds:
            r = item["round"]
            w(f"**R{r['round']:02d} — {r['tag']}** ({', '.join(item['reasons'])})")
            w("")
            w(f"**用户输入**: {r['user_input']}")
            w("")
            resp = r.get("response")
            if isinstance(resp, dict):
                msg = (
                    resp
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "(空)")
                )
                w("**助手回复**:")
                w("")
                w(f"{msg[:500]}{'...' if len(msg) > 500 else ''}")
            else:
                w("**原始响应**:")
                w("")
                w("```")
                w(str(resp)[:500])
                w("```")
            w("")
        has_content = True

    if not has_content:
        w("✅ 所有轮次响应正常，无慢轮次或异常")
        w("")

    # ---- 3. 数据库内容 ----
    w("## 3. 数据库内容")
    w("")

    w("### 3.1 conversation_history.db")
    w("")
    convs = db_data.get("conversations", [])
    w(f"共 {len(convs)} 条记录")
    w("")
    if convs:
        for c in [convs[0], convs[-1]]:
            rn = c.get("round_number", "?")
            user_msg = str(c.get("user_message", ""))[:100].replace("\n", " ")
            asst_msg = str(c.get("assistant_response", ""))[:100].replace("\n", " ")
            w(f"- **Round {rn}** | 用户: `{user_msg}` | 助手: `{asst_msg}`")
        if len(convs) > 2:
            w(f"- ... (省略 {len(convs) - 2} 条)")
        w("")

    # 索引弧短语分组 (双区设计的老期区: 已冻结的语义 run)
    index_groups = [g for g in db_data.get("index_groups", []) if "error" not in g]
    w("#### 3.1.1 索引弧短语分组 (conversation_index_group)")
    w("")
    w(f"共 {len(index_groups)} 条冻结分组")
    w("")
    if index_groups:
        w("| 轮次范围 | 弧短语 |")
        w("|---------|---------|")
        for g in index_groups:
            rs = g.get("round_start", "?")
            re_ = g.get("round_end", "?")
            rng = f"{rs}-{re_}" if rs != re_ else f"{rs}"
            arc = str(g.get("arc_phrase", "")).replace("|", "\\|").replace("\n", " ")
            w(f"| {rng} | {arc} |")
        w("")
        w(
            "> 老期对话经语义 run 检测闭合后, 由 LLM 蒸馏为弧短语冻结至此表; "
            "近期未冻结的全索引走 prompt 内 <index> bridge 区, 不在此表. "
            "弧短语质量/是否合理冻结交人工判断.",
        )
        w("")
    else:
        w("*(无冻结分组 - 对话可能未触发 run 闭合, 或为旧数据无此表)*")
        w("")

    w("### 3.2 pinned_memory.db")
    w("")
    pinned = db_data.get("pinned_memory", [])
    w(f"共 {len(pinned)} 条记录")
    w("")
    for p in pinned:
        w(f"**更新时间**: {p.get('updated_at', '?')}")
        content = str(p.get("content", p.get("pinned_content", "")))
        w("```")
        w(content[:3000])
        w("```")
        w("")

    # 语义去重提示: full 版含"置顶记忆去重验证"轮次, 重复提交的个人信息应被余弦相似度去重
    if pinned:
        dedup_note = (
            "> **置顶记忆语义去重**: 配置 `dedup_enabled` 时, 重复/相似表述的 add "
            "应按嵌入向量余弦相似度去重. "
        )
        if use_all:
            dedup_note += "full 版含「置顶记忆去重验证」轮次, 若上表出现内容高度重叠的多条记录, 可能表示去重未生效."
        else:
            dedup_note += "若上表出现内容高度重叠的多条记录, 可结合 full 版去重验证轮次进一步排查."
        w(dedup_note)
        w("")

    w("### 3.3 todo.db")
    w("")
    todos = db_data.get("todos", [])
    w(f"共 {len(todos)} 条记录")
    w("")
    if todos:
        w("| ID | 标题 | 状态 | 优先级 | 创建时间 |")
        w("|----|------|------|--------|---------|")
        for t in todos:
            tid = str(t.get("id", "?"))[:12]
            title = str(t.get("title", ""))[:30].replace("|", "\\|")
            status_t = t.get("status", "?")
            prio = t.get("priority", "?")
            created = str(t.get("created_at", "?"))[:19]
            w(f"| {tid} | {title} | {status_t} | {prio} | {created} |")
        w("")
    else:
        w("*(无记录)*")
        w("")

    # ---- 3.4 file_registry.db (用户级, 附件注册表) ----
    w("### 3.4 file_registry.db (用户级, 附件注册表)")
    w("")
    attachments = [a for a in db_data.get("attachments", []) if "error" not in a]
    w(f"共 {len(attachments)} 条记录 (用户级 SSOT, 供 Ch2/Ch8 [file: file_id] 标记反幻觉核验)")
    w("")
    if attachments:
        w("| file_id | 类型 | 文件名 | 轮次 | 概要 |")
        w("|---------|------|--------|------|------|")
        for a in attachments:
            fid = a.get("file_id", "?")
            ftype = a.get("file_type", "?")
            fname = str(a.get("filename", ""))[:30].replace("|", "\\|")
            rn = a.get("round_number", "?")
            rn_str = f"R{rn:02d}" if isinstance(rn, int) else str(rn)
            brief = str(a.get("brief", ""))[:40].replace("|", "\\|").replace("\n", " ")
            w(f"| {fid} | {ftype} | {fname} | {rn_str} | {brief} |")
        w("")
    else:
        w("*(无附件记录)*")
        w("")

    # ---- 3.5 置顶记忆演进 (基于 prompt 捕获, 模型实际所见) ----
    w("### 3.5 置顶记忆演进")
    w("")
    pinned_evo = _pinned_evolution(conv_results, prompt_logs)
    if pinned_evo:
        w(
            "> 每轮 prompt 的 system_prompt 含 <pinned_memory> 块, 逐轮对比反映记忆的"
            "实际变化 (经过去重/精筛后, 即模型实际所见). prompt N↔N+1 的变化由 round N "
            "的对话触发. 内容质量 (是否该记/是否准确) 交由人工判断."
        )
        w("")
        first_present = next((e for e in pinned_evo if e["present"]), None)
        first_round = first_present["round"] if first_present else None
        changed = [e for e in pinned_evo if e["added"] or e["removed"]]
        if not changed and first_round is None:
            w("*(全程无置顶记忆)*")
            w("")
        else:
            w("| 轮次 | +新增 | -移除 |")
            w("|------|-------|-------|")
            for e in pinned_evo:
                if e["round"] == first_round or e["added"] or e["removed"]:
                    w(
                        f"| R{e['round']:02d} | +{len(e['added'])} | -{len(e['removed'])} |"
                    )
            w("")
            if first_present:
                w(f"**首次出现 (R{first_present['round']:02d}):**")
                w("```")
                w(first_present["block"][:1500])
                w("```")
                w("")
            for e in changed:
                w(f"**R{e['round']:02d} 变化:**")
                for ln in e["added"]:
                    w(f"- ➕ {ln[:120]}")
                for ln in e["removed"]:
                    w(f"- ➖ {ln[:120]}")
                w("")
    else:
        w("*(无 prompt 捕获, 无法计算演进)*")
        w("")

    # ---- 4. 向量数据库 ----
    w("## 4. 向量数据库")
    w("")
    vec = db_data.get("vector", {})
    if vec.get("status") == "not_found":
        w("*(未找到 chroma.sqlite3)*")
    else:
        w(f"- **文件大小**: {vec.get('chroma_size_human', '?')}")
        w(f"- **Collections**: {vec.get('collections_count', '?')}")
        w(f"- **Embeddings**: {vec.get('embeddings_count', '?')}")
        if vec.get("error"):
            w(f"- **错误**: {vec['error']}")
    w("")

    # ---- 5. 工具与 LLM 调用日志 ----
    w("## 5. 工具与 LLM 调用日志")
    w("")
    if tool_logs:
        tool_summary: dict[str, list[dict]] = {}
        for ev in tool_logs:
            etype = ev.get("type", "unknown")
            data_inner = ev.get("data", {})
            if etype in ("tool_start", "tool_end", "tool_error"):
                tool_name = data_inner.get("tool_name", "unknown")
                if tool_name not in tool_summary:
                    tool_summary[tool_name] = []
                tool_summary[tool_name].append({"type": etype, **data_inner})

        w("### 工具调用统计")
        w("")
        w("| 工具 | 调用次数 | 成功 | 错误 | 平均耗时 |")
        w("|------|---------|------|------|---------|")
        for tname, events in tool_summary.items():
            starts = [e for e in events if e["type"] == "tool_start"]
            ends = [e for e in events if e["type"] == "tool_end"]
            errors_list = [e for e in events if e["type"] == "tool_error"]
            # tool_end 中 success=false 也计为失败
            failed_ends = [e for e in ends if e.get("success") is False]
            success_count = len(ends) - len(failed_ends)
            error_count = len(errors_list) + len(failed_ends)
            avg_dur = 0
            if ends:
                avg_dur = sum(e.get("duration_ms", 0) for e in ends) // max(
                    len(ends), 1
                )
            w(
                f"| {tname} | {len(starts)} | {success_count} | {error_count} | {avg_dur}ms |"
            )
        w("")

        sm_events = tool_summary.get("search_memories", [])
        if sm_events:
            sm_starts = [e for e in sm_events if e["type"] == "tool_start"]
            w("### search_memories 调用详情")
            w("")
            w(f"共 {len(sm_starts)} 次记忆搜索调用")
            w("")
            for e in sm_starts:
                preview = str(e.get("input_preview", ""))[:100]
                w(f"- `{preview}`")
            w("")

        # 硬错误 (抛异常) 与软失败 (业务 success=false) 分开展示
        hard_errors = [ev for ev in tool_logs if ev.get("type") == "tool_error"]
        soft_fails = [
            ev
            for ev in tool_logs
            if ev.get("type") == "tool_end"
            and ev.get("data", {}).get("success") is False
        ]

        if hard_errors:
            w(f"### 工具错误事件 ({len(hard_errors)} 条)")
            w("")
            w("```json")
            for ev in hard_errors:
                w(json.dumps(ev, ensure_ascii=False, default=str))
            w("```")
            w("")
        else:
            w("**工具错误**: 未发现 tool_error 事件 ✅")
            w("")

        if soft_fails:
            # input_preview 只在 tool_start 事件里, 用 run_id 关联回去
            start_inputs = {
                ev.get("data", {}).get("run_id"): ev.get("data", {}).get("input_preview", "")
                for ev in tool_logs
                if ev.get("type") == "tool_start"
            }
            soft_windows = _build_round_windows(conv_results)
            w(f"### 工具软失败事件 ({len(soft_fails)} 条)")
            w("")
            w(
                "> 工具正常返回但业务 `success=false` (常为模型传参不当, "
                "如把工具组名当 skill 名). 未抛异常, 不计入 server ERROR."
            )
            w("")
            w("| 工具 | 轮次 | 入参 | 失败原因 |")
            w("|------|------|------|---------|")
            for ev in soft_fails:
                d = ev.get("data", {})
                tname = d.get("tool_name", "unknown")
                epoch = _event_epoch(ev)
                rnd = _match_round(epoch, soft_windows) if epoch else None
                rnd_col = f"R{rnd}" if rnd else "—"
                input_col = (
                    _truncate(str(start_inputs.get(d.get("run_id"), "")), 80).replace(
                        "|", "\\|"
                    )
                    or "(无)"
                )
                reason = _extract_soft_fail_reason(d.get("output_preview", ""))
                reason_col = reason.replace("|", "\\|") if reason else "(无详情)"
                w(f"| {tname} | {rnd_col} | {input_col} | {reason_col} |")
            w("")

        # ---- LLM 调用概览 (与工具事件同源 ToolCallTracker) ----
        llm_events = [
            ev for ev in tool_logs if str(ev.get("type", "")).startswith("llm_")
        ]
        llm_ends = [ev for ev in llm_events if ev.get("type") == "llm_end"]
        llm_errs = [ev for ev in llm_events if ev.get("type") == "llm_error"]
        total_llm_ms = sum(
            int(e.get("data", {}).get("duration_ms", 0) or 0) for e in llm_ends
        )
        w("### LLM 调用概览")
        w("")
        w(
            f"共 {len(llm_ends)} 次成功调用, 累计 {total_llm_ms / 1000:.1f}s "
            f"(主对话 + 专家工具子 Agent)"
        )
        w("")
        if llm_errs:
            w(f"### LLM 错误事件 ({len(llm_errs)} 条)")
            w("")
            w("```json")
            for ev in llm_errs:
                w(json.dumps(ev, ensure_ascii=False, default=str))
            w("```")
            w("")
        else:
            w("**LLM 错误**: 未发现 llm_error 事件 ✅")
            w("")
    else:
        w("*(未找到工具调用日志)*")
        w("")

    # ---- 6. Prompt结构摘要 (messages 数组优先, 字符串扫描回退) ----
    w("## 6. Prompt结构摘要")
    w("")
    if prompt_logs:
        has_history_messages = any(p.get("history_messages") for p in prompt_logs)
        path_label = "messages 数组" if has_history_messages else "字符串拼接 (旧格式)"
        w(f"共 {len(prompt_logs)} 个prompt文件 (路径: {path_label})")
        w("")

        w("| # | 时间 | 对话历史 | 轮次 | 索引区 | TODO | UR | 总长 |")
        w("|---|------|---------|------|--------|------|----|------|")

        overflow_start = None
        req_first_round: int | None = None
        req_block_sample = ""
        for idx, p in enumerate(prompt_logs, 1):
            content = p.get("user_content", "")
            ts_p = p.get("timestamp", "?")[:19]
            history_msgs = p.get("history_messages", [])

            if history_msgs:
                # messages 数组路径: 从 history_messages 统计
                round_info, index_info, _ = _analyze_history_messages(history_msgs)
                conv_icon = "✅"
                if index_info and overflow_start is None:
                    overflow_start = idx
            else:
                # 回退: 老格式日志, 从 user_content 字符串扫描
                conv_section = _extract_section(content, "conversation_history")
                if conv_section:
                    round_count, r_min, r_max = _count_rounds(conv_section)
                    round_info = (
                        f"{round_count}轮 [{r_min}-{r_max}]"
                        if round_count > 0
                        else "0轮"
                    )
                else:
                    round_info = "❌ 缺失"

                # 首轮无 conversation_history 是正常的
                if idx == 1 and not conv_section:
                    conv_icon = "➖"
                    round_info = "首轮"
                else:
                    conv_icon = "✅" if conv_section else "❌"

                index_section = (
                    _extract_section(conv_section or "", "index")
                    if conv_section
                    else ""
                )
                if index_section:
                    idx_count, idx_min, idx_max = _count_index_rounds(index_section)
                    index_info = f"✅ {idx_count}轮 [{idx_min}-{idx_max}]"
                    if overflow_start is None:
                        overflow_start = idx
                else:
                    index_info = ""

            # TODO 检测两种路径都从 user_content 扫描 (messages 数组路径里 TODO 仍在当前轮)
            todo_section = _extract_section(
                content, "current_todos"
            ) or _extract_section(content, "todo_list")
            if todo_section:
                todo_count = _count_todo_items(todo_section)
                todo_info = f"✅ {todo_count}项"
            else:
                todo_info = ""

            # 总长: messages 数组路径下加上 history_total_length 更准确
            capture_info = p.get("capture_info", {}) or {}
            total_len = len(content) + int(capture_info.get("history_total_length", 0))

            # user_requirements 标签 (system_prompt 内, requirement_memory 维护)
            sp = p.get("system_prompt", "") or ""
            req_block = _extract_requirements_block(sp)
            ur_icon = "✅" if req_block else "—"
            if req_block and req_first_round is None:
                req_first_round = idx
                req_block_sample = req_block

            w(
                f"| {idx} | {ts_p} | {conv_icon} | {round_info} | {index_info} | {todo_info} | {ur_icon} | {total_len} |",
            )
        w("")

        if req_first_round is not None:
            w("### user_requirements 注入")
            w("")
            w(f"**首现**: Prompt #{req_first_round}")
            w("")
            w("```")
            w(req_block_sample[:1500])
            w("```")
            w("")
            w(
                "> 标签是否存在为机械事实; 内容是否应记/记对交人工判断 "
                "(requirement_memory 工具维护, 与 <pinned_memory> 同在 system_prompt).",
            )
            w("")

        if overflow_start is not None:
            w("### 记忆溢出检测")
            w("")
            w(f"**溢出起始**: Prompt #{overflow_start}")
            w("")
            w(
                f"从第 {overflow_start} 个prompt开始出现索引区, 早期对话被压缩为索引摘要."
            )
            w("")
        else:
            w("### 记忆溢出检测")
            w("")
            w(
                "**未检测到溢出** - 对话未触发记忆溢出 "
                "(属正常情况, 工具调用密集轮次可能产生较长响应)."
            )
            w("")

        anomalies: list[str] = []
        for idx, p in enumerate(prompt_logs[1:], 2):
            content = p.get("user_content", "")
            ts_p = p.get("timestamp", "?")[:19]
            history_msgs = p.get("history_messages", [])

            ctx_section = _extract_section(content, "current_context")
            input_section = _extract_section(content, "user_input")

            # 历史缺失判定: 既无字符串 conversation_history 也无 history_messages 数组
            has_history = bool(
                _extract_section(content, "conversation_history") or history_msgs,
            )
            if not has_history:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 conversation_history")
            if not ctx_section:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 current_context")
            if not input_section:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 user_input")

        if anomalies:
            w("### 异常提示")
            w("")
            for a in anomalies:
                w(f"- ⚠️ {a}")
            w("")
        else:
            w("**异常检测**: 未发现结构异常 ✅")
            w("")
    else:
        w("*(未找到prompt日志)*")
        w("")

    # ---- 7. 错误与警告 ----
    w("## 7. 错误与警告")
    w("")
    if server_errors:
        # 构建轮次时间窗, 用于关联错误到轮次并过滤无关日志
        windows = _build_round_windows(conv_results)
        first_lo = windows[0][1] if windows else None
        # 每轮允许额外余量 (工具超时等可能略超出轮次窗口)
        round_margin = 5.0

        matched: list[tuple[int | None, dict]] = []
        filtered_count = 0
        for ev in server_errors:
            epoch = _server_error_epoch(ev)
            if epoch is None:
                matched.append((None, ev))
                continue
            # 过滤: 首轮开始前超过余量的无关事件
            if first_lo is not None and epoch < first_lo - round_margin:
                filtered_count += 1
                continue
            rnd = _match_round(epoch, windows)
            matched.append((rnd, ev))

        if not matched:
            w("*(未发现ERROR/Traceback日志)*")
            w("")
        else:
            if filtered_count:
                w(
                    f"共 {len(matched)} 个错误事件 ({filtered_count} 个时间线外事件已过滤)"
                )
            else:
                w(f"共 {len(matched)} 个错误事件")
            w("")
            w("```")
            for rnd, ev in matched[:50]:
                prefix = f"[R{rnd:02d}]" if rnd is not None else "[--]"
                w(
                    f"{prefix} {ev['headline'].split('] ', 1)[-1] if '] ' in ev['headline'] else ev['headline']}"
                )
                # 只展示前 10 行 traceback, 超出折叠
                event_lines = ev.get("lines", [])
                if len(event_lines) <= 10:
                    for line in event_lines[1:]:
                        w(line)
                else:
                    for line in event_lines[1:10]:
                        w(line)
                    w(f"... ({len(event_lines) - 10} 行 traceback 续行已折叠)")
            w("```")
            w("")
    else:
        w("*(未发现ERROR/Traceback日志)*")
        w("")

    # ---- 8. 综合评估 ----
    w("## 8. 综合评估")
    w("")

    checks: list[tuple[str, str]] = []

    failed_rounds = [
        r
        for r in conv_results
        if isinstance(r.get("status_code"), int) and r["status_code"] != 200
    ]
    total = len(conv_results)
    passed = total - len(failed_rounds)
    if not failed_rounds:
        checks.append((f"✅ {passed}/{total} 成功", ""))
    else:
        failed_ids = ", ".join(f"R{r['round']:02d}" for r in failed_rounds)
        checks.append((f"❌ {passed}/{total} 成功", f"失败轮次: {failed_ids}"))

    conv_count = len(db_data.get("conversations", []))
    if conv_count == total:
        checks.append((f"✅ {conv_count} 条记录, 完整", ""))
    else:
        checks.append((
            f"⚠️ {conv_count} 条记录",
            f"预期 {total} 条, 差异 {total - conv_count}",
        ))

    pinned = db_data.get("pinned_memory", [])
    if pinned:
        # 只报告事实计数. 内容质量 (是否含姓名/城市/技术栈等) 不做关键词命中判断
        # (Go/数据库/喜欢 等关键词太泛, 误判率高), 由 Ch3 原始 dump + Ch3.x
        # 置顶记忆演进表交由人工判断
        checks.append((f"✅ {len(pinned)} 条", "见 Ch3 置顶记忆原始内容与演进"))
    else:
        checks.append(("❌ 无记录", ""))

    todos = db_data.get("todos", [])
    if not todos:
        checks.append(("⚠️ 无TODO记录", ""))
    else:
        statuses = {t.get("status", "") for t in todos}
        issues: list[str] = []
        deleted = [t for t in todos if t.get("status") == "DELETED"]
        if deleted:
            issues.append(f"{len(deleted)} 条 DELETED 残留")
        if issues:
            checks.append((f"⚠️ {len(todos)} 条 TODO", "; ".join(issues)))
        else:
            checks.append((f"✅ {len(todos)} 条 TODO", f"状态: {','.join(statuses)}"))

    # 工具失败: 硬错误 (抛异常) 与软失败 (业务 success=false) 分别呈现,
    # 避免把模型传参导致的软失败误读为工具故障 (如 load_skill 被传入不存在的技能名)
    eval_hard: list[str] = []
    eval_soft: list[str] = []
    for ev in tool_logs:
        t = ev.get("type")
        if t == "tool_error":
            eval_hard.append(ev.get("data", {}).get("tool_name", "unknown"))
        elif t == "tool_end" and ev.get("data", {}).get("success") is False:
            eval_soft.append(ev.get("data", {}).get("tool_name", "unknown"))
    total_fail = len(eval_hard) + len(eval_soft)
    if total_fail == 0:
        checks.append(("✅ 无 tool_error", ""))
    else:
        parts = []
        if eval_hard:
            parts.append(f"{len(eval_hard)} 硬错误({','.join(sorted(set(eval_hard)))})")
        if eval_soft:
            parts.append(f"{len(eval_soft)} 软失败({','.join(sorted(set(eval_soft)))})")
        checks.append((f"❌ {total_fail} 个错误", " + ".join(parts)))

    if prompt_logs:
        # 双轨: 既无字符串 section 也无 history_messages 数组才算缺失
        missing_conv = sum(
            1
            for p in prompt_logs[1:]
            if not _extract_section(p.get("user_content", ""), "conversation_history")
            and not p.get("history_messages")
        )
        if missing_conv == 0:
            checks.append(("✅ Prompt结构正常", ""))
        else:
            checks.append((f"⚠️ {missing_conv} 个prompt缺少conversation_history", ""))
    else:
        checks.append(("⚠️ 无prompt日志", ""))

    if not server_errors:
        checks.append(("✅ 无 ERROR/Traceback", ""))
    else:
        event_count = len(server_errors)
        total_lines = sum(len(e.get("lines", [])) for e in server_errors)
        checks.append((f"❌ {event_count} 个错误事件 ({total_lines} 行)", ""))

    # 附件链接校验: 文件产出轮响应应含可下载附件链接, 且 [file: file_id] 标记
    # 须与 file_registry 交叉核验. att_result 由 Ch2 提前计算, 此处复用.
    att_unreg = att_result["unregistered"]
    att_ok = att_result["ok"]
    att_missing = att_result["missing"]
    if att_unreg:
        detail = "; ".join(
            f"{it['label']}: {', '.join(it['fake_ids'])}" for it in att_unreg
        )
        checks.append((f"❌ {len(att_unreg)} 个未注册附件标记(疑似幻觉)", detail))
    elif not att_ok and not att_missing:
        checks.append(("➖ 无文件产出轮次", ""))
    elif not att_missing:
        checks.append((f"✅ {len(att_ok)} 轮已注入附件链接", ""))
    else:
        detail = f"缺链接: {', '.join(att_missing)}"
        checks.append((f"⚠️ {len(att_missing)} 轮缺附件链接", detail))

    w("| # | 检查项 | 结果 | 详情 |")
    w("|---|--------|------|------|")
    labels = [
        "对话响应",
        "数据库记录",
        "置顶记忆",
        "TODO生命周期",
        "工具调用",
        "Prompt结构",
        "服务日志",
        "附件链接",
    ]
    for i, (label, (result, detail)) in enumerate(zip(labels, checks), 1):
        detail_col = detail.replace("|", "\\|") if detail else ""
        w(f"| {i} | {label} | {result} | {detail_col} |")
    w("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Personal Assistant 对话测试脚本 (合并版: 默认21轮 quick, --all 切到54轮 full)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="使用54轮完整版对话 (默认21轮精简版)",
    )
    parser.add_argument(
        "--agent",
        default="personal-assistant",
        metavar="ID",
        help="目标 Agent ID (默认 personal-assistant, 可选 health-assistant 等)",
    )
    parser.add_argument(
        "--start-round",
        type=int,
        default=1,
        metavar="N",
        help="从第N轮开始执行 (默认从第1轮开始)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        metavar="N",
        help="最多执行N轮 (0=不限, 用于小规模校准如 --max-rounds 3)",
    )
    parser.add_argument(
        "--server-log",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="额外扫描的服务日志路径 (可指定多次, 如 /tmp/dev_server.log 或 /tmp/)",
    )
    args = parser.parse_args()

    # 按参数设置全局配置
    global MODEL, AGENT_ID, DATA_DIR, CONVERSATIONS
    MODEL = args.agent
    AGENT_ID = args.agent
    DATA_DIR = Path(f"data/{USER_ID}/{THREAD_ID}/{AGENT_ID}")
    CONVERSATIONS = CONVERSATIONS_FULL if args.all else CONVERSATIONS_QUICK

    mode_label = "54轮完整版" if args.all else "21轮精简版"
    print(_cyan("=" * 60))
    print(_cyan(f"  Personal Assistant 对话测试脚本 ({mode_label})"))
    print(_cyan(f"  Agent: {AGENT_ID}  对话轮数: {len(CONVERSATIONS)}"))
    print(_cyan("=" * 60))

    if not check_server():
        sys.exit(1)

    session_start = time.time()
    # naive local, 与服务日志行首时间戳同时区, 用于按行隔离本轮错误
    session_start_dt = datetime.now()

    conv_results = run_conversations(
        start_round=args.start_round, max_rounds=args.max_rounds
    )

    print(f"\n{_cyan('[采集]')} 正在采集数据库内容...")
    db_data = collect_db_data()
    print(f"  - 对话记录: {len(db_data.get('conversations', []))} 条")
    print(f"  - 置顶记忆: {len(db_data.get('pinned_memory', []))} 条")
    print(f"  - TODO: {len(db_data.get('todos', []))} 条")
    vec = db_data.get("vector", {})
    print(f"  - 向量: {vec.get('chroma_size_human', '未找到')}")

    print(f"\n{_cyan('[采集]')} 正在解析工具调用日志...")
    tool_logs = collect_tool_call_logs(session_start)
    tool_names = set()
    for ev in tool_logs:
        if ev.get("type") in ("tool_start", "tool_end", "tool_error"):
            tool_names.add(ev.get("data", {}).get("tool_name", ""))
    print(f"  - 事件数: {len(tool_logs)}")
    print(f"  - 涉及工具: {', '.join(sorted(tool_names)) if tool_names else '无'}")

    print(f"\n{_cyan('[采集]')} 正在解析Prompt日志...")
    prompt_logs = collect_prompt_logs(session_start)
    print(f"  - Prompt文件: {len(prompt_logs)} 个")

    print(f"\n{_cyan('[采集]')} 正在扫描服务日志错误...")
    server_errors = collect_server_logs(
        session_start_dt, session_start, args.server_log
    )
    if server_errors:
        event_count = len(server_errors)
        total_lines = sum(len(e.get("lines", [])) for e in server_errors)
        print(f"  - ERROR/Traceback: {event_count} 个错误事件 ({total_lines} 行)")
    else:
        print("  - ERROR/Traceback: 0 个错误事件")

    print(f"\n{_cyan('[报告]')} 正在生成报告...")
    report_path = generate_report(
        conv_results,
        db_data,
        tool_logs,
        prompt_logs,
        server_errors,
        use_all=args.all,
        agent_id=AGENT_ID,
        session_start=session_start,
    )
    print(f"{_green('[完成]')} 报告已保存: {report_path}")
    print(f"\n{SEPARATOR}")


if __name__ == "__main__":
    main()
