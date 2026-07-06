"""微信公众号文章发布服务.

编排完整发布流程:
1. 摘要+封面提示词自动生成 (1次LLM) + 文章润色 (1次LLM) - 并行
2. 封面图生成+上传
3. 自动插图定位 (纯代码) + 插图描述生成 (1次LLM)
4. 附件清理 ([file: id] 引用解析)
5. 插图处理 ({{IMG:...}} 占位符 -> 图片生成)
6. Markdown -> 微信 HTML
7. 图片上传到微信素材库 + CDN替换
8. 封面图插入文章开头
9. 创建草稿
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

_IMG_PLACEHOLDER_RE = re.compile(r"\{\{IMG:(.+?)\}\}")
_AUTO_PREFIX = "auto:"
_ILLUSTRATION_MARKER_RE = re.compile(r"\[ILLUSTRATION_(\d+)\]")

_ATTACHMENT_MARKER_RE = re.compile(r"\[file:\s*(\w{8})\]")

_ANALYZE_PROMPT = """\
请分析以下文章, 生成摘要和封面图提示词.

## 文章信息
标题:{title}

内容:
{content}

## 任务
1. 生成摘要:80字符以内, 概括核心洞察, 不要添加"本文介绍了"等冗余词语
2. 生成封面图提示词:描述一张封面图画面, 体现文章核心洞察, 专业克制有质感
   - 严禁在图片中包含任何文字/标题/标签
   - 只描述画面内容, 不要提及"封面图"/"微信"等平台词汇

## 返回格式 (严格 JSON, 不要 markdown 代码块)
{{"summary": "摘要内容", "cover_prompt": "封面图画面描述"}}"""

_REFINE_PROMPT = """\
你是一位科技类深度公众号的主编, 账号调性为"冷静/专业/有洞察/不煽动", 读者多为科技行业从业者和深度思考者. 你的任务是帮助我把一篇草稿打磨成适合该公众号发布的作品.

[原文内容]
{content}

[核心原则]
- 尊重原文的洞察/思考深度和核心结论, 只调整表达方式, 不添加原文没有的新观点
- 写作者的存在感要低, 让事实和逻辑本身说话, 避免任何"小编感"语言

[改写要求]
1. 结构与段落
   - 为文章添加恰当的小标题(二级或三级标题), 标题本身要信息量足/冷静克制, 不使用疑问句/感叹句和网络热词
   - 正文段落控制在3-5句, 每段不超过150字; 段落之间逻辑跳跃不要太大, 适当使用过渡句衔接
   - 保持清晰的"引入-展开-洞察-收束"结构, 但不使用套路化结尾

2. 语言与措辞
   - 使用客观/准确/克制的书面语, 避免口头禅/网络用语和过度修饰
   - 优先用"观察到""值得注意""一个被忽视的视角是"等表达, 避免"你必须""每个人都应该"等说教或催促句式
   - 不制造焦虑, 不放大情绪, 不夹带价值观评判
   - 禁用标题党写法, 包括但不限于:"震惊""彻底改写""一夜之间""终于有人把...讲清楚了"

3. 阅读节奏(移动端适配)
   - 考虑到微信公众号的手机阅读场景, 要在长句中穿插短句, 使阅读有呼吸感
   - 关键洞察可以用独立短句成段来强调, 但全文不超过3次
   - 专业术语首次出现时保留, 但全篇避免在同一概念上反复解释

4. 格式
   - 使用 Markdown 格式, 但仅限: 标题(##/###)/加粗/无序列表/引用块(用于引述观点或数据)
   - 不生成表格/分割线和代码块
   - 保留原文中的 [file: id] 标记, 不要修改或删除这些标记
   - 不得添加任何AI提示痕迹或解释性说明, 直接输出改写后的全文

直接输出改写完成后的完整文章, 以 Markdown 格式呈现."""

_ILLUSTRATION_PROMPT = """\
你是一个科技文章视觉编辑. 以下文章中 [ILLUSTRATION_1] [ILLUSTRATION_2] ... 标记了需要插图的位置.

为每个标记位置生成一张插图的视觉描述:
- 纯视觉符号/画面, 严禁包含任何文字/标签/标题/品牌名
- 风格: 抽象/专业/克制, 深色冷色调为主
- 结合上下文内容, 图画应呼应前文主题

返回严格 JSON (不要 markdown 代码块):
{{"illustrations": {{"1": "描述1", "2": "描述2", ...}}}}

key 必须与标记中的序号一一对应.

文章:
{content}"""


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

    content = await _auto_insert_illustrations(
        content, text_model_id, text_model_params
    )

    content, attachment_map = await _clean_content(content, client, user_id, thread_id)

    content, illustration_paths = await _process_illustrations(
        content, img_service, image_model_id
    )

    html = md_to_wechat_html(content, summary=summary, author=final_author)
    html = sanitize(html)

    for placeholder, local_path in illustration_paths:
        if await asyncio.to_thread(pathlib.Path(local_path).exists):
            media_info = await client.upload_media(local_path)
            if media_info and media_info.get("url"):
                img_tag = (
                    '<section style="text-align: center; margin: 16px 0;">'
                    f'<img src="{media_info["url"]}" style="max-width: 100%; height: auto;" />'
                    "</section>"
                )
                html = html.replace(placeholder, img_tag)
            await asyncio.to_thread(pathlib.Path(local_path).unlink)

    html = _replace_attachment_markers(html, attachment_map)

    if cover_media_info and cover_media_info.get("url"):
        cover_img = (
            '<section style="text-align: center; margin: 0 0 16px 0;">'
            f'<img src="{cover_media_info["url"]}" style="max-width: 100%; height: auto;" />'
            "</section>"
        )
        html = cover_img + html

    article: dict[str, Any] = {
        "title": title,
        "author": final_author,
        "digest": summary[:100],
        "content": html,
        "thumb_media_id": cover_media_info["media_id"],
        "show_cover_pic": 0,
        "content_source_url": "",
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
    """用 pro 模型润色文章, 优化公众号排版. 失败返回原文."""
    prompt = _REFINE_PROMPT.format(content=content)
    try:
        response = await _invoke_llm(
            [HumanMessage(content=prompt)], model_id, model_params
        )
        refined = response.content.strip()
        if refined:
            logger.info("文章润色完成")
            return refined
        return content
    except Exception as e:
        logger.warning("文章润色失败, 使用原文: %s", e)
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

    占位符 __WXATT_{file_id}__ 在 markdown->html 转换中保持稳定, 避免
    full_marker 含行尾文本被 markdown 语法重组导致 html 阶段失配.
    非图片附件或上传失败时移除标记.
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
                placeholder = f"__WXATT_{file_id}__"
                content = marker_re.sub(placeholder, content)
                attachment_map[placeholder] = media_info["url"]
            else:
                content = marker_re.sub("", content)
        except Exception as e:
            logger.warning("附件 %s 上传失败: %s", file_id, e)
            content = marker_re.sub("", content)

    return content


def _locate_illustration_points(
    content: str,
    interval: int = 600,
    max_points: int = 4,
) -> list[int]:
    """纯代码定位插图插入点.

    规则:
    - 每 ~interval 字且在段落结尾处标记一个插图点
    - 含 [file: id] 的段落及其后一个段落跳过 (图表后延)
    - 不在最后一个段落放插图
    - 最多 max_points 个插图点

    Returns:
        需要插入插图标记的段落索引列表
    """
    paragraphs = re.split(r"\n\n+", content.strip())
    if len(paragraphs) < 3:
        return []

    chart_skip: set[int] = set()
    for i, para in enumerate(paragraphs):
        if _ATTACHMENT_MARKER_RE.search(para):
            chart_skip.add(i)
            if i + 1 < len(paragraphs):
                chart_skip.add(i + 1)

    points: list[int] = []
    char_count = 0

    for i, para in enumerate(paragraphs):
        if i in chart_skip:
            char_count += len(para)
            continue
        if i == len(paragraphs) - 1:
            break

        prev_count = char_count
        char_count += len(para)

        if (
            char_count >= interval * (len(points) + 1)
            and prev_count < char_count
            and char_count - prev_count > 10
        ):
            points.append(i)
            if len(points) >= max_points:
                break

    return points


async def _generate_illustration_prompts(
    content_with_markers: str,
    model_id: str,
    model_params: dict[str, Any],
) -> dict[str, str]:
    """LLM 为每个 [ILLUSTRATION_N] 生成视觉描述.

    Returns:
        {序号: 描述} 映射; 失败返回空 dict.
    """
    markers = _ILLUSTRATION_MARKER_RE.findall(content_with_markers)
    if not markers:
        return {}

    expected_ids = set(markers)
    params = {**model_params, "max_tokens": 8192}

    prompt_text = _ILLUSTRATION_PROMPT.format(content=content_with_markers)
    try:
        response = await _invoke_llm(
            [HumanMessage(content=prompt_text)], model_id, params
        )
        raw = content_to_text(response.content).strip()
        json_str = _extract_json(raw)
        result = json.loads(json_str)
        illustrations_raw = result.get("illustrations", {})

        if not isinstance(illustrations_raw, dict):
            logger.warning(
                "插图描述格式错误: 期望 dict, 得到 %s", type(illustrations_raw).__name__
            )
            return {}

        matched: dict[str, str] = {}
        for mid in expected_ids:
            if illustrations_raw.get(mid):
                matched[mid] = illustrations_raw[mid]
            else:
                logger.warning("插图 %s: LLM 未返回描述, 跳过", mid)

        extra = set(illustrations_raw.keys()) - expected_ids
        if extra:
            logger.info("LLM 多返回 %d 个描述, 已丢弃: %s", len(extra), extra)

        if matched:
            logger.info("插图描述生成完成: %d/%d 个", len(matched), len(expected_ids))
        return matched
    except Exception as e:
        logger.warning("插图描述生成失败: %s", e)
        return {}


async def _auto_insert_illustrations(
    content: str,
    model_id: str,
    model_params: dict[str, Any],
) -> str:
    """自动插图: 纯代码定位 + LLM 生成描述.

    1. 纯代码在段落结尾定位插图点 (每 ~600 字, 避开图表区域)
    2. 插入带序号的标记 [ILLUSTRATION_N]
    3. LLM 为每个标记生成视觉描述 (按序号对应)
    4. 按序号精确替换为 {{IMG:auto:描述}} 占位符
    5. 清理残留未匹配的标记
    """
    existing_count = len(_IMG_PLACEHOLDER_RE.findall(content))
    if existing_count >= 2:
        return content

    point_indices = _locate_illustration_points(content)
    if not point_indices:
        logger.info("无需自动插图")
        return content

    paragraphs = re.split(r"\n\n+", content.strip())

    for i, idx in enumerate(point_indices, start=1):
        paragraphs[idx] += f"\n\n[ILLUSTRATION_{i}]\n\n"

    content_with_markers = "\n\n".join(paragraphs)

    prompts = await _generate_illustration_prompts(
        content_with_markers, model_id, model_params
    )

    if not prompts:
        cleaned = _ILLUSTRATION_MARKER_RE.sub("", content_with_markers)
        return cleaned.strip()

    for mid, prompt_text in prompts.items():
        marker = f"[ILLUSTRATION_{mid}]"
        replacement = f"\n\n{{{{IMG:auto:{prompt_text}}}}}\n\n"
        content_with_markers = content_with_markers.replace(marker, replacement)

    cleaned = _ILLUSTRATION_MARKER_RE.sub("", content_with_markers)
    logger.info("自动插图完成: %d 张", len(prompts))
    return cleaned.strip()


async def _process_illustrations(
    content: str,
    img_service: ImageGenerationService,
    image_model_id: str,
) -> tuple[str, list[tuple[str, str]]]:
    """扫描并处理 {{IMG:...}} 占位符.

    Returns:
        (处理后的content, [(原始占位符, 本地图片路径)] 列表)

    """
    illustration_paths: list[tuple[str, str]] = []

    matches = _IMG_PLACEHOLDER_RE.findall(content)
    for raw in matches:
        original = f"{{{{IMG:{raw}}}}}"
        local_path: str | None = None

        if raw.startswith(_AUTO_PREFIX):
            prompt = raw[len(_AUTO_PREFIX) :]
            local_path = await _generate_image(
                img_service, image_model_id, prompt, "illustration"
            )
        elif await asyncio.to_thread(pathlib.Path(raw).exists):
            local_path = raw

        if local_path:
            illustration_paths.append((original, local_path))

    return content, illustration_paths


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

    return html
