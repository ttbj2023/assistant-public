"""推理协调器单元测试.

测试职责: 验证InferenceCoordinator的核心功能逻辑
测试范围: LangChain Agent创建、工具集管理、模型推理协调
Mock策略: Mock外部依赖（LLM、嵌入模型、外部工具），保留协调逻辑
测试价值: 确保推理协调器的稳定性和可靠性
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agent.processors.inference_coordinator import InferenceCoordinator
from tests.decorators import quick_test


class TestInferenceCoordinator:
    """InferenceCoordinator核心测试."""

    @pytest.fixture
    def mock_config(self) -> dict:
        """创建Mock配置."""
        return {
            "llm": {
                "model": "local:qwen3.5:9b",
                "temperature": 0.7,
                "max_tokens": 4000,
            },
            "cache": {"ttl": 3600},
        }

    @pytest.fixture
    def inference_coordinator(self, mock_config: dict) -> InferenceCoordinator:
        """创建InferenceCoordinator实例."""
        return InferenceCoordinator(mock_config)

    @pytest.mark.asyncio
    @quick_test
    async def test_create_toolset_success(
        self, inference_coordinator: InferenceCoordinator
    ) -> None:
        """测试成功创建工具集."""
        with patch(
            "src.agent.processors.inference_coordinator.get_tools_manager"
        ) as mock_get_manager:
            mock_manager = Mock()

            # 创建更真实的Mock工具对象
            from langchain_core.tools import StructuredTool

            def dummy_tool_func(input_str: str) -> str:
                return f"Processed: {input_str}"

            mock_tool1 = StructuredTool.from_function(
                dummy_tool_func,
                name="dummy_tool_1",
                description="A dummy tool for testing",
            )
            mock_tool2 = StructuredTool.from_function(
                dummy_tool_func,
                name="dummy_tool_2",
                description="Another dummy tool for testing",
            )
            mock_tools = [mock_tool1, mock_tool2]

            # 更新：InferenceCoordinator现在调用create_tools方法
            mock_manager.create_tools = AsyncMock(return_value=mock_tools)
            mock_get_manager.return_value = mock_manager

            # 模拟agent配置
            class MockAgentConfig:
                def __init__(self):
                    self.tools = ["create_todo", "search_memories"]
                    self.agent_id = "test-agent"

            (
                tools,
                tool_stats,
                discovery_middleware,
                prompt_hints,
                skill_load_middleware,
                skill_l1_manifest,
            ) = await inference_coordinator.create_toolset(
                "test_user", "test_thread", MockAgentConfig()
            )

            assert len(tools) == 2
            assert tool_stats["total_tools"] == 2
            assert discovery_middleware is None
            # MockAgentConfig无skills属性, skills装配不触发
            assert skill_load_middleware is None
            assert skill_l1_manifest == ""
            assert isinstance(prompt_hints, str)
            mock_get_manager.assert_called_once()
            mock_manager.create_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_toolset_manager_failure(
        self, inference_coordinator: InferenceCoordinator
    ) -> None:
        """测试工具管理器获取失败."""
        with patch(
            "src.agent.processors.inference_coordinator.get_tools_manager",
            side_effect=Exception("Manager error"),
        ):
            (
                tools,
                tool_stats,
                discovery_middleware,
                _prompt_hints,
                _skill_load_middleware,
                _skill_l1_manifest,
            ) = await inference_coordinator.create_toolset("test_user", "test_thread")
            # 应该返回空工具集和错误信息而不是抛出异常
            assert tools == []
            assert "error" in tool_stats

    @pytest.mark.asyncio
    @quick_test
    async def test_create_toolset_empty_tools(
        self, inference_coordinator: InferenceCoordinator
    ) -> None:
        """测试工具集为空."""
        with patch(
            "src.agent.processors.inference_coordinator.get_tools_manager"
        ) as mock_get_manager:
            mock_manager = Mock()
            # 更新：InferenceCoordinator现在调用create_tools方法
            mock_manager.create_tools = AsyncMock(return_value=[])
            mock_get_manager.return_value = mock_manager

            # 模拟agent配置
            class MockAgentConfig:
                def __init__(self):
                    self.tools = []
                    self.agent_id = "test-agent"

            (
                tools,
                tool_stats,
                discovery_middleware,
                _prompt_hints,
                _skill_load_middleware,
                _skill_l1_manifest,
            ) = await inference_coordinator.create_toolset(
                "test_user", "test_thread", MockAgentConfig()
            )

            assert tools == []
            assert tool_stats["total_tools"] == 0


class TestBuildHumanMessage:
    """测试_build_human_message - 多模态/文本降级选择."""

    @pytest.fixture
    def inference_coordinator(self):
        """创建Mock配置和InferenceCoordinator实例."""
        config = {
            "llm": {
                "model": "local:qwen3.5:9b",
                "temperature": 0.7,
                "max_tokens": 4000,
            },
            "cache": {"ttl": 3600},
        }
        return InferenceCoordinator(config)

    @pytest.fixture
    def mock_multimodal_model(self):
        """模拟支持多模态的模型."""
        model = Mock()
        model.supports_multimodal.return_value = True
        return model

    @pytest.fixture
    def mock_non_multimodal_model(self):
        """模拟不支持多模态的模型."""
        model = Mock()
        model.supports_multimodal.return_value = False
        return model

    @pytest.fixture
    def image_datas(self):
        """模拟图片数据."""
        return [
            {"data": b"fake_image_bytes", "mime_type": "image/jpeg"},
        ]

    @pytest.fixture
    def attachment_infos(self):
        """模拟附件信息."""
        from src.files.models import AttachmentDTO

        return [
            AttachmentDTO(
                file_id="abc12345",
                file_type="image",
                internal_path="files/images/test.jpg",
                filename="test.jpg",
                detail="测试图片描述",
                file_size=100,
                brief="橘猫照片",
                file_format="jpg",
            ),
        ]

    def test_multimodal_model_returns_content_blocks(
        self,
        inference_coordinator,
        mock_multimodal_model,
        image_datas,
        attachment_infos,
    ):
        """多模态模型应返回带 image_url 块 + attachment_id 标注的 HumanMessage."""
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=mock_multimodal_model,
        ):
            result = inference_coordinator._build_human_message(
                user_content="描述这张图片",
                llm_model="local:qwen3.5:9b",
                image_datas=image_datas,
                attachment_infos=attachment_infos,
            )

        from langchain_core.messages import HumanMessage

        assert isinstance(result, HumanMessage)
        assert isinstance(result.content, list)
        # text(user_content) + image_url + text(attachment_id)
        assert len(result.content) == 3
        assert result.content[0]["type"] == "text"
        assert result.content[0]["text"] == "描述这张图片"
        assert result.content[1]["type"] == "image_url"
        assert "data:image/jpeg;base64," in result.content[1]["image_url"]["url"]
        assert result.content[2]["type"] == "text"
        assert result.content[2]["text"] == "[file: abc12345]"

    def test_multimodal_multiple_images_attachment_ids_ordered(
        self,
        inference_coordinator,
        mock_multimodal_model,
    ):
        """多模态模型上传多张图片时, attachment_id 应按序附带在对应 image_url 后."""
        from src.files.models import AttachmentDTO

        multi_image_datas = [
            {"data": b"img_a", "mime_type": "image/png"},
            {"data": b"img_b", "mime_type": "image/jpeg"},
        ]
        multi_attachment_infos = [
            AttachmentDTO(
                file_id="aaa11111",
                file_type="image",
                internal_path="a.png",
                filename="a.png",
                detail="",
                file_size=10,
                brief="",
                file_format="png",
            ),
            AttachmentDTO(
                file_id="bbb22222",
                file_type="image",
                internal_path="b.jpg",
                filename="b.jpg",
                detail="",
                file_size=20,
                brief="",
                file_format="jpg",
            ),
        ]
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=mock_multimodal_model,
        ):
            result = inference_coordinator._build_human_message(
                user_content="对比这两张图",
                llm_model="local:qwen3.5:9b",
                image_datas=multi_image_datas,
                attachment_infos=multi_attachment_infos,
            )

        assert isinstance(result.content, list)
        # text + image_url + id + image_url + id = 5 blocks
        assert len(result.content) == 5
        assert result.content[0]["text"] == "对比这两张图"
        assert result.content[1]["type"] == "image_url"
        assert "image/png" in result.content[1]["image_url"]["url"]
        assert result.content[2]["text"] == "[file: aaa11111]"
        assert result.content[3]["type"] == "image_url"
        assert "image/jpeg" in result.content[3]["image_url"]["url"]
        assert result.content[4]["text"] == "[file: bbb22222]"

    def test_multimodal_no_attachment_infos_safe_fallback(
        self,
        inference_coordinator,
        mock_multimodal_model,
        image_datas,
    ):
        """多模态模型无 attachment_infos 时, 不附带 ID, 行为退回修改前."""
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=mock_multimodal_model,
        ):
            result = inference_coordinator._build_human_message(
                user_content="描述这张图片",
                llm_model="local:qwen3.5:9b",
                image_datas=image_datas,
                attachment_infos=None,
            )

        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[0]["type"] == "text"
        assert result.content[1]["type"] == "image_url"

    def test_non_multimodal_model_returns_text_fallback(
        self,
        inference_coordinator,
        mock_non_multimodal_model,
        image_datas,
        attachment_infos,
    ):
        """非多模态模型应返回包含文本描述的 HumanMessage."""
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=mock_non_multimodal_model,
            ),
            patch(
                "src.files.desc_writer.read_desc",
                return_value="测试图片描述",
            ),
            patch(
                "src.core.context.get_user_context_or_none",
            ) as mock_ctx,
        ):
            mock_ctx.return_value = Mock(user_id="u1")
            result = inference_coordinator._build_human_message(
                user_content="描述这张图片",
                llm_model="local:qwen3:4b-instruct",
                image_datas=image_datas,
                attachment_infos=attachment_infos,
            )

        from langchain_core.messages import HumanMessage

        assert isinstance(result, HumanMessage)
        assert isinstance(result.content, str)
        assert "描述这张图片" in result.content
        assert "[img: files/images/test.jpg - 测试图片描述]" in result.content

    def test_no_images_returns_text_only(
        self,
        inference_coordinator,
        mock_multimodal_model,
    ):
        """无图片时, 即使模型支持多模态也应返回纯文本."""
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=mock_multimodal_model,
        ):
            result = inference_coordinator._build_human_message(
                user_content="你好",
                llm_model="local:qwen3.5:9b",
                image_datas=None,
                attachment_infos=None,
            )

        from langchain_core.messages import HumanMessage

        assert isinstance(result, HumanMessage)
        assert result.content == "你好"

    def test_model_not_found_returns_text_fallback(
        self,
        inference_coordinator,
        image_datas,
        attachment_infos,
    ):
        """模型未注册时应回退到文本降级路径."""
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=None,
            ),
            patch(
                "src.files.desc_writer.read_desc",
                return_value="测试图片描述",
            ),
            patch(
                "src.core.context.get_user_context_or_none",
            ) as mock_ctx,
        ):
            mock_ctx.return_value = Mock(user_id="u1")
            result = inference_coordinator._build_human_message(
                user_content="描述这张图片",
                llm_model="unknown:model",
                image_datas=image_datas,
                attachment_infos=attachment_infos,
            )

        from langchain_core.messages import HumanMessage

        assert isinstance(result, HumanMessage)
        assert isinstance(result.content, str)
        assert "[img:" in result.content
        assert "测试图片描述" in result.content


class TestProcessWithAgentDebugBranch:
    """process_with_agent 的 DEBUG-only 插桩分支回归测试.

    tool_tracker / _capture_prompt 被 is_debug_enabled() 守卫, 常规运行恒为
    no-op, 无任何测试覆盖. 本类钉死 tool_tracker → callbacks → _ensure_callbacks
    → ainvoke config 这条链路, 为提取 _build_agent_and_config (P0-2) 提供安全网.
    """

    @quick_test
    async def test_tool_tracker_propagates_to_agent_config_when_debug(self):
        """DEBUG 模式下 tool_tracker 经 _build_runnable_config 与 _ensure_callbacks 到达 ainvoke 的 config."""
        from langchain_core.messages import AIMessage

        coordinator = InferenceCoordinator(config=None)
        mock_llm = AsyncMock()
        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(
            return_value={"messages": [AIMessage(content="done")]}
        )
        fake_tracker = Mock(name="tool_call_tracker")

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
            patch(
                "src.agent.processors.inference_coordinator.is_debug_enabled",
                return_value=True,
            ),
            patch(
                "scripts.debug.tool_call_tracker.create_tool_call_tracker",
                return_value=fake_tracker,
            ),
            patch.object(
                InferenceCoordinator,
                "_capture_prompt",
            ),
        ):
            result, stats = await coordinator.process_with_agent(
                user_content="hi",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            )

        assert result == "done"
        assert "tool_stats" in stats
        mock_agent.ainvoke.assert_called_once()
        config_arg = mock_agent.ainvoke.call_args.kwargs.get("config")
        assert config_arg is not None, "ainvoke 应收到 config 参数"
        callbacks = config_arg.get("callbacks")
        assert callbacks, "DEBUG 模式下 config.callbacks 不应为空"
        assert fake_tracker in callbacks, (
            "tool_tracker 应进入 ainvoke 的 config.callbacks"
        )
