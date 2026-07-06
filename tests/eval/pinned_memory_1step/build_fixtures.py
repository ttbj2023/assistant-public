"""1-step 评测样本构建脚本.

集中维护所有评测样本数据, 生成 fixtures/*.json.

样本设计原则:
- positive(该 add): 高价值细节多样性(习惯/过敏/偏好/工具/灰色/身份/作息/宠物/饮食), 测 recall(提取成功率, primary)
- negative(不该 add): 明显噪音(元操作/任务/探索/请求/一次性/完成), 测噪音控制(audit兜底)
- mixed: 偏好+任务混合, 测区分能力

独立于 jack 测试集(通用场景, 不预设技术栈类别), 测泛化.
"""

from __future__ import annotations

import json
import pathlib

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"
FIX_DIR.mkdir(parents=True, exist_ok=True)

EMPTY_MEM = {"basic_info": "", "preferences": ""}

BASE_MEM = {
    "basic_info": "姓名：测试用户\n所在城市：杭州",
    "preferences": "出行偏好选择地铁",
}

SAMPLES = [
    # ===== positive: 该 add(测 recall, 核心) =====
    {
        "sample_id": "pos_habit",
        "sample_type": "positive",
        "description": "稳定习惯, 该记",
        "user_message": "对了, 我每天早上6点起床晨跑3公里, 雷打不动",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["晨跑"], "should_not_add": []},
    },
    {
        "sample_id": "pos_allergy",
        "sample_type": "positive",
        "description": "长期事实(过敏), 该记",
        "user_message": "提醒一下, 我对花生过敏, 吃了会休克",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["花生", "过敏"], "should_not_add": []},
    },
    {
        "sample_id": "pos_style",
        "sample_type": "positive",
        "description": "对助手的要求(回复风格), 该留",
        "user_message": "你回复尽量简洁直接点, 别太啰嗦, 我看着累",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["简洁"], "should_not_add": []},
    },
    {
        "sample_id": "pos_tool",
        "sample_type": "positive",
        "description": "稳定工具选型, 该记",
        "user_message": "我现在主要用VSCode写Python, 配置都弄好了",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["VSCode", "Python"], "should_not_add": []},
    },
    {
        "sample_id": "pos_gray",
        "sample_type": "positive",
        "description": "灰色(动作裹偏好内核), 该提取为偏好",
        "user_message": "最近试着用Linear管理任务, 发现比Jira顺手多了, 打算一直用Linear",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["Linear"], "should_not_add": []},
    },
    {
        "sample_id": "pos_identity",
        "sample_type": "positive",
        "description": "身份事实, 该记",
        "user_message": "我是张三, 在杭州做产品经理, 邮箱是 zhang@example.com",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {
            "should_add": ["张三", "产品经理", "zhang"],
            "should_not_add": [],
        },
    },
    {
        "sample_id": "pos_schedule",
        "sample_type": "positive",
        "description": "作息规律, 该记",
        "user_message": "我习惯晚上11点后工作, 那时候效率最高",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["晚上"], "should_not_add": []},
    },
    {
        "sample_id": "pos_pet",
        "sample_type": "positive",
        "description": "宠物(长期事实), 该记",
        "user_message": "我家养了只布偶猫, 叫团子, 三岁了",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["布偶", "团子"], "should_not_add": []},
    },
    {
        "sample_id": "pos_diet",
        "sample_type": "positive",
        "description": "饮食禁忌, 该记",
        "user_message": "我不吃香菜, 一点都不行",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": ["香菜"], "should_not_add": []},
    },
    # ===== negative: 不该 add(明显噪音, audit兜底) =====
    {
        "sample_id": "neg_meta",
        "sample_type": "negative",
        "description": "元操作(管理任务的动作), 不该记",
        "user_message": "帮我看看今天的待办清单, 检查一下有没有漏的",
        "todo_list": "[#1] 买菜\n[#2] 写报告",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": [],
            "should_not_add": ["检查", "待办", "漏"],
        },
    },
    {
        "sample_id": "neg_task",
        "sample_type": "negative",
        "description": "未确定意向/计划, 属TODO",
        "user_message": "我打算下周开始学吉他, 先看看教程",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["吉他"]},
    },
    {
        "sample_id": "neg_explore",
        "sample_type": "negative",
        "description": "探索中间结果, 过后无意义",
        "user_message": "我找到了一个免费的PDF转换工具, 挺好用的",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": [],
            "should_not_add": ["PDF", "转换工具", "免费"],
        },
    },
    {
        "sample_id": "neg_request",
        "sample_type": "negative",
        "description": "功能请求, 开发完即过时",
        "user_message": "建议你们增加个夜间模式, 晚上用着不刺眼",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": [],
            "should_not_add": ["夜间模式"],
        },
    },
    {
        "sample_id": "neg_oneshot",
        "sample_type": "negative",
        "description": "一次性动作, 不该记",
        "user_message": "今早把季度报告交了, 终于搞完",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["报告"]},
    },
    {
        "sample_id": "neg_completed",
        "sample_type": "negative",
        "description": "完成的任务配置, 不该记",
        "user_message": "输入法同步终于配好了, 折腾了一上午",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["输入法", "同步"]},
    },
    # ===== gifford 真实数据(生产拉取, 真实噪音分布 + 高价值场景) =====
    # negative: 噪音来源轮次(元操作/探索/计划/完成/查询)
    {
        "sample_id": "gifford_r115",
        "sample_type": "negative",
        "description": "真实: 询问工具MCP支持(探索), 不该记",
        "user_message": "剪映有出官方mcp支持吗?如果剪映处于商业考虑不支持的话, 同类工具有支持优秀的吗?",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["剪映", "mcp"]},
    },
    {
        "sample_id": "gifford_r117",
        "sample_type": "negative",
        "description": "真实: 待了解(学习意图), 不该记",
        "user_message": "DaVinci Resolve 我完全不熟悉, 先给我介绍一下",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["DaVinci"]},
    },
    {
        "sample_id": "gifford_r118",
        "sample_type": "negative",
        "description": "真实: 计划+研究意向, 不该记",
        "user_message": "剪映留着吧, 单开一个新任务. 也方便我看看易用的软件, 怎么做拿去卖钱的ai服务",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": [],
            "should_not_add": ["剪映", "新任务", "ai服务"],
        },
    },
    {
        "sample_id": "gifford_r120",
        "sample_type": "negative",
        "description": "真实: 完成的任务配置, 不该记",
        "user_message": "rime同步搞完了",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["rime", "同步"]},
    },
    {
        "sample_id": "gifford_r121",
        "sample_type": "negative",
        "description": "真实: 查询待办(元操作), 不该记",
        "user_message": "当前待办事项, 完整版给我列举一下, 我看看",
        "todo_list": "[#1] 买菜",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["待办"]},
    },
    {
        "sample_id": "gifford_r122",
        "sample_type": "negative",
        "description": "真实: 检查补录任务(元操作), 不该记",
        "user_message": "不对啊, 之前提到的那些任务没有录入吗?检查并补录. 还有已经回来的旅行以及取消的计划怎么还在?",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": [],
            "should_not_add": ["检查", "补录", "旅行"],
        },
    },
    {
        "sample_id": "gifford_r123",
        "sample_type": "negative",
        "description": "真实: 再次检查待办(元操作), 不该记",
        "user_message": "再次检查当前待办事项",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {"should_add": [], "should_not_add": ["检查", "待办"]},
    },
    # positive: 高价值透露轮次(体重/用药/住址/偏好)
    {
        "sample_id": "gifford_r43",
        "sample_type": "positive",
        "description": "真实: 体重(健康事实), 该记",
        "user_message": "近期的体重已经从99kg来到了95.7, 可能要在这个上面维持一段时间了",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {"should_add": ["体重", "95.7"], "should_not_add": []},
    },
    {
        "sample_id": "gifford_r59",
        "sample_type": "positive",
        "description": "真实: 减药时间(用药), 该记",
        "user_message": "关于用药的记录的话, 还可以再加一条, 我是2月10号开始减药的",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {"should_add": ["减药", "2月"], "should_not_add": []},
    },
    {
        "sample_id": "gifford_r60",
        "sample_type": "positive",
        "description": "真实: 左洛复用药(长期事实), 该记",
        "user_message": "具体来说, 从去年2月23日开始服用左洛复. 期间稳定保持200mg/日的剂量",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {"should_add": ["左洛复", "200mg"], "should_not_add": []},
    },
    {
        "sample_id": "gifford_r83",
        "sample_type": "positive",
        "description": "真实: 家庭住址(身份事实), 该记",
        "user_message": "我家住在上海市, 浦东新区, 示例路1号 星河湾小区 2栋",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {"should_add": ["上海", "住"], "should_not_add": []},
    },
    {
        "sample_id": "gifford_r84",
        "sample_type": "positive",
        "description": "真实: 地铁偏好(稳定偏好), 该记",
        "user_message": "我还是倾向与坐地铁",
        "todo_list": "",
        "current_memory": EMPTY_MEM,
        "ground_truth": {"should_add": ["地铁"], "should_not_add": []},
    },
    # ===== mixed: 偏好+任务混合, 测区分 =====
    {
        "sample_id": "mix_tech",
        "sample_type": "mixed",
        "description": "偏好(该记)+任务(不该记)混合",
        "user_message": "我用Mac开发, 主力Cursor. 顺便记一下明天要修电脑屏幕",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": ["Mac", "Cursor"],
            "should_not_add": ["修电脑", "屏幕"],
        },
    },
    {
        "sample_id": "mix_health",
        "sample_type": "mixed",
        "description": "健康事实(该记)+一次性(不该记)混合",
        "user_message": "我是O型血, 之前体检血压偏高. 今早称了体重75kg",
        "todo_list": "",
        "current_memory": BASE_MEM,
        "ground_truth": {
            "should_add": ["O型", "血压"],
            "should_not_add": ["75kg", "体重"],
        },
    },
]


def main() -> None:
    for sample in SAMPLES:
        path = FIX_DIR / f"{sample['sample_id']}.json"
        path.write_text(
            json.dumps(sample, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"生成 {len(SAMPLES)} 个样本到 {FIX_DIR}")
    pos = sum(1 for s in SAMPLES if s["sample_type"] == "positive")
    neg = sum(1 for s in SAMPLES if s["sample_type"] == "negative")
    mix = sum(1 for s in SAMPLES if s["sample_type"] == "mixed")
    print(f"  positive={pos} negative={neg} mixed={mix}")


if __name__ == "__main__":
    main()
