"""QueryAliasModel 隐藏式字段别名机制测试.

核心契约:
- LLM 误传声明别名时静默重映射到目标字段 (容错)
- JSON Schema 永不暴露别名 (避免污染 LLM 上下文)
- _field_aliases 作为 ClassVar, 不出现在 schema 中
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ConfigDict, Field

from src.tools.shared.query_alias_model import QueryAliasModel


class _StubInput(QueryAliasModel):
    """测试用桩模型, 主字段 content, 含必填 filename.

    声明两条别名: query -> content, title -> filename
    """

    _field_aliases: ClassVar[dict[str, str]] = {
        "query": "content",
        "title": "filename",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    content: str = Field(min_length=1)
    filename: str = Field(min_length=1)


class _WildcardInput(QueryAliasModel):
    """测试用通配别名桩模型.

    声明后缀通配别名: 任何 *_type 映射到 engine, 任何 *_code 映射到 code.
    """

    _field_aliases: ClassVar[dict[str, str]] = {
        "*_type": "engine",
        "*_code": "code",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    engine: str = Field(min_length=1)
    code: str = Field(min_length=1)


class TestAliasRemap:
    """字段别名重映射行为."""

    def test_first_alias_remapped_when_target_missing(self) -> None:
        """主字段缺失时, 第一条别名 (query) 重映射到目标字段."""
        inp = _StubInput(query="# 内容", filename="f")
        assert inp.content == "# 内容"

    def test_second_alias_remapped_when_target_missing(self) -> None:
        """主字段缺失时, 第二条别名 (title) 重映射到目标字段."""
        inp = _StubInput(title="报告", content="# 内容")
        assert inp.filename == "报告"

    def test_alias_dropped_when_target_present(self) -> None:
        """目标字段已存在时, 丢弃别名保留目标字段 (不触发 extra_forbidden)."""
        inp = _StubInput(query="被丢弃", content="实际内容", filename="f")
        assert inp.content == "实际内容"

        inp2 = _StubInput(title="被丢弃", content="内容", filename="实际名")
        assert inp2.filename == "实际名"

    def test_multiple_aliases_simultaneously(self) -> None:
        """同时传入多个别名时, 全部正确重映射."""
        inp = _StubInput.model_validate({"query": "Q", "title": "T"})
        assert inp.content == "Q"
        assert inp.filename == "T"

    def test_normal_field_still_works(self) -> None:
        """不传别名时, 原始字段名正常工作."""
        inp = _StubInput(content="正常", filename="f")
        assert inp.content == "正常"

    def test_model_validate_path_also_remapped(self) -> None:
        """model_validate 路径 (LangChain 实际调用方式) 同样重映射."""
        inp = _StubInput.model_validate({"query": "# v", "filename": "f"})
        assert inp.content == "# v"

    def test_extra_field_still_rejected(self) -> None:
        """声明别名之外的其他未知字段仍被 extra=forbid 拒绝."""
        with pytest.raises(Exception):
            _StubInput.model_validate({"content": "x", "filename": "f", "unknown": "y"})


class TestSchemaHiding:
    """别名对 LLM 隐藏的契约."""

    def test_schema_must_not_expose_aliases(self) -> None:
        """JSON Schema 的 properties 中绝不能出现任何别名."""
        props = _StubInput.model_json_schema()["properties"]
        assert "query" not in props, f"query 别名泄露到 schema: {list(props)}"
        assert "title" not in props, f"title 别名泄露到 schema: {list(props)}"

    def test_schema_must_not_require_aliases(self) -> None:
        """required 列表中绝不能出现别名."""
        required = _StubInput.model_json_schema().get("required", [])
        assert "query" not in required
        assert "title" not in required

    def test_schema_exposes_original_field(self) -> None:
        """原始字段名正常暴露."""
        props = _StubInput.model_json_schema()["properties"]
        assert "content" in props
        assert "filename" in props

    def test_classvar_not_in_schema(self) -> None:
        """_field_aliases 作为 ClassVar 不泄露到 schema."""
        props = _StubInput.model_json_schema()["properties"]
        assert "_field_aliases" not in props


class TestWildcardAliasRemap:
    """后缀通配别名重映射行为."""

    def test_wildcard_type_suffix_maps_to_engine(self) -> None:
        """chart_type / graph_type 等 *_type 应映射到 engine."""
        inp = _WildcardInput(chart_type="mermaid", code="graph TD\nA-->B")
        assert inp.engine == "mermaid"
        assert inp.code == "graph TD\nA-->B"

    def test_wildcard_code_suffix_maps_to_code(self) -> None:
        """mermaid_code / vega_code 等 *_code 应映射到 code."""
        inp = _WildcardInput(engine="vega_lite", vega_code='{"mark":"bar"}')
        assert inp.code == '{"mark":"bar"}'

    def test_wildcard_alias_dropped_when_target_present(self) -> None:
        """目标字段已存在时, 通配别名应被丢弃, 不触发 extra_forbidden."""
        inp = _WildcardInput(
            engine="mermaid",
            chart_type="vega_lite",
            code="graph TD\nA-->B",
        )
        assert inp.engine == "mermaid"
        assert inp.code == "graph TD\nA-->B"

    def test_wildcard_multiple_aliases_same_target_first_wins(self) -> None:
        """多个 *_type 同时存在时, 第一个保留, 其余丢弃."""
        data = {"chart_type": "mermaid", "graph_type": "vega_lite", "code": "c"}
        inp = _WildcardInput.model_validate(data)
        assert inp.engine in {"mermaid", "vega_lite"}
        assert inp.code == "c"

    def test_wildcard_schema_does_not_expose_aliases(self) -> None:
        """通配别名本身不能出现在 JSON Schema 中."""
        schema = _WildcardInput.model_json_schema()
        props = schema["properties"]
        assert "chart_type" not in props
        assert "mermaid_code" not in props
        assert "*_type" not in props
        assert "engine" in props
        assert "code" in props
