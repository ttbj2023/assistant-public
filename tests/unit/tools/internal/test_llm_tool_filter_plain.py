"""_llm_tool_filter plain 形态 (SFT 模型对齐) 测试."""

from src.tools.internal._llm_tool_filter import (
    _SYSTEM_PROMPT_PLAIN,
    _build_user_message_plain,
    _parse_plain_names,
)

CANDS = [
    {"name": "weather_query", "description": "查询指定城市实时天气"},
    {"name": "music_player", "description": "音乐播放工具"},
    {"name": "calendar", "description": "日程管理工具"},
]


def test_plain系统提示含none哨兵与示例():
    assert "none" in _SYSTEM_PROMPT_PLAIN
    assert "music_player" in _SYSTEM_PROMPT_PLAIN


def test_plain用户消息无编号取前3行描述():
    cands = [
        {
            "name": "weather_query",
            "description": "查询指定城市实时天气",
            "full_description": (
                "天气查询工具\n查询指定城市当前与未来天气\n支持逐小时与7日预报\n第4行参数细节不入筛选"
            ),
        },
        {"name": "music_player", "description": "音乐播放工具"},
    ]
    msg = _build_user_message_plain("北京 天气", cands)
    assert "用户查询: 北京 天气" in msg
    assert (
        "- weather_query: 天气查询工具\n查询指定城市当前与未来天气\n支持逐小时与7日预报"
        in msg
    )
    assert "第4行参数细节不入筛选" not in msg
    assert "1." not in msg


def test_plain无full_description时回退description():
    msg = _build_user_message_plain("北京 天气", CANDS)
    assert "- weather_query: 查询指定城市实时天气" in msg


def test_plain解析按行取候选内工具名():
    names = {"weather_query", "music_player", "calendar"}
    out = _parse_plain_names("weather_query\nmusic_player", names)
    assert out == {"weather_query", "music_player"}


def test_plain解析候选外名字被忽略():
    names = {"weather_query", "calendar"}
    out = _parse_plain_names("weather_query\nnonexistent_tool", names)
    assert out == {"weather_query"}


def test_plain字面none返回空集被尊重():
    """模型明确输出 none = 判定无相关工具, 返回空集(合法空, 不降级)."""
    names = {"weather_query", "calendar"}
    assert _parse_plain_names("none", names) == set()


def test_plain空输出与无候选名返回None触发降级():
    """空输出/解析不出候选名 = 解析失败, 返回 None 降级全保留."""
    names = {"weather_query", "calendar"}
    assert _parse_plain_names("", names) is None
    assert _parse_plain_names("无关文本", names) is None


def test_plain解析容忍引导符与空白():
    names = {"weather_query"}
    out = _parse_plain_names("`weather_query`", names)
    assert out == {"weather_query"}
