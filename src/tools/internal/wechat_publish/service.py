"""微信公众号文章发布服务.

编排完整发布流程:
1. 摘要+封面提示词自动生成 (1次LLM) + 校对排版 (1次LLM) - 并行
2. 封面图生成+上传
3. 附件清理 ([file: id] 引用解析)
4. Markdown -> 微信 HTML
5. 附件上传到微信素材库 + CDN替换
6. 封面图插入文章开头
7. 创建草稿
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import tempfile
from typing import Any

from langchain_core.messages import HumanMessage

from src.inference.image_generation import ImageGenerationService
from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

from .adapter import sanitize
from .api_client import WechatApiClient
from .converter import md_to_wechat_html

logger = logging.getLogger(__name__)

_ATTACHMENT_MARKER_RE = re.compile(r"\[file:\s*(\w{8})\]")
_ATTACHMENT_PLACEHOLDER_RE = re.compile(r"\{WXATT:\w+\}")

_ANALYZE_PROMPT = """\
请分析以下文章, 生成摘要和封面图提示词.

## 文章信息
标题:{title}

内容:
{content}

## 任务
1. 生成摘要:80字符以内, 概括核心洞察, 不要添加"本文介绍了"等冗余词语
2. 生成封面图提示词:描述一张与文章核心意象强关联的封面图画面
   - 画面应承载信息量: 用视觉隐喻/符号传达文章核心矛盾或关键概念, 而非纯装饰
   - 风格不要固定: 根据文章气质自由选择 (写实/超现实/极简/拼贴/数据可视化风格/插画/摄影感等), 每篇文章应有不同的视觉方向
   - 追求视觉冲击力和记忆点, 避免泛泛的"科技感深色背景"
   - 严禁在图片中包含任何文字/标题/标签
   - 只描述画面内容, 不要提及"封面图"/"微信"等平台词汇

## 返回格式 (严格 JSON, 不要 markdown 代码块)
{{"summary": "摘要内容", "cover_prompt": "封面图画面描述"}}"""

_REFINE_PROMPT = """\
你是一位校对编辑. 你的任务是对一篇 Markdown 草稿做校对和格式排版, 使其适合微信公众号手机端阅读.

[原文内容]
{content}

[绝对禁止]
- 禁止改写/替换/润色任何句子, 禁止调整措辞或语气
- 禁止添加原文没有的观点/过渡句/总结句/引言/小标题
- 禁止删除原文中的任何段落或句子
- 禁止统一文风, 原文的口语化/不规则/个人化表达必须原样保留

[允许的操作 (仅限以下)]
1. 校对: 修错别字/标点错误/明显断句错误 (不改变表达)
2. 断段: 过长的段落拆分, 适配手机屏幕阅读
3. 格式整理: 调整标题层级/加粗/列表等 Markdown 格式, 使排版清晰
4. 保留原文中的 [file: id] 标记, 不要修改或删除

[格式]
- Markdown 格式, 仅限: 标题(##/###)/加粗/无序列表/引用块
- 不生成表格/分割线/代码块

直接输出整理后的完整文章."""


async def run_publish(
    content: str,
    title: str,
    author: str | None,
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """执行微信发布流程.

    Args:
        content: Markdown 正文
        title: 文章标题
        author: 作者 (可选, 首次指定后记住为默认值)
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: AgentID, 用于渠道配置物理隔离

    Returns:
        发布结果字典

    """
    from src.config.inference_config import get_config as get_inference_config

    inference_cfg = get_inference_config()
    wp_config = inference_cfg.wechat_publish
    text_model_id = wp_config.model
    text_model_params = wp_config.model_params
    refine_model_id = wp_config.refine_model
    refine_model_params = wp_config.refine_model_params
    image_model_id = inference_cfg.image_generation.model_id

    from src.storage.service.user_channel_config_service import (
        get_user_channel_config_service,
    )

    config_service = await get_user_channel_config_service(user_id, thread_id, agent_id)
    mp_config = await config_service.get_config_for_channel("wechat_mp")
    if not mp_config or not mp_config.get("appid") or not mp_config.get("secret"):
        return {"success": False, "message": "未配置微信公众号凭证"}

    client = WechatApiClient(appid=mp_config["appid"], secret=mp_config["secret"])
    img_service = ImageGenerationService()

    final_author = await _resolve_author(author, mp_config, config_service)

    analysis, refined_content = await asyncio.gather(
        _analyze_article(content, title, text_model_id, text_model_params),
        _refine_content(content, refine_model_id, refine_model_params),
    )
    summary = analysis.get("summary", "")[:100]
    cover_prompt = analysis.get("cover_prompt", "")
    content = refined_content

    cover_media_info: dict[str, str] | None = None
    if cover_prompt:
        cover_path = await _generate_image(
            img_service, image_model_id, cover_prompt, "cover"
        )
        if cover_path:
            cover_media_info = await client.upload_media(cover_path)
            await asyncio.to_thread(pathlib.Path(cover_path).unlink, missing_ok=True)

    if not cover_media_info:
        return {"success": False, "message": "封面图生成或上传失败"}

    content, attachment_map = await _clean_content(content, client, user_id, thread_id)

    html = md_to_wechat_html(content, author=final_author, title=title)
    html = sanitize(html)

    html = _replace_attachment_markers(html, attachment_map)

    article: dict[str, Any] = {
        "title": title,
        "author": final_author,
        "digest": summary[:100],
        "content": html,
        "thumb_media_id": cover_media_info["media_id"],
        "show_cover_pic": 0,
        "content_source_url": "",
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }

    draft_id = await client.upload_news_draft([article])

    if draft_id:
        return {
            "success": True,
            "draft_id": draft_id,
            "message": "已发布到微信公众号草稿箱",
        }
    return {"success": False, "message": "创建草稿失败"}


async def _resolve_author(
    author: str | None,
    mp_config: dict[str, Any],
    config_service: Any,
) -> str:
    """解析作者, 首次指定后记住为默认值."""
    default_author = mp_config.get("default_author", "")

    if author:
        if not default_author:
            try:
                updated_config = {**mp_config, "default_author": author}
                await config_service.upsert_channel_config("wechat_mp", updated_config)
                logger.info("已记住默认作者: %s", author)
            except Exception as e:
                logger.warning("记住默认作者失败: %s", e)
        return author

    return default_author


async def _invoke_llm(
    prompt: str | list,
    model_id: str,
    model_params: dict[str, Any],
) -> Any:
    """调用 LLM, 瞬时错误时切换到全局文本 fallback 模型."""
    return await invoke_with_fallback(
        prompt,
        model_id,
        model_params,
        fallback_kind="text",
        usage_tag="tool_llm",
        use_json_mode=False,
    )


async def _analyze_article(
    content: str,
    title: str,
    model_id: str,
    model_params: dict[str, Any],
) -> dict[str, str]:
    """1次LLM调用生成摘要+封面提示词."""
    prompt_text = _ANALYZE_PROMPT.format(title=title, content=content[:4000])
    try:
        response = await _invoke_llm(
            [HumanMessage(content=prompt_text)], model_id, model_params
        )
        raw = content_to_text(response.content).strip()

        json_str = _extract_json(raw)
        result = json.loads(json_str)

        summary = result.get("summary", "")
        cover_prompt = result.get("cover_prompt", "")

        if summary and len(summary) > 100:
            logger.warning("摘要超长(%d字符), 截断处理", len(summary))
            summary = summary[:100]

        return {"summary": summary, "cover_prompt": cover_prompt}
    except Exception as e:
        logger.warning("文章分析失败: %s", e)
        return {"summary": "", "cover_prompt": ""}


async def _refine_content(
    content: str,
    model_id: str,
    model_params: dict[str, Any],
) -> str:
    """校对 + 格式排版: 修错别字/断段/调整格式, 不改写表达. 失败返回原文."""
    prompt = _REFINE_PROMPT.format(content=content)
    try:
        response = await _invoke_llm(
            [HumanMessage(content=prompt)], model_id, model_params
        )
        refined = response.content.strip()
        if refined:
            logger.info("校对排版完成")
            return refined
        return content
    except Exception as e:
        logger.warning("校对排版失败, 使用原文: %s", e)
        return content


def _extract_json(text: str) -> str:
    """从LLM输出中提取JSON."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    text_stripped = text.strip()
    if text_stripped.startswith("{") and text_stripped.endswith("}"):
        return text_stripped

    match = re.search(r"\{[^{}]*\}", text_stripped, re.DOTALL)
    if match:
        return match.group(0)

    return text


async def _generate_image(
    img_service: ImageGenerationService,
    model_id: str,
    prompt: str,
    prefix: str,
) -> str | None:
    try:
        result = await img_service.generate_image(
            model_id=model_id,
            prompt=prompt,
            size="2560x1440",
            watermark=False,
            timeout=120.0,
        )
        tmp_dir = tempfile.mkdtemp()
        path = pathlib.Path(tmp_dir) / f"{prefix}.png"
        with path.open("wb") as f:
            f.write(result.image_data)
        return str(path)
    except Exception as e:
        logger.warning("图片生成失败 (%s): %s", prefix, e)
        return None


async def _clean_content(
    content: str,
    client: WechatApiClient,
    user_id: str,
    thread_id: str,
) -> tuple[str, dict[str, str]]:
    """清理文章内容中的附件引用标记.

    [file: id] -> 图片附件上传微信CDN, 非图片丢弃.

    Returns:
        (清理后的content, {原始标记: CDN_URL} 映射表)

    """
    attachment_map: dict[str, str] = {}

    content = await _resolve_attachment_markers(
        content, client, user_id, thread_id, attachment_map
    )

    return content, attachment_map


async def _resolve_attachment_markers(
    content: str,
    client: WechatApiClient,
    user_id: str,
    thread_id: str,
    attachment_map: dict[str, str],
) -> str:
    """解析 [file: id] 标记, 图片上传微信CDN后用 ASCII 占位符替换.

    占位符 {WXATT:{file_id}} 不含 markdown 强调定界符 (双下划线/星号),
    经 markdown->html 转换后保持字面量不变, 避免被转成 <strong> 导致
    html 阶段字符串失配. 非图片附件或上传失败时移除标记.
    """
    from src.core.path_resolver import resolve_attachment_internal_path
    from src.storage.service.file_registry_service import (
        create_file_registry_service,
    )

    attach_service = await create_file_registry_service(user_id)
    seen: set[str] = set()

    for file_id in _ATTACHMENT_MARKER_RE.findall(content):
        if file_id in seen:
            continue
        seen.add(file_id)

        marker_re = re.compile(rf"\[file:\s*{re.escape(file_id)}\]")
        db_entry = await attach_service.get(file_id)
        if not db_entry:
            logger.debug("附件 %s 未在注册表中找到, 移除标记", file_id)
            content = marker_re.sub("", content)
            continue

        if db_entry.file_type != "image":
            logger.debug("附件 %s 非图片(%s), 丢弃", file_id, db_entry.file_type)
            content = marker_re.sub("", content)
            continue

        try:
            internal_path = (
                db_entry.physical_path.split("shared/", 1)[-1]
                if "shared/" in db_entry.physical_path
                else db_entry.physical_path
            )
            full_path = resolve_attachment_internal_path(
                internal_path, user_id, thread_id
            )
            if not full_path.exists():
                logger.warning("附件文件不存在: %s", full_path)
                content = marker_re.sub("", content)
                continue

            media_info = await client.upload_media(str(full_path))
            if media_info and media_info.get("url"):
                placeholder = f"{{WXATT:{file_id}}}"
                content = marker_re.sub(placeholder, content)
                attachment_map[placeholder] = media_info["url"]
            else:
                content = marker_re.sub("", content)
        except Exception as e:
            logger.warning("附件 %s 上传失败: %s", file_id, e)
            content = marker_re.sub("", content)

    return content


def _replace_attachment_markers(
    html: str,
    attachment_map: dict[str, str],
) -> str:
    """将 HTML 中的附件占位符替换为微信 CDN <img> 标签."""
    for placeholder, cdn_url in attachment_map.items():
        img_tag = (
            '<section style="text-align: center; margin: 16px 0;">'
            f'<img src="{cdn_url}" style="max-width: 100%; height: auto;" />'
            "</section>"
        )
        html = html.replace(placeholder, img_tag)

    residual = _ATTACHMENT_MARKER_RE.search(html)
    if residual:
        logger.warning("微信HTML中残留未替换的附件标记: %s", residual.group(0))

    leftover_placeholder = _ATTACHMENT_PLACEHOLDER_RE.search(html)
    if leftover_placeholder:
        logger.warning(
            "微信HTML中残留未替换的附件占位符 (attachment_map 缺失对应key): %s",
            leftover_placeholder.group(0),
        )

    return html
