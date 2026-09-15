"""KnowledgeImageDescriber 单元测试 - 离线图片题注 + sidecar 幂等缓存.

测试范围:
1. 首次描述: 调用视觉模型, 题注落 sidecar(含 content hash)
2. 幂等缓存: 未变更图片二次调用直接命中, 不再调用视觉模型
3. 图片变更: hash 不匹配时缓存失效, 重新生成
4. 图片缺失 / 视觉失败 / 碎片结果: 返回 None 且不写 sidecar
"""

from __future__ import annotations

from pathlib import Path

from src.knowledge_base.image_describer import KnowledgeImageDescriber


class _FakeDescriber:
    """内联 Mock: 记录调用次数, 返回固定题注(None 模拟视觉失败)."""

    def __init__(self, caption: str | None = "滚筒杀青机结构示意图, 画面为机械剖面图") -> None:
        self.calls = 0
        self.caption = caption


def _write_image(root: Path, rel_path: str, data: bytes = b"fake-jpeg-bytes") -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _describer(fake: _FakeDescriber) -> KnowledgeImageDescriber:
    """构造 KnowledgeImageDescriber, 将视觉调用替换为 fake."""
    describer = KnowledgeImageDescriber()

    async def _fake_vision(image_path: Path, mime_type: str) -> str | None:
        fake.calls += 1
        return fake.caption

    describer._describe_via_vision = _fake_vision  # type: ignore[method-assign]
    return describer


class TestDescribe:
    async def test_first_describe_calls_vision_and_writes_sidecar(self, tmp_path):
        fake = _FakeDescriber()
        describer = _describer(fake)
        _write_image(tmp_path, "中国茶经/images/a.jpg")

        caption = await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert caption == "滚筒杀青机结构示意图, 画面为机械剖面图"
        assert fake.calls == 1
        sidecar = tmp_path / "中国茶经/images/.desc/a.jpg.json"
        assert sidecar.exists()
        data = sidecar.read_text(encoding="utf-8")
        assert "滚筒杀青机结构示意图" in data
        assert "hash" in data

    async def test_unchanged_image_second_call_hits_cache(self, tmp_path):
        fake = _FakeDescriber()
        describer = _describer(fake)
        _write_image(tmp_path, "中国茶经/images/a.jpg")

        first = await describer.describe(tmp_path, "中国茶经/images/a.jpg")
        second = await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert first == second
        assert fake.calls == 1

    async def test_changed_image_invalidates_cache(self, tmp_path):
        fake = _FakeDescriber()
        describer = _describer(fake)
        _write_image(tmp_path, "中国茶经/images/a.jpg", b"v1-bytes")

        await describer.describe(tmp_path, "中国茶经/images/a.jpg")
        _write_image(tmp_path, "中国茶经/images/a.jpg", b"v2-bytes")
        await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert fake.calls == 2

    async def test_missing_image_returns_none(self, tmp_path):
        describer = _describer(_FakeDescriber())
        assert await describer.describe(tmp_path, "不存在/x.jpg") is None

    async def test_vision_failure_returns_none_without_sidecar(self, tmp_path):
        describer = _describer(_FakeDescriber(caption=None))
        _write_image(tmp_path, "中国茶经/images/a.jpg")

        result = await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert result is None
        assert not (tmp_path / "中国茶经/images/.desc/a.jpg.json").exists()

    async def test_json_fragment_caption_rejected_without_sidecar(self, tmp_path):
        """JSON 碎片(视觉模型输出不稳的降级产物)视为失败, 不污染缓存."""
        describer = _describer(
            _FakeDescriber(caption='{"brief": "这是一张黑白线条绘制的螺旋')
        )
        _write_image(tmp_path, "中国茶经/images/a.jpg")

        result = await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert result is None
        assert not (tmp_path / "中国茶经/images/.desc/a.jpg.json").exists()

    async def test_too_short_caption_rejected(self, tmp_path):
        """过短题注(信息量不足)视为失败."""
        describer = _describer(_FakeDescriber(caption="图"))
        _write_image(tmp_path, "中国茶经/images/a.jpg")

        result = await describer.describe(tmp_path, "中国茶经/images/a.jpg")

        assert result is None
        assert not (tmp_path / "中国茶经/images/.desc/a.jpg.json").exists()
