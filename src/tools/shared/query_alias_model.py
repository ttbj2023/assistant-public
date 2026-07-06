"""共享输入模型基类 - 容忍 LLM 对字段的固有命名偏好.

LLM 经常把字段叫作符合其训练分布的名称 (如把 filename 叫作 title),
这些别名并不存在于实际 schema 中. 若直接使用 Field(alias=...) 容错,
Pydantic v2 的 model_json_schema() 会把别名暴露到 LLM 可见的 JSON Schema,
污染上下文.

本基类通过 model_validator(mode="before") 在校验前把别名重映射到子类声明的
目标字段名, 别名完全不出现在 schema 中. before-validator 在 extra="forbid"
检查之前执行 (已于 Pydantic 2.13.4 验证), 可安全 pop.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, model_validator


class QueryAliasModel(BaseModel):
    """容忍 LLM 常见字段别名误传的输入模型基类.

    子类需声明:
        _field_aliases: ClassVar[dict[str, str]] = {"别名": "目标字段", ...}

    行为 (对每条别名规则):
    - 输入含别名且目标字段缺失: 别名 -> 目标字段
    - 输入含别名且目标字段已存在: 丢弃别名, 保留目标字段
    - 输入不含别名: 无副作用
    - JSON Schema 中只出现原始字段名, 别名永不暴露
    """

    _field_aliases: ClassVar[dict[str, str]] = {}

    @model_validator(mode="before")
    @classmethod
    def _remap_field_aliases(cls, data: object) -> object:
        """把所有声明的别名重映射到目标字段 (仅当目标字段缺失).

        支持两类别名:
        - 精确匹配: {"title": "filename"}
        - 后缀通配: {"*_type": "engine"} 会把 chart_type / graph_type 等映射到 engine
        """
        if not isinstance(data, dict):
            return data

        field_names = set(cls.model_fields.keys())
        exact_aliases = {
            alias: target
            for alias, target in cls._field_aliases.items()
            if not alias.startswith("*")
        }
        wildcard_aliases = {
            alias: target
            for alias, target in cls._field_aliases.items()
            if alias.startswith("*")
        }

        # 1. 精确别名: 先处理, 避免与通配规则产生歧义
        for alias, target in exact_aliases.items():
            if alias not in data:
                continue
            # 注意: 必须先 pop 再赋值, 不能写 {**data, target: data.pop(...)}
            # (dict 推导式中 **data 先展开, 会让 alias 残留触发 extra_forbidden)
            value = data.pop(alias)
            if target not in data:
                data[target] = value

        # 2. 后缀通配别名: 如 *_type -> engine, *_code -> code
        for alias, target in wildcard_aliases.items():
            suffix = alias[1:]
            for key in list(data.keys()):
                if key in field_names:
                    continue
                if not key.endswith(suffix):
                    continue
                value = data.pop(key)
                if target not in data:
                    data[target] = value

        return data


__all__ = ["QueryAliasModel"]
