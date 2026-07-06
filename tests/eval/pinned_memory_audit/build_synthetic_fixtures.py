"""合成评测样本生成 - 二次增强评测集.

构造覆盖各判断维度、有难度的合成样本, 测试 prompt 泛化能力(而非过拟合到几个真实样本).
每个样本聚焦 1-2 个判断维度, 含"动作形式的稳定属性"等难点.

维度覆盖:
- 正样本(该删): 元操作/探索意图/过时事件/系统诉求
- 负样本(该留): 医疗事实(动作形式但持久)/技术偏好/财务
- 灰色(该提炼): 状态包装偏好/冗长偏好
- 难度: 混合条目/临时事实/长期事件(身份标记)
"""

from __future__ import annotations

import json
import pathlib

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"


def _build(sample_id, sample_type, desc, basic_items, pref_items):
    """从带标注的行列表构建 fixture. action ∈ keep/delete/change."""
    pinned = {
        "basic_info": "\n".join(
            it["content"] for it in basic_items if it["content"].strip()
        ),
        "preferences": "\n".join(
            it["content"] for it in pref_items if it["content"].strip()
        ),
    }
    gt_delete = [
        it["content"] for it in basic_items + pref_items if it["action"] == "delete"
    ]
    gt_change = [
        {"old_content": it["content"], "reason": it.get("reason", "")}
        for it in basic_items + pref_items
        if it["action"] == "change"
    ]
    return {
        "sample_id": sample_id,
        "sample_type": sample_type,
        "description": desc,
        "pinned_memory": pinned,
        "conversation_index": [],
        "ground_truth": {"delete": gt_delete, "change": gt_change, "merge_groups": []},
    }


SAMPLES = [
    _build(
        "syn_meta_ops",
        "positive",
        "元操作日志(多表述变体), 测召回",
        [
            {"action": "keep", "content": "姓名：李四"},
            {"action": "delete", "content": "2026年6月20日整理了置顶记忆"},
            {"action": "keep", "content": "职业：产品经理"},
            {"action": "delete", "content": "刚才检查并更新了任务清单"},
        ],
        [
            {"action": "keep", "content": "偏好用Notion做笔记"},
            {"action": "delete", "content": "昨天补录了遗漏的信息"},
        ],
    ),
    _build(
        "syn_medical",
        "negative",
        "医疗事实(动作形式但持久状态), 难点: 不误删",
        [
            {"action": "keep", "content": "2024年确诊高血压，每日服用缬沙坦80mg"},
            {"action": "keep", "content": "对青霉素过敏"},
            {"action": "delete", "content": "上周去医院复查，血压130/85"},
            {"action": "keep", "content": "家族有糖尿病史"},
        ],
        [
            {"action": "keep", "content": "饮食习惯偏清淡"},
        ],
    ),
    _build(
        "syn_exploration",
        "positive",
        "探索意图(未确定), 测召回; 穿插已采纳偏好作对照",
        [
            {"action": "keep", "content": "姓名：王五"},
        ],
        [
            {"action": "keep", "content": "已采用Python作为主力语言"},
            {"action": "delete", "content": "考虑学习Rust语言"},
            {"action": "delete", "content": "打算明年搬家到杭州"},
            {"action": "delete", "content": "在评估是否换工作"},
            {"action": "keep", "content": "偏好远程工作"},
        ],
    ),
    _build(
        "syn_expired",
        "positive",
        "过时/一次性事件, 测召回",
        [
            {"action": "keep", "content": "居住地：上海"},
            {"action": "delete", "content": "上个月完成了项目X的验收"},
            {"action": "delete", "content": "去年的体检报告已归档"},
        ],
        [
            {"action": "keep", "content": "喜欢打网球"},
            {"action": "delete", "content": "上周采购了新服务器"},
        ],
    ),
    _build(
        "syn_demands",
        "positive",
        "功能请求(一次性) vs 对助手的要求(留)",
        [
            {"action": "keep", "content": "姓名：赵六"},
        ],
        [
            {"action": "keep", "content": "希望助手回复更简洁"},
            {"action": "delete", "content": "建议增加PDF导出功能"},
            {"action": "keep", "content": "偏好简洁的沟通风格"},
        ],
    ),
    _build(
        "syn_state_wrapped",
        "change",
        "状态包装的偏好(内核是偏好, 表述是过程), 测提炼",
        [
            {"action": "keep", "content": "职业：全栈工程师"},
        ],
        [
            {
                "action": "change",
                "content": "最近在试用Notion，感觉比OneNote好，打算长期用Notion",
                "reason": "剥离试用/打算过程, 提炼为已采纳偏好",
            },
            {
                "action": "change",
                "content": "关注某Web框架的生态，若成熟则采用",
                "reason": "剥离关注/条件, 提炼为倾向",
            },
            {"action": "keep", "content": "已确定使用Git作为版本控制"},
        ],
    ),
    _build(
        "syn_mixed",
        "change",
        "混合条目(事件+状态+建议), 难点: 部分提炼部分删",
        [
            {"action": "keep", "content": "毕业于浙江大学计算机系"},
            {
                "action": "change",
                "content": "上周体检，血压130/85，医生建议控制饮食",
                "reason": "剥离上周体检动作, 提炼血压状态+饮食要求",
            },
            {"action": "delete", "content": "昨天加班到很晚"},
        ],
        [
            {"action": "keep", "content": "偏好异步沟通"},
        ],
    ),
    _build(
        "syn_temporary",
        "negative",
        "工作状态(团队/项目归属), 争议项保留不评",
        [
            {"action": "keep", "content": "当前参与ProjectA开发"},
            {"action": "keep", "content": "所在团队是平台组"},
            {"action": "keep", "content": "姓名：孙七"},
        ],
        [
            {"action": "keep", "content": "代码风格偏好函数式"},
        ],
    ),
    _build(
        "syn_verbose",
        "change",
        "冗长偏好表述, 测提炼精简",
        [
            {"action": "keep", "content": "职业：前端工程师"},
        ],
        [
            {
                "action": "change",
                "content": "我个人比较倾向于在写代码的时候使用驼峰命名法，因为可读性更好，这也是大多数团队的习惯",
                "reason": "剥离论证过程, 提炼为偏好",
            },
            {"action": "keep", "content": "喜欢用VSCode，装了Python和Git插件"},
        ],
    ),
    _build(
        "syn_financial",
        "negative",
        "财务事实(风险偏好/薪资/持仓), 争议项保留不评",
        [
            {"action": "keep", "content": "风险偏好：稳健型投资"},
            {"action": "keep", "content": "持有某科技股约1000股"},
            {"action": "keep", "content": "月薪2万"},
        ],
        [
            {"action": "keep", "content": "偏好低风险理财"},
        ],
    ),
]


def main() -> None:
    for fx in SAMPLES:
        (FIX_DIR / f"{fx['sample_id']}.json").write_text(
            json.dumps(fx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        gt = fx["ground_truth"]
        print(
            f"✓ {fx['sample_id']}.json  (delete={len(gt['delete'])}, change={len(gt['change'])})"
        )
    print(f"\n合成样本构建完成: {len(SAMPLES)} 个")


if __name__ == "__main__":
    main()
