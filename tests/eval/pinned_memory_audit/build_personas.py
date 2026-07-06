"""多样化用户画像样本 - 扩展评测集覆盖度.

想象不同职业/身份/年龄/心情的用户, 会给出什么样的"重要信息"值得被记录.
每个画像混入该类用户典型的高价值细节 + 噪音 + 灰色表述, 测试 prompt 跨画像的泛化能力.
目标: 更高概率留下高价值细节(标准本身模糊, 不追求精确).
"""

from __future__ import annotations

import json
import pathlib

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"


def _build(sample_id, sample_type, desc, basic_items, pref_items):
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


PERSONAS = [
    _build(
        "persona_programmer",
        "mixed",
        "后端工程师(技术画像)",
        [
            {"action": "keep", "content": "职业：后端工程师"},
            {"action": "keep", "content": "主力语言Go和Python, 工作5年"},
            {"action": "keep", "content": "偏好微服务架构, 重视可观测性"},
            {"action": "delete", "content": "昨天的线上支付bug已修复"},
        ],
        [
            {"action": "keep", "content": "编辑器用Vim, 装了LSP插件"},
            {"action": "delete", "content": "计划下周重构支付模块"},
            {"action": "delete", "content": "希望CI流水线更快"},
        ],
    ),
    _build(
        "persona_teacher",
        "mixed",
        "高中教师(教育画像)",
        [
            {"action": "keep", "content": "高中数学老师, 带高二两个班"},
            {"action": "keep", "content": "偏好启发式教学, 反对照本宣科"},
            {"action": "delete", "content": "上周教研会讨论了新大纲"},
        ],
        [
            {"action": "keep", "content": "板书为主, 较少用PPT"},
            {"action": "delete", "content": "打算下学期引入项目制学习"},
        ],
    ),
    _build(
        "persona_retiree",
        "mixed",
        "退休老人(老年画像, 含用药史难点)",
        [
            {"action": "keep", "content": "68岁, 退休工人"},
            {
                "action": "keep",
                "content": "有高血压和轻度糖尿病, 每日服用缬沙坦和二甲双胍",
            },
            {"action": "keep", "content": "老伴三年前过世, 目前独居"},
            {"action": "delete", "content": "昨天去社区医院量了血压, 偏高"},
        ],
        [
            {"action": "keep", "content": "每天早晨在公园打太极拳"},
            {"action": "delete", "content": "希望社区多组织老年人活动"},
        ],
    ),
    _build(
        "persona_new_mom",
        "mixed",
        "新手妈妈(育儿画像)",
        [
            {"action": "keep", "content": "宝宝8个月大, 纯母乳喂养"},
            {"action": "keep", "content": "宝宝对鸡蛋和蛋白过敏"},
            {"action": "delete", "content": "昨晚宝宝发烧38.5, 今早已退"},
        ],
        [
            {"action": "keep", "content": "认同亲密育儿法, 注重回应宝宝需求"},
            {"action": "delete", "content": "计划下周带宝宝接种麻腮风疫苗"},
        ],
    ),
    _build(
        "persona_sales",
        "mixed",
        "医疗器械销售(商务画像)",
        [
            {"action": "keep", "content": "医疗器械销售, 负责华东三省"},
            {"action": "keep", "content": "沟通风格直接, 不喜欢绕弯子"},
            {"action": "delete", "content": "昨天签下了苏州第一人民医院的单子"},
        ],
        [
            {"action": "keep", "content": "外勤为主, 主要用微信和电话联系客户"},
            {"action": "delete", "content": "希望公司调整提成比例"},
        ],
    ),
    _build(
        "persona_designer",
        "mixed",
        "UI设计师(创意画像, 含灰色提炼)",
        [
            {"action": "keep", "content": "UI设计师, 工作3年, 主要做移动端"},
        ],
        [
            {"action": "keep", "content": "审美偏极简, 注重留白和层次"},
            {"action": "keep", "content": "主力工具Figma, 偶尔用C4D做简单3D"},
            {
                "action": "change",
                "content": "最近在学动效设计, 感觉挺有用, 想深入学下去",
                "reason": "剥离学习过程, 提炼为兴趣方向",
            },
            {"action": "delete", "content": "昨天的首页设计稿被产品驳回了"},
        ],
    ),
    _build(
        "persona_student",
        "mixed",
        "大学生(学习画像)",
        [
            {"action": "keep", "content": "计算机专业大三学生, 在某985"},
            {"action": "keep", "content": "想在算法方向深造"},
        ],
        [
            {"action": "keep", "content": "用番茄钟学习, 专注25分钟休息5分钟"},
            {"action": "delete", "content": "准备考研, 目标本校人工智能实验室"},
            {"action": "delete", "content": "希望这学期课程作业能少一点"},
        ],
    ),
    _build(
        "persona_anxious",
        "mixed",
        "焦虑状态用户(心情画像, 测不被情绪噪音干扰)",
        [
            {"action": "keep", "content": "住在北京海淀区"},
            {"action": "delete", "content": "最近压力特别大, 总担心项目出问题"},
        ],
        [
            {"action": "keep", "content": "喜欢明确具体的指令, 不喜欢模糊任务"},
            {"action": "delete", "content": "今天心情很糟, 不想聊复杂的"},
            {"action": "delete", "content": "希望老板别再催进度了"},
        ],
    ),
    _build(
        "persona_chef",
        "mixed",
        "粤菜厨师(餐饮画像)",
        [
            {"action": "keep", "content": "粤菜厨师, 从业15年, 擅长煲汤"},
            {"action": "keep", "content": "不吃香菜"},
        ],
        [
            {"action": "keep", "content": "做菜讲究火候, 偏好猛火快炒"},
            {"action": "delete", "content": "上周研究了一道新菜式"},
            {"action": "delete", "content": "打算明年考高级技师证"},
        ],
    ),
    _build(
        "persona_translator",
        "mixed",
        "自由译者(冗长画像, 测提炼)",
        [
            {
                "action": "change",
                "content": "我是一名自由职业者, 主要做翻译工作, 已经做了大概三年了, 平时接中日互译的法律类稿件比较多",
                "reason": "剥离冗长表述, 提炼为身份与专长",
            },
            {"action": "keep", "content": "住在成都"},
        ],
        [
            {
                "action": "change",
                "content": "我一般习惯晚上工作, 因为比较安静效率高, 用Trados这个软件",
                "reason": "剥离原因, 提炼为偏好",
            },
            {"action": "delete", "content": "昨天接了个加急的合同翻译熬夜到三点"},
        ],
    ),
]


def main() -> None:
    for fx in PERSONAS:
        (FIX_DIR / f"{fx['sample_id']}.json").write_text(
            json.dumps(fx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        gt = fx["ground_truth"]
        print(
            f"✓ {fx['sample_id']}.json  (delete={len(gt['delete'])}, change={len(gt['change'])})"
        )
    print(f"\n画像样本构建完成: {len(PERSONAS)} 个")


if __name__ == "__main__":
    main()
