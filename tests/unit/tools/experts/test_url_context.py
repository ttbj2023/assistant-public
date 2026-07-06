"""Gemini URL Context 模块单元测试."""

from __future__ import annotations

from src.tools.experts.web_research.url_context import (
    _parse_url_context_response,
    extract_supported_urls,
)


class TestExtractSupportedUrls:
    """URL 提取和过滤."""

    def test_should_extract_public_supported_urls_only(self) -> None:
        text = (
            "分析 https://example.com/report.pdf 和 https://localhost/test, "
            "跳过 http://127.0.0.1/a, https://docs.google.com/document/d/x, "
            "https://youtu.be/demo, https://demo.ngrok-free.app/a, "
            "保留 https://example.com/report.pdf。"
        )

        urls = extract_supported_urls(text, max_urls=4)

        assert urls == ["https://example.com/report.pdf"]

    def test_should_respect_max_urls_and_dedupe(self) -> None:
        text = (
            "https://a.example.com/a https://a.example.com/a "
            "https://b.example.com/b https://c.example.com/c"
        )

        urls = extract_supported_urls(text, max_urls=2)

        assert urls == ["https://a.example.com/a", "https://b.example.com/b"]


class TestParseUrlContextResponse:
    """Interactions 响应解析."""

    def test_should_mark_verified_when_citation_and_success_retrieval_exist(
        self,
    ) -> None:
        interaction = {
            "steps": [
                {
                    "type": "url_context_result",
                    "result": [{"url": "https://example.com/a", "status": "success"}],
                },
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "页面说明了核心结论。",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Example",
                                    "url": "https://example.com/a",
                                    "start_index": 0,
                                    "end_index": 4,
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        result = _parse_url_context_response(interaction)

        assert result["verified"] is True
        assert result["answer"] == "页面说明了核心结论。"
        assert result["citation_count"] == 1
        assert result["sources"][0]["url"] == "https://example.com/a"

    def test_should_reject_answer_without_url_citation(self) -> None:
        interaction = {
            "steps": [
                {
                    "type": "url_context_result",
                    "result": [{"url": "https://example.com/a", "status": "success"}],
                },
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "模型给出了回答, 但没有 citation。",
                            "annotations": [],
                        }
                    ],
                },
            ],
        }

        result = _parse_url_context_response(interaction)

        assert result["verified"] is False
        assert result["error"] == "no_url_citation"

    def test_should_reject_failed_retrieval_even_with_citation(self) -> None:
        interaction = {
            "steps": [
                {
                    "type": "url_context_result",
                    "result": [{"url": "https://example.com/a", "status": "unsafe"}],
                },
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "回答",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Example",
                                    "url": "https://example.com/a",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        result = _parse_url_context_response(interaction)

        assert result["verified"] is False
        assert result["error"] == "url_retrieval_failed"
