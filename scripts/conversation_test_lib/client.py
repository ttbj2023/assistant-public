"""服务器健康检查与对话执行."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx

from scripts.conversation_test_lib.config import ConversationTestConfig
from scripts.conversation_test_lib.formatting import (
    SEPARATOR,
    THIN_SEP,
    _cyan,
    _green,
    _truncate,
    _yellow,
)


def check_server(config: ConversationTestConfig) -> bool:
    """检查目标服务器是否在线."""
    print(f"\n{_cyan('[准备]')} 检查服务器状态...")
    try:
        resp = httpx.get(f"{config.api_base}/health", timeout=10)
        if resp.status_code == 200:
            print(f"{_green('[准备]')} ✅ 服务器在线")
            return True
        print(f"{_yellow('[准备]')} ⚠️  服务器响应异常: {resp.status_code}")
        return False
    except Exception as e:
        print(f"{_yellow('[准备]')} ❌ 无法连接服务器: {e}")
        print("       请先启动: DEBUG=true python scripts/dev_server.py --port 8011")
        return False


def _build_content(
    user_input: str,
    image_path: str | None,
) -> str | list[dict[str, Any]]:
    """构造请求 content: 纯文本或 text+image_url 数组."""
    if not image_path:
        return user_input
    path = Path(image_path)
    if not path.exists():
        return user_input

    img_bytes = path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode()
    mime = "image/jpeg" if image_path.endswith(".jpg") else "image/png"
    return [
        {"type": "text", "text": user_input},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
    ]


def _record_success(
    round_num: int,
    tag: str,
    user_input: str,
    resp: httpx.Response,
    start: float,
) -> dict[str, Any]:
    """记录成功响应."""
    elapsed = time.time() - start
    response_data = resp.json() if resp.status_code == 200 else resp.text
    return {
        "round": round_num,
        "tag": tag,
        "user_input": user_input,
        "status_code": resp.status_code,
        "elapsed": round(elapsed, 1),
        "start_ts": start,
        "end_ts": time.time(),
        "response": response_data,
    }


def _record_failure(
    round_num: int,
    tag: str,
    user_input: str,
    status_code: str | int,
    response: object,
    start: float,
) -> dict[str, Any]:
    """记录超时或异常响应."""
    elapsed = time.time() - start
    return {
        "round": round_num,
        "tag": tag,
        "user_input": user_input,
        "status_code": status_code,
        "elapsed": round(elapsed, 1),
        "start_ts": start,
        "end_ts": time.time(),
        "response": response,
    }


def _print_round_header(
    round_num: int,
    total: int,
    tag: str,
    hint: str | None,
    user_input: str,
    image_path: str | None,
) -> None:
    """打印轮次开始信息."""
    print(f"\n{SEPARATOR}")
    header = f"[R{round_num}/{total}] 正在发送... ({tag})"
    print(_cyan(header))
    if hint:
        print(_yellow(f"  ⏳ {hint}, 请耐心等待"))
    print(f"  用户: {user_input}")
    if image_path:
        print(f"  📎 图片: {image_path}")
    print(THIN_SEP)


def _print_assistant_response(resp: object) -> None:
    """打印助手回复摘要."""
    if isinstance(resp, dict):
        msg = resp.get("choices", [{}])[0].get("message", {}).get("content", "(空响应)")
    else:
        msg = str(resp)
    print(f"  助手: {_truncate(msg)}")


def run_conversations(
    config: ConversationTestConfig,
    start_round: int = 1,
    max_rounds: int = 0,
) -> list[dict[str, Any]]:
    """串行执行预设对话并收集结果."""
    results: list[dict[str, Any]] = []
    conversations = config.conversations
    total = len(conversations)

    if start_round < 1 or start_round > total:
        print(
            _yellow(f"⚠️ start_round={start_round} 超出范围 [1, {total}], 将从第1轮开始")
        )
        start_round = 1

    if start_round > 1:
        print(_yellow(f"📌 从第 {start_round} 轮开始, 跳过前 {start_round - 1} 轮"))

    if max_rounds and max_rounds > 0:
        print(_yellow(f"📌 限流: 最多执行 {max_rounds} 轮 (校准模式)"))

    executed = 0

    for i, conv in enumerate(conversations, 1):
        if i < start_round:
            continue
        if max_rounds and executed >= max_rounds:
            break

        executed += 1

        tag = conv["tag"]
        hint = conv.get("hint")
        user_input = conv["input"]
        image_path = conv.get("image")

        _print_round_header(i, total, tag, hint, user_input, image_path)

        content = _build_content(user_input, image_path)
        request_data = {
            "model": config.model,
            "messages": [{"role": "user", "content": content}],
        }

        start = time.time()
        try:
            resp = httpx.post(
                f"{config.api_base}/v1/chat/completions",
                json=request_data,
                headers={"Authorization": f"Bearer {config.api_key}"},
                timeout=config.timeout,
            )
            elapsed = time.time() - start

            if resp.status_code != 200:
                print(
                    f"  助手: {_yellow(f'[HTTP {resp.status_code}] {resp.text[:300]}')}"
                )
            else:
                _print_assistant_response(resp.json())
            print(f"  耗时: {elapsed:.1f}s")

            results.append(_record_success(i, tag, user_input, resp, start))

        except httpx.TimeoutException:
            elapsed = time.time() - start
            print(f"  助手: {_yellow(f'[超时] 请求超过{config.timeout}s')}")
            print(f"  耗时: {elapsed:.1f}s")
            results.append(_record_failure(i, tag, user_input, "TIMEOUT", None, start))
        except Exception as e:
            elapsed = time.time() - start
            print(f"  助手: {_yellow(f'[异常] {e}')}")
            print(f"  耗时: {elapsed:.1f}s")
            results.append(_record_failure(i, tag, user_input, "ERROR", str(e), start))

    print(f"\n{SEPARATOR}")
    print(_green(f"[完成] {executed}轮对话执行完毕"))
    return results
