"""Mock LLM helper for integration tests.

提供统一的Mock LLM集成,简化测试编写。
"""

from unittest.mock import AsyncMock, patch


def create_mock_llm_response_function(response_text: str = "测试响应"):
    """创建Mock LLM响应函数.

    Args:
        response_text: 返回的响应文本

    Returns:
        异步Mock响应函数
    """

    async def mock_llm_response(*args, **kwargs):
        return response_text

    return mock_llm_response


def patch_llm_in_processor(test_case_func):
    """装饰器：为测试函数自动添加LLM Mock.

    Args:
        test_case_func: 测试函数

    Returns:
        包装后的测试函数
    """

    async def wrapper(*args, **kwargs):
        with patch(
            "src.inference.llm.model_loader.create_llm"
        ) as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke = create_mock_llm_response_function("测试LLM响应")
            mock_create_llm.return_value = mock_llm
            return await test_case_func(*args, **kwargs)

    return wrapper


class MockLLMManager:
    """Mock LLM管理器,支持多次调用场景."""

    def __init__(self):
        self.call_count = 0
        self.responses = [
            "让我先搜索您的历史对话记录...",
            "现在让我查看您的任务列表...",
            "最后让我检查您的用户偏好信息...",
            "基于搜索结果、任务列表和偏好信息,我为您提供了综合性的建议。",
        ]

    async def mock_llm_response(self, *args, **kwargs):
        """模拟多轮LLM调用."""
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return response

    def create_patch_context(self):
        """创建patch上下文管理器."""
        mock_create_llm = patch("src.inference.llm.model_loader.create_llm")
        mock_llm = AsyncMock()
        mock_llm.ainvoke = self.mock_llm_response
        mock_create_llm.return_value = mock_llm
        return mock_create_llm
