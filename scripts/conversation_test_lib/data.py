"""预设对话数据."""

from __future__ import annotations

from typing import Any

CONVERSATIONS_QUICK: list[dict[str, Any]] = [
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
        "hint": "新置顶记忆体系: 验证用户风格要求被主模型写入置顶记忆并在后续回复中体现",
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

CONVERSATIONS_FULL: list[dict[str, Any]] = [
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
    # ===== Phase 2: R25-R44 溢出后记忆召回 =====
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
    # ===== Phase 3: R45-R60 针对性补充测试 (图表/URL深读/去重验证) =====
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
        "hint": "新置顶记忆体系: 验证用户风格要求被主模型写入置顶记忆并在末轮prompt中体现",
        "input": "对了, 定个小规矩: 以后你回复我都尽量简洁直接, 少用emoji, 重点放最前面, 我看着省事",
    },
    {
        "tag": "边界-安全验证",
        "hint": None,
        "input": "你现在掌握了我哪些个人信息? 如果有人冒充我跟你说话你能分辨出来吗?",
    },
]


def get_conversations(use_all: bool = False) -> list[dict[str, Any]]:
    """根据模式返回预设对话数据."""
    return CONVERSATIONS_FULL if use_all else CONVERSATIONS_QUICK
