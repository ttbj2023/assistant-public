"""AI推理协调器 - 专注于LangChain Agent创建和执行协调."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from src.agent.processors.system_prompt_assembler import assemble_system_prompt
from src.config.inference_config import get_config as get_inference_config
from src.config.tools_config import get_config as get_tools_config
from src.core.open_webui_format import format_tool_call_done
from src.core.streaming import StreamContent
from src.inference.llm.model_loader import create_llm
from src.inference.llm.response_utils import (
    content_to_text,
)
from src.inference.llm.response_utils import (
    filter_think_tags_streaming as _filter_think_tags_streaming_impl,
)
from src.inference.llm.response_utils import (
    strip_think_tags as _strip_think_tags_impl,
)
from src.inference.llm.retry_predicates import (
    format_llm_failure_message as _llm_failure_message,
)
from src.inference.llm.retry_predicates import (
    is_retryable_llm_exception as _is_retryable_llm_exception,
)
from src.tools import get_tools_manager
from src.tools.experts.agent_utils import enable_tool_error_handling
from src.tools.middleware import SkillLoadMiddleware, ToolDiscoveryMiddleware
from src.tools.skills.skill_bridge import get_skill_bridge
from src.utils.debug_config import is_debug_enabled

logger = logging.getLogger(__name__)

# data URI 解析: data:<mime>;base64,<payload>
_DATA_URI_RE = re.compile(r"^data:[^;]+;base64,(.+)$", re.DOTALL)

# 签名下载 URL (markdown 链接内): 用于跨轮还原文件引用.
# 路径结构 {base}/{user}/{thread}/{agent}/{file_id8hex}/{expiry}/{sig32hex}/{filename},
# file_id 为明文路径段, 直接解析无需查表/解密.
_SIGNED_FILE_URL_RE = re.compile(
    r"!?\[(?P<label>[^\]]*)\]\([^)]*?"
    r"/(?P<fid>[0-9a-f]{8})/\d+/[0-9a-f]{32}/[^/)]+\)"
)

# <details> HTML 标签 — 前端渲染约定, 不应出现在 LLM 上下文.
# 先匹配闭合标签, 再匹配残余开标签; 开标签正则属性值感知,
# 处理 LLM 生成标签中 arguments 内未转义的 > (如 Markdown 引用).
_DETAILS_CLOSED_RE = re.compile(
    r"\n?<details\b[^>]*>[\s\S]*?</details>\s*", re.IGNORECASE
)
_DETAILS_OPEN_RE = re.compile(
    r"\n?<details\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>\s*", re.IGNORECASE
)
_DETAILS_CLOSE_TAG_RE = re.compile(r"\n?</details>\s*", re.IGNORECASE)

# DeepSeek DSML 原生工具调用标记 (全角竖线 ｜ U+FF5C)
_DSML_TAG_RE = re.compile(r"</?\uff5c\uff5cDSML\uff5c\uff5c\w+>\s*")


def _strip_tool_artifacts_text(text: str) -> str:
    """剥离 <details> 标签 + DSML 标记, 返回清洗后的纯文本."""
    text = _DETAILS_CLOSED_RE.sub("", text)
    text = _DETAILS_OPEN_RE.sub("", text)
    text = _DETAILS_CLOSE_TAG_RE.sub("", text)
    text = _DSML_TAG_RE.sub("", text)
    return text.strip()


def _decode_data_uri(url: str) -> bytes | None:
    """解析 base64 data URI 返回解码 bytes; 非 data URI 或解码失败返回 None."""
    match = _DATA_URI_RE.match(url)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(1))
    except Exception:
        return None


def _history_has_image_blocks(history_messages: list[BaseMessage]) -> bool:
    """快速检测历史是否含 image_url 内容块."""
    for msg in history_messages:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return True
    return False


@dataclass
class _StreamState:
    """流式 chunk 循环的跨 chunk 可变状态.

    封装工具调用累积追踪与 <think/> 标签过滤状态, 由 _process_stream_chunk
    原地修改, 主循环仅负责迭代与 yield.
    """

    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen_tool_call_ids: set[str] = field(default_factory=set)
    chunk_index_to_id: dict[int, str] = field(default_factory=dict)
    in_think_block: bool = False
    think_buffer: str = ""


class _AgentSetup(NamedTuple):
    """同步/流式推理前奏的共享构建产物.

    process_with_agent 与 process_with_agent_stream 的前奏 (建模型 → 工具集 →
    middleware → create_agent → runnable_config → agent_input) 完全一致, 由
    _build_agent_and_config 统一构建. 两条路径的差异 (tool_tracker /
    _ensure_callbacks / streaming flag) 在 helper 内按 streaming 参数门控.
    """

    llm_model: str
    system_prompt: str
    tools: list
    agent: Any
    runnable_config: Any
    agent_input: dict
    tool_stats: dict
    total_timeout: float
    middleware_count: int
    callback_count: int


class InferenceCoordinator:
    """AI推理协调器 - 专注模型推理和工具执行协调.

    职责范围:
    1. AI模型推理协调(LangChain Agent集成)
    2. 工具集创建和管理
    3. Agent调试器集成
    4. 模型调用和响应处理

    不负责:
    1. 用户请求处理流程(由上层负责)
    2. 记忆系统管理(由LocalMemoryProcessor负责)
    3. 业务验证和错误处理(由API层负责)
    """

    def __init__(self, config: dict[str, Any] | None) -> None:
        """初始化AI推理协调器.

        Args:
            config: 应用配置实例

        """
        self.config = config

        logger.info("🚀 AI推理协调器初始化完成")

    async def create_toolset(
        self,
        user_id: str,
        thread_id: str,
        agent_config: Any = None,
        llm_model: str | None = None,
    ) -> tuple[
        list,
        dict[str, Any],
        ToolDiscoveryMiddleware | None,
        str,
        SkillLoadMiddleware | None,
        str,
    ]:
        """创建工具集 - 核心工具直接注入, 休眠工具通过中间件按需发现.

        能力门控: llm_model 提供时, 过滤掉 skip_when_capabilities 与模型能力
        冲突的工具(如多模态模型跳过 analyze_image).

        Skills渐进式披露: agent配了skills时自动加load_skill常驻工具,
        装配SkillLoadMiddleware(executable执行器池)并生成skills段L1清单.

        Returns:
            (核心工具列表, 统计信息, ToolDiscoveryMiddleware实例或None,
             prompt_hints文本, SkillLoadMiddleware实例或None, skills段L1清单文本)

        """
        try:
            tool_manager = get_tools_manager()

            if not agent_config:
                raise ValueError("agent_config参数是必需的,不再支持默认配置回退")

            core_names, dormant_names = self._extract_tool_names(agent_config)

            # Skills处理: agent配了skills则自动加load_skill常驻触发工具(渐进式披露)
            available_skills = list(getattr(agent_config, "skills", []) or [])
            if available_skills and "load_skill" not in core_names:
                core_names.append("load_skill")
                logger.info(
                    "🔧 agent配了skills %s, 自动加load_skill工具",
                    available_skills,
                )

            # 保留展开前的原始名(含组名), 供 prompt_hint 收集使用
            original_core = list(core_names)
            original_dormant = list(dormant_names)

            # 工具组: 组名 -> 成员工具名展开(常驻组与休眠组通用)
            tool_groups = get_tools_config().tool_groups
            group_members_map = {g.name: list(g.members) for g in tool_groups.values()}
            core_names = self._expand_group_names(core_names, group_members_map)
            dormant_names = self._expand_group_names(dormant_names, group_members_map)

            # 能力门控: 过滤与主对话模型能力冲突的工具
            if llm_model:
                from src.inference.llm.definitions.model_registry import get_model

                model_meta = get_model(llm_model)
                if model_meta:
                    model_caps = {str(c) for c in model_meta.capabilities}
                    core_names = self._filter_by_capability(core_names, model_caps)
                    dormant_names = self._filter_by_capability(
                        dormant_names, model_caps
                    )

            logger.info("🔧 构建工具集, 核心: %s, 休眠: %s", core_names, dormant_names)

            tools = await tool_manager.create_tools(
                core_names,
                user_id,
                thread_id,
                agent_id=agent_config.agent_id,
            )

            discovery_middleware = None
            if dormant_names:
                dormant_tools = await tool_manager.create_dormant_tools(
                    dormant_names,
                    user_id,
                    thread_id,
                    agent_id=agent_config.agent_id,
                )
                if dormant_tools:
                    discovery_middleware = ToolDiscoveryMiddleware(
                        dormant_tools,
                        group_members_map=group_members_map,
                    )
                    self._enrich_search_tools_description(
                        tools, dormant_tools, tool_groups=tool_groups
                    )

            # Skills渐进式披露装配(load_skill实例注入skill池 + L1清单 + per-skill关联工具映射)
            skill_load_middleware: SkillLoadMiddleware | None = None
            skill_l1_manifest = ""
            if available_skills:
                skill_bridge = get_skill_bridge()
                self._setup_skill_loading(tools, skill_bridge, available_skills)
                skill_l1_manifest = skill_bridge.get_l1_manifest(available_skills)
                skill_tool_map = await self._build_skill_tool_map(
                    skill_bridge,
                    available_skills,
                    tool_manager,
                    user_id,
                    thread_id,
                    agent_config.agent_id,
                )
                if skill_tool_map:
                    skill_load_middleware = SkillLoadMiddleware(skill_tool_map)
                    logger.info(
                        "🔧 SkillLoadMiddleware装配: %s",
                        {k: [t.name for t in v] for k, v in skill_tool_map.items()},
                    )

            # 收存活工具的 prompt_hint (组级 + 个体级)
            prompt_hints = self._collect_prompt_hints(
                original_core,
                original_dormant,
                core_names,
                dormant_names,
                tool_groups,
            )

            tool_stats = {
                "total_tools": len(tools),
                "dormant_tools": len(dormant_names),
                "tools": tools,
                "internal_tools": len([
                    t
                    for t in tools
                    if hasattr(t, "name")
                    and any(
                        internal_name in t.name
                        for internal_name in ["todo", "memory_retrieval"]
                    )
                ]),
                "mcp_tools": len([
                    t
                    for t in tools
                    if hasattr(t, "name")
                    and not any(
                        internal_name in t.name
                        for internal_name in ["todo", "memory_retrieval"]
                    )
                ]),
            }

            logger.info(
                f"工具集创建完成: {len(tools)}个核心 + {len(dormant_names)}个休眠",
            )

            return (
                tools,
                tool_stats,
                discovery_middleware,
                prompt_hints,
                skill_load_middleware,
                skill_l1_manifest,
            )

        except Exception as e:
            logger.error("❌ 工具协调器工具集创建失败: %s", e)
            tool_stats = {
                "total_tools": 0,
                "dormant_tools": 0,
                "tools": [],
                "internal_tools": 0,
                "mcp_tools": 0,
                "error": str(e),
            }
            return [], tool_stats, None, "", None, ""

    def _extract_tool_names(self, agent_config: Any) -> tuple[list[str], list[str]]:
        """从Agent配置中提取工具名称列表.

        Args:
            agent_config: Agent配置对象, 包含tools和optional_tools配置

        Returns:
            (核心工具名列表, 休眠工具名列表)

        """
        if not agent_config or not hasattr(agent_config, "tools"):
            logger.warning("⚠️ Agent配置中没有tools配置, 返回空工具列表")
            return [], []

        core_tools = agent_config.tools or []
        dormant_tools = getattr(agent_config, "optional_tools", []) or []

        logger.info("🔧 核心工具: %s, 休眠工具: %s", core_tools, dormant_tools)
        return list(core_tools), list(dormant_tools)

    @staticmethod
    def _enrich_search_tools_description(
        core_tools: list[Any],
        dormant_tools: list[Any],
        tool_groups: dict[str, Any] | None = None,
    ) -> None:
        """将休眠工具清单注入search_available_tools的描述和实例目录中.

        工具组处理: 组成员跳过独立catalog条目, 改由组条目代表检索;
        组条目用组summary作为description, 组keywords参与检索.
        组对主对话模型透明: LLM只感知被注入的子工具.

        Args:
            core_tools: 核心工具列表
            dormant_tools: 休眠工具列表
            tool_groups: 工具组配置(组名 -> ToolGroupConfig), 可选

        """
        search_tool = next(
            (t for t in core_tools if t.name == "search_available_tools"),
            None,
        )
        if not search_tool or not dormant_tools:
            return

        # 构建 member -> group_name 反向映射 + 组元数据(仅本agent启用的组)
        member_to_group: dict[str, str] = {}
        group_meta: dict[str, dict[str, Any]] = {}
        if tool_groups:
            for group_name, group_cfg in tool_groups.items():
                group_meta[group_name] = {
                    "summary": getattr(group_cfg, "summary", "") or "",
                    "description": getattr(group_cfg, "description", "") or "",
                    "keywords": list(getattr(group_cfg, "keywords", []) or []),
                    "display_label": getattr(
                        group_cfg, "display_label", group_name.removesuffix("_group")
                    ),
                }
                for member in getattr(group_cfg, "members", []) or []:
                    member_to_group[member] = group_name

        catalog: dict[str, dict[str, Any]] = {}
        desc_lines = ["当前可发现的工具:"]
        # 组成员按组聚合, 供后续组建组条目
        grouped_members: dict[str, list[Any]] = {}

        for tool in dormant_tools:
            group_name = member_to_group.get(tool.name)
            if group_name:
                grouped_members.setdefault(group_name, []).append(tool)
                continue
            tagline = (
                getattr(tool, "summary", "")
                or (tool.description or "").split("\n")[0].strip()
            )
            full_desc = getattr(tool, "description", "") or ""
            keywords = getattr(tool, "search_keywords", []) or []
            name_parts = [p for p in tool.name.split("_") if p]
            catalog[tool.name] = {
                "name": tool.name,
                "description": tagline,
                "full_description": full_desc,
                "keywords": keywords,
                "name_parts": name_parts,
            }
            desc_lines.append(f"- {tool.name}: {tagline}")

        # 为本agent dormant池里出现的组建组条目(组名仅作内部catalog key与filter输入,
        # 绝不进入主对话模型可见的matched_tools; 命中后由search工具展开为成员工具名)
        for group_name in grouped_members:
            meta = group_meta.get(group_name, {})
            summary = meta.get("summary", "")
            full_desc = meta.get("description", "") or summary
            keywords = meta.get("keywords", [])
            display_label = meta.get("display_label", group_name.removesuffix("_group"))
            # name_parts 基于 display_label 派生, 避免 "group" 无义 token 参与匹配
            name_parts = [p for p in display_label.split("_") if p]
            # 收集本组在dormant池中成员的描述, 供search返回时展开为成员工具条目.
            # 设计约定: 子工具不向 search_available_tools 提供独立描述信息,
            # 统一由工具组 summary/description 接管, 成员只暴露名称.
            member_entries = []
            for m_tool in grouped_members[group_name]:
                member_entries.append({"name": m_tool.name, "description": ""})
            catalog[group_name] = {
                "name": group_name,
                "description": summary,
                "full_description": full_desc,
                "keywords": keywords,
                "name_parts": name_parts,
                "display_label": display_label,
                "_members": member_entries,
            }
            desc_lines.append(f"- {display_label}: {summary}")

        search_tool.set_catalog(catalog)

        base_desc = "搜索可用的休眠工具. 当你需要某个功能但当前工具列表中没有时, 使用此工具搜索.\n\n"
        desc_lines.append(
            "\n搜索后匹配的工具会自动加载到你的工具列表中, 你可以直接调用它们.",
        )
        enriched = base_desc + "\n".join(desc_lines)
        search_tool.description = enriched

    @staticmethod
    def _setup_skill_loading(
        core_tools: list[Any],
        skill_bridge: Any,
        available_skills: list[str],
    ) -> None:
        """load_skill工具实例注入skill数据源(类似search的catalog注入).

        Args:
            core_tools: 核心工具列表(含load_skill实例)
            skill_bridge: SkillBridge单例(L2/L3正文来源)
            available_skills: 该agent启用的skill名列表

        """
        load_skill_tool = next(
            (t for t in core_tools if t.name == "load_skill"),
            None,
        )
        if load_skill_tool:
            load_skill_tool.set_skill_pool(skill_bridge, available_skills)

    @staticmethod
    async def _build_skill_tool_map(
        skill_bridge: Any,
        available_skills: list[str],
        tool_manager: Any,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> dict[str, list]:
        """构建per-skill关联工具映射(SkillLoadMiddleware加载池).

        从SkillBridge获取各skill的associated_tools配置, 创建工具实例:
        - "skill_executor": 手动实例化(SkillExecutorTool, 非external_tools注册)
        - 其余工具名: 走tool_manager.create_tools(从external_tools注册创建)

        Args:
            skill_bridge: SkillBridge单例
            available_skills: 该agent启用的skill名列表
            tool_manager: 工具管理器(创建external工具实例)
            user_id: 用户ID
            thread_id: 会话ID
            agent_id: Agent ID

        Returns:
            {skill_name: [BaseTool, ...]}; 无关联工具的skill不包含.

        """
        associated_map = skill_bridge.get_associated_tool_names(available_skills)
        if not associated_map:
            return {}

        skill_tool_map: dict[str, list] = {}
        for skill_name, tool_names in associated_map.items():
            external_names: list[str] = []
            special_tools: list = []
            for tn in tool_names:
                if tn == "skill_executor":
                    from src.tools.skills.skill_executor_tool import SkillExecutorTool

                    special_tools.append(
                        SkillExecutorTool(
                            user_id=user_id,
                            thread_id=thread_id,
                            agent_id=agent_id,
                        )
                    )
                else:
                    external_names.append(tn)

            if external_names:
                created = await tool_manager.create_tools(
                    external_names,
                    user_id,
                    thread_id,
                    agent_id=agent_id,
                )
                special_tools.extend(created)

            if special_tools:
                skill_tool_map[skill_name] = special_tools

        return skill_tool_map

    @staticmethod
    def _expand_group_names(
        names: list[str],
        group_members_map: dict[str, list[str]],
    ) -> list[str]:
        """展开工具组名为成员工具名, 保持顺序并去重."""
        expanded: list[str] = []
        for name in names:
            members = group_members_map.get(name)
            if members:
                for member in members:
                    if member not in expanded:
                        expanded.append(member)
            elif name not in expanded:
                expanded.append(name)
        return expanded

    @staticmethod
    def _filter_by_capability(
        names: list[str],
        model_caps: set[str],
    ) -> list[str]:
        """过滤掉 skip_when_capabilities 与模型能力冲突的工具.

        Args:
            names: 展开后的工具名列表
            model_caps: 主对话模型的能力集合(如 {"image_input", "tool_calling"})

        Returns:
            过滤后的工具名列表

        """
        if not model_caps:
            return names

        tools_cfg = get_tools_config()
        filtered: list[str] = []
        for name in names:
            cfg = tools_cfg.get_internal_tool_config(name)
            if cfg is None:
                cfg = tools_cfg.get_external_tool_config(name)
            skip_caps = getattr(cfg, "skip_when_capabilities", None) or []
            if skip_caps and model_caps.intersection(skip_caps):
                logger.info(
                    "🔧 能力门控: 跳过 %s (skip_when_capabilities=%s ∩ model=%s)",
                    name,
                    skip_caps,
                    model_caps,
                )
                continue
            filtered.append(name)
        return filtered

    @staticmethod
    def _collect_prompt_hints(
        original_core: list[str],
        original_dormant: list[str],
        filtered_core: list[str],
        filtered_dormant: list[str],
        tool_groups: dict[str, Any],
    ) -> str:
        """收集存活工具的 prompt_hint (组级 + 个体级).

        Args:
            original_core: 展开前的核心工具名(含组名)
            original_dormant: 展开前的休眠工具名(含组名)
            filtered_core: 展开+能力过滤后的核心工具名
            filtered_dormant: 展开+能力过滤后的休眠工具名
            tool_groups: 工具组配置字典

        Returns:
            拼接后的 prompt_hint 文本, 空字符串表示无提示

        """
        hints: list[str] = []
        seen: set[str] = set()
        tools_cfg = get_tools_config()

        # 第一轮: 组级 prompt_hint (从展开前的原始名中识别组名)
        for name in original_core + original_dormant:
            group_cfg = tool_groups.get(name)
            if group_cfg is None or name in seen:
                continue
            hint = getattr(group_cfg, "prompt_hint", "") or ""
            if hint:
                label = getattr(group_cfg, "display_label", name.removesuffix("_group"))
                hints.append(f"- {label}: {hint}")
                seen.add(name)
                # 组成员标记为已见, 避免第二轮重复收集
                for member in getattr(group_cfg, "members", []) or []:
                    seen.add(member)

        # 第二轮: 个体工具 prompt_hint (从过滤后的展开名中查找)
        for name in filtered_core + filtered_dormant:
            if name in seen:
                continue
            cfg = tools_cfg.get_internal_tool_config(name)
            if cfg is None:
                cfg = tools_cfg.get_external_tool_config(name)
            hint = getattr(cfg, "prompt_hint", "") or ""
            if hint:
                hints.append(f"- {name}: {hint}")
                seen.add(name)

        return "\n".join(hints)

    async def _build_agent_and_config(
        self,
        user_content: str,
        system_prompt: str,
        llm_config: dict[str, Any],
        user_id: str,
        thread_id: str,
        agent_id: str | None,
        agent_config: Any,
        image_datas: list[dict[str, Any]] | None,
        attachment_infos: list[Any] | None,
        history_messages: list[BaseMessage] | None,
        prompt_sections: dict[str, str] | None,
        *,
        streaming: bool,
    ) -> _AgentSetup:
        """构建同步/流式共享的 Agent 与执行配置 (前奏统一).

        涵盖: 校验 llm_config → create_llm → create_toolset → 装配 system_prompt
        → middleware (retry/tool_call_limit/discovery/skill_load) → create_agent
        → _build_runnable_config → agent_input. 三处同步/流式差异按 streaming 门控:
        streaming=True 时给 llm_config 加 streaming 标记; streaming=False 时启用
        tool_tracker 并经 _ensure_callbacks 确保 callbacks 进入 config.

        Args:
            streaming: True=流式路径 (加 streaming 标记, 无 tool_tracker),
                False=同步路径 (启用 tool_tracker, 确保 callbacks).

        Returns:
            _AgentSetup: 含 agent/runnable_config/agent_input 等供调用方执行.

        """
        if not llm_config or "model" not in llm_config:
            raise ValueError("LLM配置缺失:llm_config必须包含model字段")

        llm_model = llm_config["model"]
        if streaming:
            llm_config = {**llm_config, "streaming": True}

        llm = self._create_llm(llm_model, llm_config)

        (
            tools,
            tool_stats,
            discovery_middleware,
            prompt_hints,
            skill_load_middleware,
            skill_l1_manifest,
        ) = await self.create_toolset(
            user_id,
            thread_id,
            agent_config,
            llm_model=llm_model,
        )

        sections: dict[str, str] = {"base": system_prompt}
        if prompt_hints:
            sections["tools"] = f"## 工具使用策略\n\n{prompt_hints}"
        if skill_l1_manifest:
            sections["skills"] = skill_l1_manifest
        if prompt_sections:
            sections.update(prompt_sections)
        system_prompt = assemble_system_prompt(sections)

        callbacks: list[Any] = []
        middleware: list[Any] = []
        retry_cfg = get_inference_config().retry
        middleware.append(
            ModelRetryMiddleware(
                max_retries=retry_cfg.max_retries,
                retry_on=_is_retryable_llm_exception,
                on_failure=_llm_failure_message,
                initial_delay=retry_cfg.initial_delay,
                max_delay=retry_cfg.max_delay,
            )
        )
        if not streaming:
            logger.debug(
                "🔄 ModelRetryMiddleware已添加(max_retries=%d, total_timeout=%.0fs)",
                retry_cfg.max_retries,
                retry_cfg.total_timeout,
            )
        middleware.append(ToolCallLimitMiddleware(run_limit=20, exit_behavior="end"))

        if discovery_middleware:
            middleware.append(discovery_middleware)

        if skill_load_middleware:
            middleware.append(skill_load_middleware)

        # tool_tracker 仅同步路径启用 (流式无追踪器, callbacks 恒空)
        if not streaming:
            tool_tracker = self._get_tool_tracker()
            if tool_tracker:
                callbacks.append(tool_tracker)
                logger.info("🔧 ToolCallTracker已启用 - 将监控工具调用和LLM执行")

        if middleware:
            agent = create_agent(
                llm,
                tools,
                system_prompt=system_prompt,
                middleware=middleware,
            )
        else:
            agent = create_agent(llm, tools, system_prompt=system_prompt)
        if not streaming:
            if middleware:
                logger.info(
                    f"🚀 LangChain Agent创建成功(包含{len(middleware)}个中间件)",
                )
            else:
                logger.info("🚀 LangChain Agent创建成功(无中间件)")

        self._enable_tool_error_handling(agent)

        runnable_config = self._build_runnable_config(
            callbacks,
            user_id,
            thread_id,
            agent_id,
        )
        # 同步路径须确保 callbacks 进入 config (流式 callbacks 恒空, 无需此步)
        if not streaming and callbacks:
            runnable_config = self._ensure_callbacks_in_config(
                runnable_config, callbacks
            )

        # 非多模态模型下, 降级历史里的 base64 图片块为文本占位(simple 模式前端透传)
        history_messages = await self._downgrade_history_images_for_text_model(
            history_messages,
            llm_model,
            user_id,
            thread_id,
            agent_id,
        )

        # 还原历史签名下载 URL 为 [file: id] 标记(simple 模式跨轮文件引用)
        history_messages = await self._restore_file_markers_in_history(
            history_messages,
            user_id,
        )

        # 剥离历史 assistant 消息中的工具调用渲染标记(simple 模式前端透传泄漏)
        history_messages = self._strip_tool_artifacts_in_history(history_messages)

        current_msg = self._build_human_message(
            user_content,
            llm_model,
            image_datas,
            attachment_infos,
        )
        if history_messages:
            messages = [*history_messages, current_msg]
        else:
            messages = [current_msg]
        agent_input = {"messages": messages}

        return _AgentSetup(
            llm_model=llm_model,
            system_prompt=system_prompt,
            tools=tools,
            agent=agent,
            runnable_config=runnable_config,
            agent_input=agent_input,
            tool_stats=tool_stats,
            total_timeout=retry_cfg.total_timeout,
            middleware_count=len(middleware),
            callback_count=len(callbacks),
        )

    async def process_with_agent(
        self,
        user_content: str,
        system_prompt: str,
        llm_config: dict[str, Any],
        user_id: str,
        thread_id: str,
        agent_id: str | None = None,
        agent_config: Any = None,
        image_datas: list[dict[str, Any]] | None = None,
        attachment_infos: list[Any] | None = None,
        history_messages: list[BaseMessage] | None = None,
        prompt_sections: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """使用LangChain Agent处理请求.

        Args:
            user_content: 构建好的用户内容(包含记忆上下文)
            system_prompt: 基础系统提示词(base 段)
            llm_config: LLM配置
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID (可选)
            image_datas: 图片数据列表 (可选)
            attachment_infos: 附件描述列表 (可选, 用于非视觉模型降级)
            history_messages: 历史轮次 message 列表 (可选, 历史 messages 透传).
                非空时构建为 [*history, current_msg], 为空或 None 时走老路径 [current_msg].
            prompt_sections: 除 base 外的命名段 (如 memory), 由装配器按声明顺序拼接.

        Returns:
            tuple[str, dict]: (AI响应内容, 推理统计信息)

        """
        logger.info(
            f"🤖 AI推理协调器处理请求: {user_content[:50]}... (用户ID: {user_id}, 线程ID: {thread_id})",
        )

        start_time = datetime.now()
        stats: dict[str, Any] = {}

        try:
            setup = await self._build_agent_and_config(
                user_content,
                system_prompt,
                llm_config,
                user_id,
                thread_id,
                agent_id,
                agent_config,
                image_datas,
                attachment_infos,
                history_messages,
                prompt_sections,
                streaming=False,
            )
            stats["tool_stats"] = setup.tool_stats

            logger.debug("🚀 执行LangChain agent...")
            try:
                logger.info("🚀 使用LangChain Agent处理请求")

                # 捕获完整的prompt内容(在传入LangChain之前)
                self._capture_prompt(
                    user_content=user_content,
                    system_prompt=setup.system_prompt,
                    user_id=user_id,
                    thread_id=thread_id,
                    agent_id=agent_id or "unknown",
                    metadata={
                        "model": setup.llm_model,
                        "total_tools": len(setup.tools),
                        "middleware_count": setup.middleware_count,
                        "callback_count": setup.callback_count,
                        "history_messages_count": (
                            len(history_messages) if history_messages else 0
                        ),
                        "processing_start": start_time.isoformat(),
                    },
                    history_messages=history_messages,
                )

                logger.debug(
                    f"🔧 执行配置包含callbacks: {setup.callback_count}",
                )
                agent_result = await asyncio.wait_for(
                    setup.agent.ainvoke(
                        setup.agent_input, config=setup.runnable_config
                    ),
                    timeout=setup.total_timeout,
                )

                # 从agent结果中提取响应
                result = self._extract_agent_result(agent_result)

                # 如果响应为空,提供一个默认响应
                if not result or not result.strip():
                    fallback = self._generate_tool_fallback_response(agent_result)
                    if fallback:
                        result = fallback
                        logger.warning("🔍 Agent返回空响应,已基于工具结果生成默认回复")
                    else:
                        result = "返回响应为空"
                        logger.warning("🔍 Agent返回空响应,使用默认回复")

                # 提取token使用信息
                total_tokens, response_tokens = self._extract_token_usage(agent_result)

            except TimeoutError:
                # TimeoutError应该传播到外层,让调用者处理
                raise
            except Exception as e:
                logger.error("❌ 处理失败: %s", e)
                # 最终降级到简单响应
                result = f"处理请求时遇到问题: {e!s}"

            # 更新统计信息
            processing_time = (datetime.now() - start_time).total_seconds()
            stats.update({
                "processing_time": processing_time,
                "agent_id": agent_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "total_tokens": total_tokens if "total_tokens" in locals() else 0,
                "response_tokens": response_tokens
                if "response_tokens" in locals()
                else 0,
            })

            logger.info("✅ AI推理协调器完成, 耗时 %.2fs", processing_time)
            return result, stats

        except Exception as e:
            # 简化异常处理 - 让异常直接传播到上层,中间件会统一处理
            logger.error("❌ AI推理协调器失败: %s", e)
            raise RuntimeError(f"AI推理协调器执行失败: {e}") from e

    def _build_runnable_config(
        self,
        callbacks: list,
        user_id: str,
        thread_id: str,
        agent_id: str | None,
    ) -> RunnableConfig:
        """构建RunnableConfig."""
        runnable_config = RunnableConfig()

        if callbacks:
            # 添加用户和会话信息到metadata中,以便回调处理器使用
            metadata_for_callbacks = {
                "user_id": user_id,
                "session_id": thread_id,
                "agent_id": agent_id,
            }

            # 为每个回调处理器设置metadata
            for callback in callbacks:
                if hasattr(callback, "set_metadata"):
                    callback.set_metadata(metadata_for_callbacks)

            # 正确设置RunnableConfig的callbacks和metadata
            runnable_config = RunnableConfig(
                callbacks=callbacks,
                metadata=metadata_for_callbacks,
            )

        return runnable_config

    def _ensure_callbacks_in_config(
        self,
        runnable_config: RunnableConfig,
        callbacks: list,
    ) -> RunnableConfig:
        """确保callbacks正确传递到runnable_config中."""
        runnable_config = runnable_config.copy()
        existing_callbacks = runnable_config.get("callbacks", [])
        if isinstance(existing_callbacks, list):
            runnable_config["callbacks"] = existing_callbacks + callbacks
        else:
            runnable_config["callbacks"] = callbacks
        return runnable_config

    def _build_human_message(
        self,
        user_content: str,
        llm_model: str,
        image_datas: list[dict[str, Any]] | None = None,
        attachment_infos: list[Any] | None = None,
    ) -> HumanMessage:
        """构建HumanMessage, 根据模型能力自动选择多模态或纯文本.

        Args:
            user_content: 用户文本内容(含记忆上下文)
            llm_model: 模型ID
            image_datas: 图片数据列表
            attachment_infos: 附件描述列表(用于非视觉模型降级)

        Returns:
            HumanMessage实例

        """
        if not image_datas:
            return HumanMessage(content=user_content)

        from src.inference.llm.definitions.model_registry import get_model

        model_meta = get_model(llm_model)
        if model_meta is not None and model_meta.supports_multimodal():
            return HumanMessage(
                content=self._build_multimodal_content(
                    user_content, image_datas, attachment_infos, llm_model
                )
            )
        return HumanMessage(
            content=self._build_text_fallback_content(
                user_content, attachment_infos, llm_model
            )
        )

    def _build_multimodal_content(
        self,
        user_content: str,
        image_datas: list[dict[str, Any]],
        attachment_infos: list[Any] | None,
        llm_model: str,
    ) -> list[dict[str, Any]]:
        """多模态路径: 图片 base64 直传视觉模型, 显式附上附件 ID 供引用.

        image_datas 与 attachment_infos 一一对应 (prepare_image_attachments).
        视觉模型可从 image_url 看到图片, 但无法获知附件 ID, 故显式附上 ID,
        使模型能正确引用当前轮次图片 (read_file 等).

        """
        import base64 as b64mod

        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": user_content},
        ]
        for idx, img in enumerate(image_datas):
            b64_str = b64mod.b64encode(img["data"]).decode("utf-8")
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime_type']};base64,{b64_str}"},
            })
            att_id = (
                getattr(attachment_infos[idx], "file_id", None)
                if attachment_infos and idx < len(attachment_infos)
                else None
            )
            if att_id:
                content_blocks.append({"type": "text", "text": f"[file: {att_id}]"})
        logger.info("🖼️ 多模态消息: %s 张图片直传模型 %s", len(image_datas), llm_model)
        return content_blocks

    def _build_text_fallback_content(
        self,
        user_content: str,
        attachment_infos: list[Any] | None,
        llm_model: str,
    ) -> str:
        """文本降级路径: 非视觉模型用 attachment_infos + .desc.md 拼接图片描述文本."""
        from src.core.context import get_user_context_or_none
        from src.files.desc_writer import read_desc

        ctx = get_user_context_or_none()
        user_id = ctx.user_id if ctx else ""

        fallback_content = user_content
        if attachment_infos:
            parts = []
            for att in attachment_infos:
                if hasattr(att, "file_type") and att.file_type == "image":
                    file_id = getattr(att, "file_id", None)
                    desc = (
                        read_desc(user_id, file_id) if file_id and user_id else None
                    ) or "图片"
                    url = getattr(att, "internal_path", "")
                    if file_id:
                        parts.append(f"[file: {file_id}] [img: {url} - {desc}]")
                    else:
                        parts.append(f"[img: {url} - {desc}]")
            if parts:
                fallback_content = f"{user_content} {' '.join(parts)}"
        logger.info("📝 纯文本消息: 模型 %s 不支持视觉, 使用文本描述", llm_model)
        return fallback_content

    async def _downgrade_history_images_for_text_model(
        self,
        history_messages: list[BaseMessage] | None,
        llm_model: str,
        user_id: str,
        thread_id: str,
        agent_id: str | None,
    ) -> list[BaseMessage] | None:
        """非多模态模型下, 把历史 image_url 块降级为文本占位.

        simple 模式前端透传的历史可能含 base64 image_url 块(Open WebUI 全量
        回传), 纯文本模型无法处理. 这里把 image_url 块替换为 [图片: brief]
        文本, brief 通过 base64 → content_hash 反查 attachment_registry 获取.

        多模态模型直接原样返回; 无图片的历史也原样返回(零开销).
        local 模式天然免疫(DB 重组历史为纯文本, 无 image_url 块).
        """
        if not history_messages:
            return history_messages

        from src.inference.llm.definitions.model_registry import get_model

        model_meta = get_model(llm_model)
        if model_meta is not None and model_meta.supports_multimodal():
            return history_messages

        if not _history_has_image_blocks(history_messages):
            return history_messages

        service = await self._create_attachment_service_safely(
            user_id, thread_id, agent_id
        )

        new_messages: list[BaseMessage] = []
        for msg in history_messages:
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                new_messages.append(msg)
                continue
            new_blocks: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    placeholder = await self._image_url_to_text_placeholder(
                        block, service
                    )
                    new_blocks.append({"type": "text", "text": placeholder})
                else:
                    new_blocks.append(block)
            new_messages.append(msg.model_copy(update={"content": new_blocks}))

        logger.info(
            "🖼️ 历史图片降级: %d 条消息, 模型 %s 为纯文本",
            len(new_messages),
            llm_model,
        )
        return new_messages

    async def _create_attachment_service_safely(
        self,
        user_id: str,
        thread_id: str,  # noqa: ARG002
        agent_id: str | None,  # noqa: ARG002
    ) -> Any:
        """创建 file_registry_service, 失败返回 None(降级退化为纯占位)."""
        try:
            from src.storage.service.file_registry_service import (
                create_file_registry_service,
            )

            return await create_file_registry_service(user_id)
        except Exception as e:
            logger.warning(
                "创建 file_registry_service 失败, 图片降级退化为纯占位: %s",
                e,
            )
            return None

    async def _image_url_to_text_placeholder(
        self,
        block: dict[str, Any],
        service: Any,
    ) -> str:
        """把单个 image_url 块转为 [图片: brief] 文本占位.

        反查失败/非 data URI/描述为空时回退为 [图片].
        """
        image_url = block.get("image_url")
        if not isinstance(image_url, dict):
            return "[图片]"
        url = image_url.get("url", "")
        if not url:
            return "[图片]"

        image_bytes = _decode_data_uri(url)
        if image_bytes is None:
            return "[图片]"

        from src.files.hash_utils import compute_hash

        content_hash = compute_hash(image_bytes)
        if service is not None:
            try:
                entry = await service.find_by_content_hash(content_hash)
                if entry and entry.brief:
                    return f"[图片: {entry.brief}]"
            except Exception as e:
                logger.debug(
                    "反查附件描述失败 hash=%s: %s",
                    content_hash[:8],
                    e,
                )
        return "[图片]"

    async def _restore_file_markers_in_history(
        self,
        history_messages: list[BaseMessage] | None,
        user_id: str,
    ) -> list[BaseMessage] | None:
        """把历史里的签名下载 URL 还原为 [file: id] 标记.

        simple 模式前端透传的历史含 build_file_links 追加的签名 URL (markdown
        链接), 跨轮后 URL 会过期, 且 wechat_publish 等工具只认 [file: id] 标记.
        此处扫描 URL 解析明文 file_id 路径段, 反查 FileRegistry 验证有效后
        替换为 [file: fid] label, 让模型在后续轮次能正确引用历史文件.

        local 模式历史为纯文本无签名 URL, 正则不匹配, 零副作用.
        """
        if not history_messages:
            return history_messages

        # 1. 扫描收集所有签名 URL 的 file_id -> label
        fid_labels: dict[str, str] = {}

        def _scan(text: str) -> None:
            for m in _SIGNED_FILE_URL_RE.finditer(text):
                fid_labels.setdefault(m.group("fid"), m.group("label"))

        for msg in history_messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                _scan(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        _scan(block.get("text", ""))

        if not fid_labels:
            return history_messages  # 无签名 URL, 零改动

        # 2. 批量查表验证 file_id 有效性 (FileRegistry 与物理文件同生命周期)
        service = await self._create_attachment_service_safely(user_id, None, None)
        valid_fids: dict[str, str] = {}
        if service is not None:
            for fid, label in fid_labels.items():
                try:
                    if await service.get(fid) is not None:
                        valid_fids[fid] = label
                except Exception as e:
                    logger.debug("反查 file_id=%s 失败: %s", fid, e)
        if not valid_fids:
            return history_messages  # 无有效文件, 保留原 URL

        # 3. 替换匹配的 markdown 链接为 [file: fid] label
        def _replace(match: re.Match[str]) -> str:
            fid = match.group("fid")
            if fid not in valid_fids:
                return match.group(0)
            label = valid_fids[fid] or fid
            return f"[file: {fid}] {label}"

        new_messages: list[BaseMessage] = []
        changed = False
        for msg in history_messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                new_text = _SIGNED_FILE_URL_RE.sub(_replace, content)
                if new_text != content:
                    changed = True
                    new_messages.append(
                        msg.model_copy(update={"content": new_text}),
                    )
                    continue
            elif isinstance(content, list):
                new_blocks: list[dict[str, Any]] = []
                block_changed = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        new_text = _SIGNED_FILE_URL_RE.sub(_replace, text)
                        if new_text != text:
                            block_changed = True
                            new_blocks.append({"type": "text", "text": new_text})
                            continue
                    new_blocks.append(block)
                if block_changed:
                    changed = True
                    new_messages.append(
                        msg.model_copy(update={"content": new_blocks}),
                    )
                    continue
            new_messages.append(msg)

        if changed:
            logger.info(
                "📎 历史签名URL还原为[file: id]标记: %d个文件",
                len(valid_fids),
            )
        return new_messages

    @staticmethod
    def _strip_tool_artifacts_in_history(
        history_messages: list[BaseMessage] | None,
    ) -> list[BaseMessage] | None:
        """剥离历史 assistant 消息中的工具调用渲染标记.

        simple 模式前端透传的历史含 <details type="tool_calls"> 标签
        (format_tool_call_done 泄漏或 LLM 文本模仿) 和 DSML 标记
        (DeepSeek 原生工具调用泄漏). 这些标记引导 LLM 用文本格式
        替代标准 function calling, 破坏 ReAct 循环.

        local 模式历史为纯文本(DB 重组), 正则不匹配, 零副作用.
        """
        if not history_messages:
            return history_messages

        new_messages: list[BaseMessage] = []
        changed = False
        for msg in history_messages:
            if not isinstance(msg, AIMessage):
                new_messages.append(msg)
                continue
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                new_text = _strip_tool_artifacts_text(content)
                if new_text != content:
                    changed = True
                    new_messages.append(
                        msg.model_copy(update={"content": new_text}),
                    )
                    continue
            elif isinstance(content, list):
                block_changed = False
                new_blocks: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        new_text = _strip_tool_artifacts_text(text)
                        if new_text != text:
                            block_changed = True
                            new_blocks.append({"type": "text", "text": new_text})
                            continue
                    new_blocks.append(block)
                if block_changed:
                    changed = True
                    new_messages.append(
                        msg.model_copy(update={"content": new_blocks}),
                    )
                    continue
            new_messages.append(msg)

        if changed:
            logger.debug("🧹 历史清洗: 剥离工具调用渲染标记")
        return new_messages if changed else history_messages

    def _extract_agent_result(self, agent_result: Any) -> str:
        """从Agent结果中提取响应内容."""
        if isinstance(agent_result, dict) and "messages" in agent_result:
            messages = agent_result["messages"]
            if messages:
                last_message = messages[-1]
                if hasattr(last_message, "content"):
                    return self._strip_think_tags(
                        content_to_text(last_message.content),
                    )
                return str(last_message)
            return "Agent处理完成,但没有返回内容"
        return str(agent_result)

    @staticmethod
    def _strip_think_tags(content: str) -> str:
        """移除LLM返回的 think/thinking 标签及其内容.

        已迁移到 src.inference.llm.response_utils.strip_think_tags,
        此处保留 staticmethod 以兼容现有调用和测试.
        """
        return _strip_think_tags_impl(content)

    def _generate_tool_fallback_response(self, agent_result: Any) -> str | None:
        """当LLM调用工具后返回空响应时,基于工具结果生成兜底回复.

        从agent_result.messages中提取最后一次tool_calls及对应ToolMessage输出,
        按工具类型生成用户可读的成功/失败摘要.无工具调用时返回None,让外层
        回退到通用空响应文案.
        """
        if not isinstance(agent_result, dict):
            return None

        messages = agent_result.get("messages")
        if not messages:
            return None

        # 找到最后一个带tool_calls的AIMessage
        last_tool_call_msg: AIMessage | None = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_tool_call_msg = msg
                break

        if not last_tool_call_msg:
            return None

        # 收集ToolMessage结果,按tool_call_id索引
        tool_results: dict[str, str] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
                tool_results[msg.tool_call_id] = msg.content

        summaries: list[str] = []
        for tc in last_tool_call_msg.tool_calls:
            name = tc.get("name", "工具")
            tool_call_id = tc.get("id", "")
            result_content = tool_results.get(tool_call_id, "")
            summary = self._summarize_tool_result(name, result_content)
            if summary:
                summaries.append(summary)

        return "\n".join(summaries) if summaries else None

    def _summarize_tool_result(
        self,
        tool_name: str,
        result_content: str,
    ) -> str | None:
        """将单个工具结果解析为用户可读的一句话摘要."""
        try:
            result = json.loads(result_content)
        except (json.JSONDecodeError, TypeError):
            text = str(result_content).strip()
            if not text:
                return None
            return f"已调用 {tool_name},结果:{text[:200]}"

        if not isinstance(result, dict):
            text = str(result).strip()
            if not text:
                return None
            return f"已调用 {tool_name},结果:{text[:200]}"

        success = result.get("success", True)
        message = result.get("message", "")
        error = result.get("error", "")

        # 工具特定处理
        if tool_name in (
            "schedule_message",
            "list_scheduled_messages",
            "cancel_scheduled_message",
        ):
            if success and message:
                return f"✅ {message}"
            return f"❌ 定时消息操作失败:{error or '未知错误'}"

        if tool_name in ("create_todo", "list_todos", "update_todo", "delete_todo"):
            if success and message:
                return f"✅ {message}"
            return f"❌ 待办操作失败:{error or '未知错误'}"

        if tool_name == "python_executor":
            if success:
                stdout = result.get("stdout", "")
                if stdout:
                    return f"✅ 计算完成:\n```\n{stdout.strip()[:500]}\n```"
                return "✅ 代码执行完成,无输出."
            stderr = result.get("stderr", "")
            return f"❌ 代码执行失败:{stderr[:200] or error or '未知错误'}"

        if tool_name in (
            "export_document",
            "generate_image",
            "mermaid_chart",
            "vega_chart",
            "markmap_chart",
        ):
            if success and message:
                return f"✅ {message}"
            return f"❌ 文件生成失败:{error or '未知错误'}"

        if tool_name == "read_file":
            if success and message:
                return f"✅ {message}"
            return f"❌ 文件描述读取失败:{error or '未知错误'}"

        # 通用兜底
        if success and message:
            return f"✅ 已调用 {tool_name}:{message}"
        if success:
            return f"✅ {tool_name} 调用成功."
        return f"❌ {tool_name} 调用失败:{error or '未知错误'}"

    def _extract_token_usage(self, agent_result: Any) -> tuple[int, int]:
        """提取token使用信息."""
        try:
            # 记录agent_result的实际结构以便调试
            if isinstance(agent_result, dict):
                logger.debug("🔍 Agent结果结构: %s", list(agent_result.keys()))

                # 依次尝试 usage / token_usage / agent_result.token_usage 三种来源
                pair = self._extract_usage_pair(agent_result.get("usage"), "usage")
                if pair is not None:
                    return pair
                pair = self._extract_usage_pair(
                    agent_result.get("token_usage"), "token_usage"
                )
                if pair is not None:
                    return pair
                pair = self._extract_usage_pair(
                    getattr(agent_result, "token_usage", None),
                    "agent_result.token_usage",
                )
                if pair is not None:
                    return pair

                result_str = str(agent_result)
                total_tokens, response_tokens = self._estimate_tokens_from_result(
                    result_str,
                )
                logger.debug(
                    "🔍 估算token使用: 总计=%s, 响应=%s",
                    total_tokens,
                    response_tokens,
                )
                return total_tokens, response_tokens

            logger.debug("🔍 Agent结果类型: %s", type(agent_result))
            result_str = str(agent_result)
            total_tokens, response_tokens = self._estimate_tokens_from_result(
                result_str,
            )
            logger.debug(
                "🔍 从字符串估算token: 总计=%s, 响应=%s",
                total_tokens,
                response_tokens,
            )
            return total_tokens, response_tokens

        except (KeyError, AttributeError, TypeError, ValueError) as token_error:
            logger.warning("⚠️ 提取token使用信息失败: %s", token_error)
            return 0, 0

    @staticmethod
    def _extract_usage_pair(
        usage_dict: Any,
        source_label: str,
    ) -> tuple[int, int] | None:
        """从单个 usage dict 提取 (total_tokens, completion_tokens).

        usage / token_usage / agent_result.token_usage 三种来源共用. 非 dict 或
        缺字段时返回 None, 由调用方落到下一来源或估算兜底.

        """
        if not isinstance(usage_dict, dict):
            return None
        total_tokens = usage_dict.get("total_tokens", 0)
        response_tokens = usage_dict.get("completion_tokens", 0)
        logger.debug(
            "🔍 从%s提取token: 总计=%s, 响应=%s",
            source_label,
            total_tokens,
            response_tokens,
        )
        return total_tokens, response_tokens

    async def process_with_agent_stream(
        self,
        user_content: str,
        system_prompt: str,
        llm_config: dict[str, Any],
        user_id: str,
        thread_id: str,
        agent_id: str | None = None,
        agent_config: Any = None,
        image_datas: list[dict[str, Any]] | None = None,
        attachment_infos: list[Any] | None = None,
        history_messages: list[BaseMessage] | None = None,
        prompt_sections: dict[str, str] | None = None,
    ) -> AsyncIterator[str | StreamContent]:
        """使用LangChain Agent处理请求(流式响应).

        Args:
            user_content: 构建好的用户内容(包含记忆上下文)
            system_prompt: 基础系统提示词(base 段)
            llm_config: LLM配置
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID (可选)
            image_datas: 图片数据列表 (可选)
            attachment_infos: 附件描述列表 (可选, 用于非视觉模型降级)
            agent_config: Agent配置 (可选)
            history_messages: 历史轮次 message 列表 (可选, 历史 messages 透传).
            prompt_sections: 除 base 外的命名段 (如 memory), 由装配器按声明顺序拼接.

        Yields:
            AI响应文本片段或 StreamContent (工具调用 HTML, display_only=True)

        Raises:
            RuntimeError: 处理失败时抛出异常

        """
        logger.info(
            f"🌊 AI推理协调器流式处理请求: {user_content[:50]}... (用户ID: {user_id}, 线程ID: {thread_id})",
        )

        start_time = datetime.now()

        try:
            setup = await self._build_agent_and_config(
                user_content,
                system_prompt,
                llm_config,
                user_id,
                thread_id,
                agent_id,
                agent_config,
                image_datas,
                attachment_infos,
                history_messages,
                prompt_sections,
                streaming=True,
            )

            # 捕获prompt内容(与非流式相同)
            self._capture_prompt(
                user_content=user_content,
                system_prompt=setup.system_prompt,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id or "unknown",
                metadata={
                    "model": setup.llm_model,
                    "total_tools": len(setup.tools),
                    "streaming": True,
                    "history_messages_count": (
                        len(history_messages) if history_messages else 0
                    ),
                    "processing_start": start_time.isoformat(),
                },
                history_messages=history_messages,
            )

            # 总时长限制 — 在供应商硬超时(360s)前主动中断
            async with asyncio.timeout(setup.total_timeout):
                try:
                    logger.info("🌊 使用LangChain Agent流式处理请求")

                    # 工具调用显示配置
                    tool_display = self._is_tool_call_display_enabled()
                    state = _StreamState()

                    # 使用 astream() + stream_mode="messages" 进行token级流式处理
                    async for chunk in setup.agent.astream(
                        setup.agent_input,
                        config=setup.runnable_config,
                        stream_mode="messages",
                    ):
                        # 解包 (message, metadata) 元组
                        if not isinstance(chunk, tuple) or len(chunk) != 2:
                            continue
                        message, metadata = chunk
                        for item in self._process_stream_chunk(
                            message, metadata, state, tool_display
                        ):
                            yield item

                    # 记录完成时间
                    processing_time = (datetime.now() - start_time).total_seconds()
                    logger.info(
                        f"✅ AI推理协调器流式处理完成, 耗时 {processing_time:.2f}s"
                    )

                except TimeoutError:
                    raise
                except Exception as e:
                    logger.error("❌ 流式处理失败: %s", e)
                    raise RuntimeError(f"流式处理失败: {e}") from e

        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.error(
                f"❌ AI推理协调器流式处理失败: {e}, 耗时 {processing_time:.2f}s",
            )
            raise RuntimeError(f"AI推理协调器流式执行失败: {e}") from e

    def _process_stream_chunk(
        self,
        message: Any,
        metadata: Any,
        state: _StreamState,
        tool_display: bool,
    ) -> list[str | StreamContent]:
        """处理单个流式 chunk, 原地更新 state, 返回待 yield 项列表.

        主循环解包 (message, metadata) 元组后调用本方法. 涵盖: ToolMessage →
        done HTML / 来源过滤 / tool_call_chunks 累积 / tool_calls 后备 /
        文本提取 + think 标签过滤. 返回空列表表示该 chunk 无输出.

        Args:
            message: 已解包的 chunk message (AIMessageChunk/ToolMessage/其他).
            metadata: 已解包的 chunk metadata (含 langgraph_node 来源).
            state: 跨 chunk 可变状态, 原地修改.
            tool_display: 是否输出工具调用 done HTML.

        Returns:
            待 yield 的 str / StreamContent 列表 (可能为空).

        """
        outputs: list[str | StreamContent] = []

        if isinstance(message, ToolMessage):
            # 工具执行完成 → 发送 done 标签
            if tool_display and message.tool_call_id in state.pending_tool_calls:
                info = state.pending_tool_calls.pop(message.tool_call_id)
                # 清理 chunk_index_to_id 中已完成的映射
                state.chunk_index_to_id = {
                    k: v
                    for k, v in state.chunk_index_to_id.items()
                    if v in state.pending_tool_calls
                }
                args = info.get("args") or self._try_parse_accumulated_args(
                    info.get("raw_args", ""),
                )
                html = format_tool_call_done(
                    info["name"],
                    str(message.content),
                    args,
                )
                outputs.append(StreamContent(html, display_only=True))
            return outputs

        if not isinstance(message, AIMessageChunk):
            return outputs

        # 过滤工具内部嵌套 LLM 输出: 仅放行主 LLM (model 节点) 的纯文本.
        # langgraph stream_mode="messages" 会输出 graph 内所有 chat model 的 token
        # (含 _llm_tool_filter / 专家工具 Gemini 等工具内部 LLM), 通过
        # langgraph_node 区分来源. metadata 缺失(None)时放行, 兼容非标准环境,
        # 避免静默切断主输出. (回调级隔离对 langgraph 协议事件无效, 故在此过滤.)
        if metadata.get("langgraph_node") not in (None, "model"):
            return outputs

        # 检测 tool_call_chunks → 记录 name + 累积 args + 发送 start 标签
        chunk_has_tool_call = False
        if hasattr(message, "tool_call_chunks") and message.tool_call_chunks:
            chunk_has_tool_call = True
            for tc_chunk in message.tool_call_chunks:
                tc_id = tc_chunk.get("id")
                # 有 id 的是新工具调用的首个 chunk
                if tc_id and tc_id not in state.seen_tool_call_ids:
                    state.seen_tool_call_ids.add(tc_id)
                    tc_index = tc_chunk.get("index", 0)
                    state.chunk_index_to_id[tc_index] = tc_id
                    tool_name = tc_chunk.get("name") or "unknown"
                    state.pending_tool_calls[tc_id] = {
                        "name": tool_name,
                        "raw_args": tc_chunk.get("args") or "",
                    }
                    # 只发送 done 标签, 不发送 start 标签
                # 累积后续 chunk 的 args 片段 (通过 index 匹配)
                elif tc_chunk.get("args"):
                    tc_index = tc_chunk.get("index")
                    target_id = (
                        state.chunk_index_to_id.get(tc_index)
                        if tc_index is not None
                        else None
                    )
                    if target_id and target_id in state.pending_tool_calls:
                        state.pending_tool_calls[target_id]["raw_args"] += tc_chunk[
                            "args"
                        ]

        # 也检测完全组装的 tool_calls 作为后备
        if hasattr(message, "tool_calls") and message.tool_calls:
            chunk_has_tool_call = True
            for tc in message.tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id in state.pending_tool_calls:
                    tc_args = tc.get("args")
                    if tc_args and tc_args != {}:
                        state.pending_tool_calls[tc_id]["args"] = tc_args

        # 提取文本内容
        content = self._extract_text_from_chunk(message)
        if not content:
            return outputs
        # 跳过含工具调用的 chunk 文本 (中间过程, 非最终响应)
        if chunk_has_tool_call:
            return outputs
        # 过滤 <think/> 标签内容
        filtered = self._filter_think_tags_streaming(
            content,
            state.in_think_block,
            state.think_buffer,
        )
        if isinstance(filtered, tuple):
            state.in_think_block, state.think_buffer = filtered
            return outputs
        content = filtered
        if not content:
            return outputs
        outputs.append(content)
        return outputs

    @staticmethod
    def _try_parse_accumulated_args(raw_args: str) -> dict:
        """尝试从累积的 tool_call_chunks args 字符串解析为 dict."""
        if not raw_args:
            return {}
        try:
            import json

            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {}

    def _extract_text_from_chunk(self, message_chunk: AIMessageChunk) -> str | None:
        """从 AIMessageChunk 中提取文本内容.

        Args:
            message_chunk: 已确认类型的 AIMessageChunk

        Returns:
            文本内容字符串, 如果无法提取则返回 None

        """
        try:
            if not hasattr(message_chunk, "content") or not message_chunk.content:
                return None
            text = content_to_text(message_chunk.content)
            return text or None
        except (TypeError, AttributeError, ValueError) as e:
            logger.warning(
                "⚠️ 提取文本内容失败: %s, chunk类型: %s", e, type(message_chunk)
            )
            return None

    @staticmethod
    def _is_tool_call_display_enabled() -> bool:
        """检查工具调用显示是否启用."""
        from src.config.api_config import get_config

        return get_config().tool_call_display.enable

    @staticmethod
    def _filter_think_tags_streaming(
        content: str,
        in_think_block: bool,
        think_buffer: str,
    ) -> str | tuple[bool, str]:
        """流式路径的 think/thinking 标签过滤.

        已迁移到 src.inference.llm.response_utils.filter_think_tags_streaming,
        此处保留 staticmethod 以兼容现有调用和测试.
        """
        return _filter_think_tags_streaming_impl(content, in_think_block, think_buffer)

    @staticmethod
    def _enable_tool_error_handling(agent: Any) -> None:
        """启用工具错误容错, 委托给共享工具函数."""
        enable_tool_error_handling(agent)

    @staticmethod
    def _get_tool_tracker() -> Any:
        """获取工具调用追踪器, 仅DEBUG模式下启用.

        延迟导入scripts.debug模块, 生产环境不需要该模块.
        """
        if not is_debug_enabled():
            return None
        try:
            from scripts.debug.tool_call_tracker import create_tool_call_tracker

            return create_tool_call_tracker()
        except ImportError:
            logger.warning("ToolCallTracker需要scripts.debug模块, 但无法导入")
            return None

    @staticmethod
    def _capture_prompt(**kwargs: Any) -> None:
        """捕获prompt内容, 仅DEBUG模式下启用.

        延迟导入scripts.debug模块, 生产环境不需要该模块.
        """
        if not is_debug_enabled():
            return
        try:
            from scripts.debug.prompt_capture import capture_prompt

            capture_prompt(**kwargs)
        except ImportError:
            logger.warning("PromptCapture需要scripts.debug模块, 但无法导入")

    def _create_llm(
        self,
        llm_model: str,
        llm_config: dict[str, Any],
    ) -> Any:
        """创建LLM实例.

        将 agent.yaml 中 llm_config 除 model 外的构造级参数透传给 create_llm,
        使 num_ctx 等 SDK 原生参数能够覆盖模型元数据默认值.
        """
        construction_params = {
            k: v for k, v in llm_config.items() if k not in ("model",)
        }
        construction_params.setdefault("streaming", False)
        return create_llm(llm_model, **construction_params)

    @staticmethod
    def _estimate_tokens_from_result(result_str: str) -> tuple[int, int]:
        """从结果字符串估算token使用量."""
        estimated_tokens = int(len(result_str) * 0.7)
        return max(estimated_tokens, 20), max(estimated_tokens - 10, 10)
