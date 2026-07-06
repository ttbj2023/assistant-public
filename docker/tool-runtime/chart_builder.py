"""HTML 模板构建器 - 生成自包含 HTML (JS 内联, sentinel 检测渲染完成).

多种引擎共用同一渲染管线: 构建 HTML → BrowserRenderer.render_to_png().
HTML 中内联完整 JS 库, 设置 window.__rendered / window.__renderError 标志.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# tool-runtime 容器内: /app/chart_libs/; 本地测试: docker/tool-runtime/chart_libs/
_STATIC_DIR = Path(__file__).parent / "chart_libs"

_JS_CACHE: dict[str, str] = {}


def _read_static(filename: str) -> str:
    """读取并缓存 static/ 下的 JS 文件内容."""
    if filename not in _JS_CACHE:
        js_path = _STATIC_DIR / filename
        _JS_CACHE[filename] = js_path.read_text(encoding="utf-8")
    return _JS_CACHE[filename]


# 默认 X 轴标签旋转角度, 仅在标签需要旋转(避免重叠)时由 Vega-Lite 应用.
# 解决默认垂直旋转 -90 度导致文字竖排不符合阅读习惯的问题.
_DEFAULT_X_LABEL_ANGLE = -45
_DEFAULT_MARKMAP_WIDTH = 1200
_DEFAULT_MARKMAP_HEIGHT = 800

# AntV G2 分类色板 (国内数据可视化主流, 现代清新)
# 参考: https://charts.ant.design/
_DEFAULT_VEGA_CATEGORY = [
    "#5B8FF9", "#5AD8A6", "#5D7092", "#F6BD16",
    "#E8684A", "#6DC8EC", "#945FB9", "#FF9845",
]

# Vega-Lite 默认 config (仅在 spec 未显式设置时注入)
# 让模型零配色代码就获得专业外观 + 柔和视觉效果
_DEFAULT_VEGA_CONFIG: dict[str, Any] = {
    "font": "Noto Sans CJK SC",
    "range": {"category": _DEFAULT_VEGA_CATEGORY},
    "view": {"stroke": None},
    "axis": {
        "labelFontSize": 13,
        "titleFontSize": 14,
        "domainOpacity": 0.4,
        "tickOpacity": 0.4,
    },
    "legend": {"labelFontSize": 13},
    "bar": {"cornerRadiusEnd": 2, "fillOpacity": 0.9},
    "arc": {"stroke": "#FFFFFF", "strokeWidth": 2, "cornerRadius": 1},
    "area": {"fillOpacity": 0.7},
    "line": {"strokeWidth": 2.5},
    "circle": {"fillOpacity": 0.8, "stroke": "#FFFFFF", "strokeWidth": 1},
}


def _inject_default_x_label_angle(spec_json: str) -> str:
    """为 nominal/ordinal X 轴注入默认标签角度 -45 度(仅当未显式设置时).

    Vega-Lite 默认对离散型(nominal/ordinal) X 轴, 在标签重叠时垂直旋转 -90 度,
    文字竖排不符合阅读习惯. 改为 -45 度(斜向). 标签较少不重叠时 Vega-Lite 仍保持
    水平, 不受影响. quantitative/temporal 类型默认已水平, 无需干预.

    覆盖单视图(spec.encoding.x)与分层图(spec.layer[].encoding.x)两种结构.
    仅在 axis.labelAngle 未显式设置时注入, 尊重 LLM 的显式覆盖.

    Args:
        spec_json: Vega-Lite spec JSON 字符串

    Returns:
        可能已注入默认角度的 spec JSON 字符串. JSON 解析失败时原样返回,
        交由下游 vegaEmbed 正常报错, 不破坏现有流程.
    """
    try:
        spec = json.loads(spec_json)
    except (json.JSONDecodeError, TypeError):
        return spec_json

    if not isinstance(spec, dict):
        return spec_json

    def _process_encoding(encoding: Any) -> None:
        if not isinstance(encoding, dict):
            return
        x_def = encoding.get("x")
        if not isinstance(x_def, dict):
            return
        if x_def.get("type") not in {"nominal", "ordinal"}:
            return
        if "axis" in x_def:
            axis = x_def["axis"]
            # "axis": null 表示显式隐藏整个轴, 尊重该意图不注入;
            # 其他非字典类型同样跳过, 保守处理
            if not isinstance(axis, dict):
                return
        else:
            axis = {}
            x_def["axis"] = axis
        if "labelAngle" not in axis:
            axis["labelAngle"] = _DEFAULT_X_LABEL_ANGLE

    _process_encoding(spec.get("encoding"))

    layer = spec.get("layer")
    if isinstance(layer, list):
        for sub_view in layer:
            if isinstance(sub_view, dict):
                _process_encoding(sub_view.get("encoding"))

    # 保持紧凑格式(separators), 与 LLM 输入的 spec 风格一致, 内联到 JS 更简洁
    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


def _inject_default_config(spec_json: str) -> str:
    """注入默认 config (配色/字号/样式), 仅在 spec 未显式设置时生效.

    注入内容:
    - config.range.category: AntV 8 色分类色板 (饼图/柱/折线自动生效)
    - config.view.stroke: null (去除默认外框)
    - config.axis.labelFontSize / titleFontSize: 13 / 14 (默认偏小)
    - config.legend.labelFontSize: 13

    逐项检查, spec 已有的值不覆盖. 模型完全不需要写配色代码.

    Args:
        spec_json: Vega-Lite spec JSON 字符串

    Returns:
        可能已注入默认 config 的 spec JSON 字符串. 解析失败时原样返回.

    """
    try:
        spec = json.loads(spec_json)
    except (json.JSONDecodeError, TypeError):
        return spec_json

    if not isinstance(spec, dict):
        return spec_json

    config = spec.get("config")
    if not isinstance(config, dict):
        config = {}
        spec["config"] = config

    for section, defaults in _DEFAULT_VEGA_CONFIG.items():
        existing = config.get(section)
        if not isinstance(existing, dict):
            config[section] = defaults
        else:
            for key, val in defaults.items():
                if key not in existing:
                    existing[key] = val

    if "width" not in spec:
        spec["width"] = 600
    if "height" not in spec:
        spec["height"] = 400

    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


def _estimate_text_width(text: str, font_size: float) -> float:
    """估算文本渲染宽度(px), 区分CJK全角和ASCII半角.

    CJK字符(中日韩)近似正方形宽度=fontSize, ASCII字符约0.55*fontSize.
    加10% letter-spacing 余量.
    """
    width = 0.0
    for ch in text:
        if ord(ch) > 0x2E80:  # CJK及全角符号
            width += font_size
        else:
            width += font_size * 0.55
    return width * 1.1


def _optimize_pie_labels(spec_json: str) -> str:
    """饼图标签自动避让: 检测相邻标签重叠, 隐藏较小扇区的标签.

    算法:
    1. 识别饼图模式 (顶层 theta encoding + layer 含 arc+text)
    2. 按 data values 计算每个扇区中心角
    3. 估算标签宽度, 换算标签半径处的弧长间距
    4. 相邻标签弧长间距 < 标签宽度之和/2 + padding → 重叠
    5. 重叠对中, 把 value 较小扇区的标签 opacity 设为 0
    6. legend 始终保留, 隐藏的标签通过颜色对应

    非饼图 spec 或无 text layer 时原样跳过.
    text layer 已有 opacity encoding 时不覆盖.
    """
    try:
        spec = json.loads(spec_json)
    except (json.JSONDecodeError, TypeError):
        return spec_json

    if not isinstance(spec, dict):
        return spec_json

    encoding = spec.get("encoding")
    if not isinstance(encoding, dict) or "theta" not in encoding:
        return spec_json

    theta = encoding["theta"]
    if not isinstance(theta, dict) or theta.get("type") != "quantitative":
        return spec_json

    value_field = theta.get("field", "value")
    data_values = spec.get("data", {}).get("values")
    if not isinstance(data_values, list) or len(data_values) <= 2:
        return spec_json

    layers = spec.get("layer")
    if not isinstance(layers, list):
        return spec_json

    text_layer = None
    has_arc = False
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        mark = layer.get("mark")
        if isinstance(mark, dict) and mark.get("type") == "arc":
            has_arc = True
        elif isinstance(mark, dict) and mark.get("type") == "text":
            text_layer = layer
    if not has_arc or not text_layer:
        return spec_json

    text_enc = text_layer.get("encoding", {})
    if not isinstance(text_enc, dict):
        return spec_json
    if "opacity" in text_enc:
        return spec_json  # 已有 opacity 配置, 尊重不动

    text_mark = text_layer.get("mark", {})
    if not isinstance(text_mark, dict):
        text_mark = {}
    radius = float(text_mark.get("radius", 100))
    font_size = float(text_mark.get("fontSize", 12))

    text_field_def = text_enc.get("text", {})
    if isinstance(text_field_def, dict) and "condition" in text_field_def:
        cond = text_field_def["condition"]
        label_field = cond.get("field", "label") if isinstance(cond, dict) else "label"
    elif isinstance(text_field_def, dict):
        label_field = text_field_def.get("field", "label")
    else:
        label_field = "label"

    values = [float(d.get(value_field, 0)) for d in data_values if isinstance(d, dict)]
    if len(values) != len(data_values):
        return spec_json
    total = sum(values)
    if total <= 0:
        return spec_json

    n = len(values)
    slice_angles = [v / total * 360.0 for v in values]
    centers = []
    cumulative = 0.0
    for sa in slice_angles:
        centers.append(cumulative + sa / 2.0)
        cumulative += sa

    labels = [str(d.get(label_field, "")) for d in data_values]
    widths = [_estimate_text_width(lbl, font_size) for lbl in labels]

    hide = set()
    for i in range(n):
        j = (i + 1) % n
        gap_deg = centers[j] - centers[i]
        if gap_deg < 0:
            gap_deg += 360.0
        gap_deg = min(gap_deg, 360.0 - gap_deg)
        gap_px = radius * math.radians(gap_deg)
        min_gap = (widths[i] + widths[j]) / 2.0 + 4.0
        if gap_px < min_gap and i not in hide and j not in hide:
            if values[i] <= values[j]:
                hide.add(i)
            else:
                hide.add(j)

    if not hide:
        return spec_json

    for i in hide:
        if isinstance(data_values[i], dict):
            data_values[i]["_hide_label"] = True

    text_enc["opacity"] = {
        "condition": {"test": "datum._hide_label", "value": 0},
        "value": 1,
    }

    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


_BASE_STYLE = """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      padding: 40px;
      background: white;
      font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC",
                   -apple-system, sans-serif;
    }
    .chart-container { display: inline-block; min-width: 100%; }
    .chart-title {
      text-align: center; font-size: 18px; font-weight: 600;
      color: #2c3e50; margin-bottom: 20px;
    }
"""


def build_mermaid_html(code: str, title: str | None) -> str:
    """构建 mermaid 自包含 HTML.

    mermaid.run() 渲染完成后设置 window.__rendered.
    """
    mermaid_js = _read_static("mermaid.min.js")
    title_html = f'<h2 class="chart-title">{title}</h2>' if title else ""

    # 注意: mermaid 源码放在 <pre class="mermaid"> 内, mermaid.js 会自动处理
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>{_BASE_STYLE}
    .mermaid-content {{ display: inline-block; width: fit-content; max-width: 100%; }}
    .mermaid-content svg text {{ font-weight: 500; }}
    .mermaid-content svg .node {{ filter: drop-shadow(0 2px 4px rgba(0,0,0,0.06)); }}
    .mermaid-content svg .node rect,
    .mermaid-content svg .node circle,
    .mermaid-content svg .node polygon {{ stroke-width: 1.5px; }}
    .mermaid-content svg .edgePath .path {{ stroke-width: 1.5px; opacity: 0.85; }}
</style>
</head>
<body>
<div class="chart-container">
  <div class="mermaid-content">
    {title_html}
    <pre class="mermaid">{code}</pre>
  </div>
</div>
<script>{mermaid_js}</script>
<script>
  mermaid.initialize({{
    startOnLoad: false,
    theme: "base",
    securityLevel: "loose",
    fontFamily: '"Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
    themeVariables: {{
      fontSize: '16px',
      primaryColor: "#E8F4FD",
      primaryBorderColor: "#5B8FF9",
      primaryTextColor: "#333333",
      lineColor: "#5D7092",
      clusterBkg: "#F7F9FC",
      clusterBorder: "#5B8FF9",
      edgeLabelBackground: "#FFFFFF",
      secondaryColor: "#FFF3E0",
      tertiaryColor: "#E8F8F0"
    }}
  }});
  mermaid.run()
    .then(function() {{ window.__rendered = true; }})
    .catch(function(e) {{ window.__renderError = String(e.message || e); }});
</script>
</body>
</html>"""


def build_vega_lite_html(
    spec_json: str,
    title: str | None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """构建 Vega-Lite 自包含 HTML.

    vegaEmbed() 渲染完成后设置 window.__rendered.
    spec_json 直接内联到 JS 中 (作为对象字面量).
    width/height 非 None 时, 强制注入 spec 覆盖原值.
    此外自动为 nominal/ordinal X 轴注入默认 labelAngle=-45 (未显式设置时),
    使标签在需要旋转时斜向显示而非默认垂直.
    """
    spec_json = _inject_default_x_label_angle(spec_json)
    spec_json = _inject_default_config(spec_json)
    spec_json = _optimize_pie_labels(spec_json)
    vega_js = _read_static("vega.min.js")
    vega_lite_js = _read_static("vega-lite.min.js")
    vega_embed_js = _read_static("vega-embed.min.js")
    title_html = f'<h2 class="chart-title">{title}</h2>' if title else ""

    # 仅在参数非 None 时注入, 数字字面量安全无需转义
    inject_width = f"spec.width = {width};" if width is not None else ""
    inject_height = f"spec.height = {height};" if height is not None else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>{_BASE_STYLE}
    .vega-content {{ display: inline-block; width: fit-content; max-width: 100%; }}
    .vega-embed {{ display: flex; justify-content: center; }}
    .vega-embed canvas, .vega-embed svg {{ max-width: 100%; }}
    .vega-embed .marks {{ margin: 0 auto; }}
</style>
</head>
<body>
<div class="chart-container">
  <div class="vega-content">
    {title_html}
    <div id="vis"></div>
  </div>
</div>
<script>{vega_js}</script>
<script>{vega_lite_js}</script>
<script>{vega_embed_js}</script>
<script>
  try {{
    var spec = {spec_json};
    {inject_width}
    {inject_height}
    vegaEmbed("#vis", spec, {{
      renderer: "svg",
      actions: false
    }}).then(function() {{
      window.__rendered = true;
    }}).catch(function(e) {{
      window.__renderError = String(e.message || e);
    }});
  }} catch(e) {{
    window.__renderError = String(e.message || e);
  }}
</script>
</body>
</html>"""


def build_markmap_html(
    markdown: str,
    title: str | None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """构建 markmap 自包含 HTML.

    Markdown 经 markmap-lib 转换为树, 再由 markmap-view 渲染为 SVG.
    """
    d3_js = _read_static("d3.min.js")
    markmap_lib_js = _read_static("markmap-lib.min.js")
    markmap_view_js = _read_static("markmap-view.min.js")
    title_html = f'<h2 class="chart-title">{title}</h2>' if title else ""
    markdown_json = json.dumps(markdown, ensure_ascii=False)
    svg_width = width or _DEFAULT_MARKMAP_WIDTH
    svg_height = height or _DEFAULT_MARKMAP_HEIGHT

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>{_BASE_STYLE}
    .markmap-content {{ display: inline-block; width: fit-content; max-width: 100%; }}
    #markmap {{ display: block; width: {svg_width}px; height: {svg_height}px; }}
    .markmap {{
      --markmap-font: 300 16px/20px "Noto Sans CJK SC", "Microsoft YaHei",
                       "PingFang SC", -apple-system, sans-serif;
    }}
</style>
</head>
<body>
<div class="chart-container">
  <div class="markmap-content">
    {title_html}
    <svg id="markmap" width="{svg_width}" height="{svg_height}"></svg>
  </div>
</div>
<script>{d3_js}</script>
<script>{markmap_lib_js}</script>
<script>{markmap_view_js}</script>
<script>
  (async function() {{
    try {{
      var markdown = {markdown_json};
      var transformer = new markmap.Transformer([]);
      var transformed = transformer.transform(markdown);
      var mindmap = markmap.Markmap.create("#markmap", {{
        duration: 0,
        initialExpandLevel: -1,
        maxInitialScale: 2
      }});
      await mindmap.setData(transformed.root);
      await mindmap.fit();
      await new Promise(function(resolve) {{ requestAnimationFrame(resolve); }});
      window.__rendered = true;
    }} catch(e) {{
      window.__renderError = String(e.message || e);
    }}
  }})();
</script>
</body>
</html>"""


__all__ = ["build_markmap_html", "build_mermaid_html", "build_vega_lite_html"]
