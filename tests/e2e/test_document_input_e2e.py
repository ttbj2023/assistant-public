"""纯文本文档输入链路 E2E 测试 — 核心独特价值.

验证 HTTP file content block → 文档入库 → 当轮 LLM prompt 内联 →
对话持久化仅存 [file: id] 标记 → 次轮 read_file 可读全文.

独特价值 (为什么集成/单元测试无法覆盖):
- 集成测试 mock 在 Service 层, 无法验证 file block 从 HTTP 边界到
  LLM prompt 的全链路组装
- 单元测试 mock 在函数级, 无法验证持久化标记与 desc 原文的真实落盘协作
"""

from __future__ import annotations

import base64

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.mock_llm import E2EMockLLM


@pytest.mark.e2e
class TestDocumentInputPipelineE2E:
    """file content block 全链路 E2E 测试."""

    async def test_file_block_inlined_to_llm_and_marker_persisted(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_db_reader,
        e2e_test_user,
    ):
        """file block 请求: 当轮 prompt 内联全文, 持久化仅存标记."""
        doc_content = "# 项目周报\n\n本周完成文件入库功能开发。"
        E2EMockLLM.set_script([
            AIMessage(content="已读完周报，收到。", tool_calls=[]),
        ])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看一下这份周报"},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "周报.md",
                                    "content": doc_content,
                                },
                            },
                        ],
                    }
                ],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"]

        # 1. 当轮 LLM 输入内联文档全文
        last_input = str(E2EMockLLM.get_last_input())
        assert "项目周报" in last_input, "当轮 prompt 应内联小文档全文"
        assert "<document>" in last_input

        # 2. 持久化的 user_message 只存 [file: id] 标记, 不含全文
        conversations = e2e_db_reader.read_conversations(
            e2e_test_thread_id, "personal-assistant"
        )
        assert conversations, "对话应已持久化"
        last_user_msg = next(
            (c for c in reversed(conversations) if c.get("user_message")),
            None,
        )
        assert last_user_msg is not None
        user_text = last_user_msg["user_message"]
        assert "[file: " in user_text, "持久化应含 [file: id] 标记"
        assert "本周完成文件入库功能开发" not in user_text, (
            "持久化不应包含文档全文 (防历史污染)"
        )

        # 3. desc = 摘要 + 分隔符 + 原文 (统一结构)
        import re

        match = re.search(r"\[file: ([0-9a-f]{8})\]", user_text)
        assert match, "应能从标记提取 file_id"
        file_id = match.group(1)

        from src.files.desc_writer import read_desc, split_desc

        summary, original = split_desc(read_desc(e2e_test_user, file_id))
        assert summary, "摘要区应非空"
        assert original == doc_content

    async def test_large_document_marker_only_in_prompt(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """大文档 (>4000字符): 当轮 prompt 仅标记 + read_file 提示."""
        big_content = "长文档内容。" * 1000  # 6000 字符
        E2EMockLLM.set_script([
            AIMessage(content="文档较长，已记录。", tool_calls=[]),
        ])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "帮我读这个"},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "长文.md",
                                    "content": big_content,
                                },
                            },
                        ],
                    }
                ],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200

        last_input = str(E2EMockLLM.get_last_input())
        assert "[file: " in last_input
        assert "<document>" not in last_input, "大文档不应内联全文"
        assert "read_file" in last_input

    async def test_mixed_image_and_file_blocks(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """图片 + 文档混合请求: 双通道同时工作."""
        b64_png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        E2EMockLLM.set_script([
            AIMessage(content="收到图片和文档。", tool_calls=[]),
        ])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看这两样东西"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_png}"
                                },
                            },
                            {
                                "type": "file",
                                "file": {"filename": "说明.md", "content": "说明内容"},
                            },
                        ],
                    }
                ],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200

        last_input = str(E2EMockLLM.get_last_input())
        assert "说明内容" in last_input, "文档内容应进入 prompt"

    async def test_code_file_stored_with_summary_structure(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_db_reader,
        e2e_test_user,
    ):
        """代码文件 (.py) 全链路: 入库 document 类型, desc = 摘要+代码原文 结构.

        后台 AI 摘要依赖真实模型调用, E2E 只验证注册时占位 desc
        (brief 摘要 + 原文) 的结构正确性.
        """
        code_content = "def main():\n    print('hello')\n"
        E2EMockLLM.set_script([
            AIMessage(content="收到代码文件。", tool_calls=[]),
        ])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看看这个脚本"},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "main.py",
                                    "content": code_content,
                                },
                            },
                        ],
                    }
                ],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200

        # 持久化标记提取 file_id
        conversations = e2e_db_reader.read_conversations(
            e2e_test_thread_id, "personal-assistant"
        )
        last_user_msg = next(
            (c for c in reversed(conversations) if c.get("user_message")),
            None,
        )
        assert last_user_msg is not None
        import re

        match = re.search(r"\[file: ([0-9a-f]{8})\]", last_user_msg["user_message"])
        assert match, "应能从标记提取 file_id"
        file_id = match.group(1)

        # desc = 注册时占位摘要(brief) + 分隔符 + 代码原文
        from src.files.desc_writer import read_desc, split_desc

        summary, original = split_desc(read_desc(e2e_test_user, file_id))
        assert summary, "desc 摘要区应非空 (注册时占位 brief)"
        assert original == code_content

        # 注册表: document 类型 + py 扩展名
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(e2e_test_user)
        entry = await registry.get(file_id)
        assert entry is not None
        assert entry.file_type == "document"
        assert entry.file_format == "py"
