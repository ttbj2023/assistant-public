"""任务形态模式解析器单元测试: JSON 宽容解析与 plain 自由文本解析."""

from scripts.benchmarks.tool_filter.task_form import (
    _parse_plain_names,
    _parse_relevant_indices,
    _parse_relevant_names,
)

_CANDIDATES = ["music_player", "calendar", "git_manager", "email_sender"]


class TestParseRelevantIndices:
    """编号模式解析, 坏 JSON 降级不中断."""

    def test_标准编号数组(self) -> None:
        assert _parse_relevant_indices('{"relevant": [1, 3]}') == [1, 3]

    def test_坏json返回None降级(self) -> None:
        assert _parse_relevant_indices('{"relevant": [1, 2') is None

    def test_空输出返回None(self) -> None:
        assert _parse_relevant_indices("") is None


class TestParseRelevantNames:
    """JSON 名称模式解析, 含宽容兜底."""

    def test_标准数组解析(self) -> None:
        assert _parse_relevant_names('{"relevant": ["music_player"]}') == {
            "music_player"
        }

    def test_字符串形态按候选拆分(self) -> None:
        # 0.8B 实测形态: relevant 为字符串
        assert _parse_relevant_names('{"relevant": "git_manager"}') == {"git_manager"}

    def test_逗号分隔字符串拆分(self) -> None:
        assert _parse_relevant_names('{"relevant": "music_player, calendar"}') == {
            "music_player",
            "calendar",
        }

    def test_键值反转取键(self) -> None:
        # 0.8B 实测形态: 把工具名当键, 描述当值, 无 relevant 键
        assert _parse_relevant_names(
            '{"image_generator": "AI图片生成工具\uff0c根据文字描述生成图片"}',
            {"image_generator", "music_player"},
        ) == {"image_generator"}

    def test_非json输出返回None触发降级(self) -> None:
        assert _parse_relevant_names("我觉得都需要保留") is None

    def test_非列表非字符串返回None(self) -> None:
        assert _parse_relevant_names('{"relevant": 42}') is None

    def test_无关键且键非工具名返回None(self) -> None:
        assert _parse_relevant_names('{"foo": "bar"}') is None


class TestParsePlainNames:
    """plain 模式解析: 自由文本中识别候选工具名."""

    def test_逐行输出识别(self) -> None:
        assert _parse_plain_names("music_player\ncalendar\n", _CANDIDATES) == {
            "music_player",
            "calendar",
        }

    def test_带列表前缀识别(self) -> None:
        assert _parse_plain_names("- music_player\n1. calendar\n", _CANDIDATES) == {
            "music_player",
            "calendar",
        }

    def test_行内混排识别(self) -> None:
        assert _parse_plain_names(
            "相关工具: music_player 和 calendar", _CANDIDATES
        ) == {"music_player", "calendar"}

    def test_代码块包裹识别(self) -> None:
        assert _parse_plain_names("```\nmusic_player\n```\n", _CANDIDATES) == {
            "music_player"
        }

    def test_识别不出任何候选返回None触发降级(self) -> None:
        assert _parse_plain_names("抱歉我不明白", _CANDIDATES) is None

    def test_子串不误报(self) -> None:
        # 候选名须作为独立标识符出现, 不作为其他标识符的子串被误识别
        assert _parse_plain_names("calendar_view 很好用", ["calendar"]) is None
        assert _parse_plain_names("musical", ["music_player"]) is None

    def test_空内容返回None(self) -> None:
        assert _parse_plain_names("", _CANDIDATES) is None
