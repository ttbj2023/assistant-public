"""内置模型数据定义.

包含所有内置模型的元数据定义,无查询和业务逻辑.

model_params 记录每个模型SDK的原生参数及默认值, 参数名与SDK构造函数一致.
agent.yaml中的llm_config参数名必须与此处定义的key一致.
"""

from __future__ import annotations

from .metadata import ModelMetadata, ModelPricing
from .model_types import ModelCapability, ModelType
from .shared_models import bind_shared


def create_builtin_models() -> list[ModelMetadata]:
    """创建内置模型元数据列表."""
    models = []

    # ═══════════════════════════════════════════════════════════════════
    # Local Chat Models
    # ═══════════════════════════════════════════════════════════════════

    # Qwen3-4B-Instruct (工具筛选首选, 非思考+JSON mode)
    # 基准测试: 工具反向选择97.5%(39/40), avg=154ms, p95=194ms
    # 原生256K上下文, 本地64K配置(RTX 4070 Ti SUPER 16GB实测196K稳定)
    # SDK: langchain_ollama.ChatOllama
    models.append(
        ModelMetadata(
            id="local:qwen3:4b-instruct",
            name="Qwen3-4B-Instruct",
            provider="local",
            model_type=ModelType.CHAT,
            description="本地部署的 Qwen3-4B-Instruct模型(Ollama: qwen3:4b-instruct), 4B参数, Q4_K_M量化."
            "原生256K上下文, 本地64K配置(RTX 4070 Ti SUPER 16GB实测196K稳定)."
            "工具筛选基准测试97.5%准确率(39/40), 平均延迟154ms, JSON mode可用."
            "非思考模式, 适合低延迟结构化输出场景(工具筛选/意图分类).",
            model_params={
                "temperature": {"default": 0.0},
                "top_p": {"default": 0.9},
                "num_predict": {"default": 4096},
                "num_ctx": {"default": 65536},
                "repeat_penalty": {"default": 1.0},
                "format": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
            ],
        ),
    )

    # Qwen3.5-9B (主模型, 原生多模态+工具调用+思考模式)
    # 混合注意力: 75% Gated DeltaNet(线性) + 25% Gated Attention, 9B参数, 32层/4096维/GQA
    # 原生多模态: 图文视频输入, 视觉编码器27层/1152维
    # 原生262K上下文, 本地64K配置(RTX 4070 Ti SUPER 16GB实测128K稳定)
    # MMLU-Pro 87.8, IFEval 92.0, GPQA Diamond 81.7, C-Eval 93.0, BFCL-V4 66.1
    # SDK: langchain_ollama.ChatOllama
    models.append(
        ModelMetadata(
            id="local:qwen3.5:9b",
            name="Qwen3.5-9B",
            provider="local",
            model_type=ModelType.CHAT,
            description="本地部署的 Qwen3.5-9B模型(Ollama: qwen3.5:9b), 混合注意力架构(75% Gated DeltaNet + 25% Gated Attention), 9B参数, Q4_K_M量化."
            "原生多模态(图文视频输入), 工具调用(BFCL-V4 66.1%), 思考/非思考模式可切换."
            "64k上下文窗口(本地配置, 原生262K, RTX 4070 Ti SUPER 16GB实测128K稳定), 支持201种语言."
            "MMLU-Pro 87.8, IFEval 92.0, GPQA Diamond 81.7, C-Eval 93.0, MathVision 78.9, LiveCodeBench 65.6, OCRBench 89.2, VideoMME 84.5."
            "推理控制: reasoning(bool) + thinking_budget(int), 不支持reasoning_effort."
            "JSON mode可用(思考模式下content正常返回).",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": 20},
                "num_predict": {"default": 4096},
                "num_ctx": {"default": 65536},
                "thinking_budget": {"default": 8192},
                "repeat_penalty": {"default": 1.0},
                "reasoning": {"default": True},
                "format": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.VIDEO_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Local Embedding Models
    # ═══════════════════════════════════════════════════════════════════

    # BGE-M3 (Ollama服务, 纯CPU推理, 568M参数, 1024维, 8192 tokens上下文)
    models.append(
        ModelMetadata(
            id="local-embedding:bge-m3",
            name="BGE-M3 (Ollama, CPU)",
            provider="local-embedding",
            model_type=ModelType.EMBEDDING,
            description="本地Ollama嵌入服务,使用 BAAI/bge-m3 模型(568M参数),纯CPU推理."
            "1024维向量,8192 tokens上下文窗口,支持100+语言(中文原生优化)."
            "多功能检索: 稠密 + 稀疏 + ColBERT,适合语义检索,聚类,分类等任务.",
            model_params={},
            capabilities=[
                ModelCapability.TEXT_INPUT,
            ],
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # External Embedding Models
    # ═══════════════════════════════════════════════════════════════════

    # text-embedding-3-small (OpenAI, 1536维, 8191 tokens上下文)
    models.append(
        ModelMetadata(
            id="openai:text-embedding-3-small",
            name="Text Embedding 3 Small",
            provider="openai",
            model_type=ModelType.EMBEDDING,
            description="OpenAI最新轻量级嵌入模型,支持1536维向量(可调至512维),性能比ada-002提升5倍,多语言支持优秀",
            model_params={},
            capabilities=[
                ModelCapability.TEXT_INPUT,
            ],
        ),
    )

    # Gemini Embedding 2 Preview (Google原生API, 3072维, 8192 tokens上下文)
    models.append(
        ModelMetadata(
            id="gemini:gemini-embedding-2-preview",
            name="Gemini Embedding 2 Preview",
            provider="gemini",
            model_type=ModelType.EMBEDDING,
            description="Google首个原生多模态嵌入模型,支持文/图/视频/音频/PDF五模态统一向量空间."
            "默认3072维(MRL支持128-3072), 文本上限8192 tokens, MTEB多语言68.32分."
            "$0.20/百万token, 通过GEMINI_BASE_URL+GEMINI_API_KEY访问.切换模型需重建向量库.",
            model_params={},
            capabilities=[
                ModelCapability.TEXT_INPUT,
            ],
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # DeepSeek V4 Models (2026-04-24发布, 全系标配1M上下文, MoE架构)
    # SDK: langchain_deepseek.ChatDeepSeek
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="deepseek:deepseek-v4-flash",
            name="DeepSeek V4 Flash",
            provider="deepseek",
            model_type=ModelType.CHAT,
            description="DeepSeek V4 Flash云端模型(2026-04-24发布).284B总参/13B激活MoE架构,全系标配1M上下文,384K最大输出."
            "默认思考模式(reasoning_effort=high),通过extra_body.thinking.type=disabled可切换非思考模式."
            "注意: temperature/top_p在思考模式下被忽略; frequency_penalty/presence_penalty已在V4中废弃."
            "注意: max_tokens包含reasoning_content+content总预算, high模式建议≥16K, max模式建议≥32K."
            "官方定价: 输入$0.14/百万tokens(缓存未命中), 输出$0.28/百万tokens, 缓存命中$0.0028/百万tokens.快捷经济之选.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 1.0},
                "max_tokens": {"default": 32768},
                "reasoning_effort": {"default": None, "options": ["high", "max"]},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            pricing=ModelPricing(
                input=0.14, output=0.28, cached_input=0.0028, currency="USD"
            ),
        ),
    )

    # DeepSeek V4 Pro 在 deepseek 官方节点与阿里云 Token Plan 均有提供, 引用共享定义.
    models.append(
        bind_shared("deepseek", "deepseek-v4-pro"),
    )

    # ═══════════════════════════════════════════════════════════════════
    # GPT-5.5 (OpenAI, 1.05M上下文, 128K最大输出)
    # SDK: langchain_openai.ChatOpenAI
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="openai:gpt-5.5",
            name="GPT-5.5",
            provider="openai",
            model_type=ModelType.CHAT,
            description="OpenAI GPT-5.5旗舰模型(2026-04-24发布,通过第三方转发节点).面向编码和专业工作的全新智能等级."
            "官方规格: 1.05M上下文窗口, 128K最大输出, reasoning_effort支持none/low/medium/high/xhigh."
            "推理模型不支持temperature/top_p/frequency_penalty/presence_penalty等采样参数, 使用reasoning_effort控制推理强度."
            "已验证能力: 1)工具调用 2)图片识别(base64) 3)流式输出 4)JSON模式 5)结构化输出 6)函数调用."
            "适用于复杂对话,记忆管理和高级Agent场景."
            "注意:需要配置OPENAI_API_KEY和OPENAI_BASE_URL.图片输入推荐使用base64编码",
            model_params={
                "max_tokens": {"default": 32768},
                "reasoning_effort": {
                    "default": "medium",
                    "options": ["none", "low", "medium", "high", "xhigh"],
                },
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            # 来源: OpenAI API Pricing(developers.openai.com), Standard 短上下文档位.
            pricing=ModelPricing(
                input=5.0, output=30.0, cached_input=0.5, currency="USD"
            ),
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # GPT-5.6 (OpenAI, 2026-07-09 GA, 1.05M上下文, 128K最大输出)
    # 三档家族: Sol(旗舰) / Terra(中间档, 未纳入) / Luna(低成本高吞吐)
    # SDK: langchain_openai.ChatOpenAI
    # ═══════════════════════════════════════════════════════════════════

    # GPT-5.6 Sol (旗舰, 与GPT-5.5同价)
    models.append(
        ModelMetadata(
            id="openai:gpt-5.6-sol",
            name="GPT-5.6 Sol",
            provider="openai",
            model_type=ModelType.CHAT,
            description="OpenAI GPT-5.6 Sol旗舰模型(2026-07-09发布, 通过第三方转发节点)."
            "GPT-5.6家族三档中的旗舰档, 面向复杂推理, 编码和长程Agent任务."
            "官方规格: 1.05M上下文窗口, 128K最大输出, reasoning_effort支持none/low/medium/high/xhigh/max."
            "推理模型不支持temperature/top_p等采样参数, 使用reasoning_effort控制推理强度."
            "已验证能力: 1)工具调用 2)图片识别 3)流式输出 4)JSON模式."
            "注意: 需要配置OPENAI_API_KEY和OPENAI_BASE_URL.",
            model_params={
                "max_tokens": {"default": 32768},
                "reasoning_effort": {
                    "default": "medium",
                    "options": ["none", "low", "medium", "high", "xhigh", "max"],
                },
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            # 来源: OpenAI API Pricing, Standard 短上下文档位. 缓存读取90%折扣.
            pricing=ModelPricing(
                input=5.0, output=30.0, cached_input=0.5, currency="USD"
            ),
        ),
    )

    # GPT-5.6 Luna (低成本高吞吐, 分类/路由/高并发场景)
    models.append(
        ModelMetadata(
            id="openai:gpt-5.6-luna",
            name="GPT-5.6 Luna",
            provider="openai",
            model_type=ModelType.CHAT,
            description="OpenAI GPT-5.6 Luna低成本模型(2026-07-09发布, 通过第三方转发节点)."
            "GPT-5.6家族三档中的轻量档, 适合分类, 意图路由, 内容审核等高吞吐任务."
            "官方规格: 1.05M上下文窗口, 128K最大输出, reasoning_effort支持none/low/medium/high/xhigh/max."
            "推理模型不支持temperature/top_p等采样参数, 使用reasoning_effort控制推理强度."
            "已验证能力: 1)工具调用 2)图片识别 3)流式输出 4)JSON模式."
            "注意: 需要配置OPENAI_API_KEY和OPENAI_BASE_URL.",
            model_params={
                "max_tokens": {"default": 32768},
                "reasoning_effort": {
                    "default": "medium",
                    "options": ["none", "low", "medium", "high", "xhigh", "max"],
                },
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            # 来源: OpenAI API Pricing, Standard 短上下文档位. 缓存读取90%折扣.
            pricing=ModelPricing(
                input=1.0, output=6.0, cached_input=0.1, currency="USD"
            ),
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Qwen3.7-Max (阿里云百炼DashScope, 1M上下文, 2026-05-20发布)
    # SDK: langchain_openai.ChatOpenAI (OpenAI兼容端点)
    # 在 dashscope 与 aliyun-token-plan 均有提供, 引用共享定义.
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared("dashscope", "qwen3.7-max"),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Gemini Models (Google, 1M上下文)
    # SDK: langchain_google_genai.ChatGoogleGenerativeAI
    # ═══════════════════════════════════════════════════════════════════

    # Gemini 3.5 Flash (2026-05-19发布, Agent优先设计)
    models.append(
        ModelMetadata(
            id="gemini:gemini-3.5-flash",
            name="Gemini 3.5 Flash",
            provider="gemini",
            model_type=ModelType.CHAT,
            description="Google Gemini 3.5 Flash 云端模型(2026-05-19 Google I/O发布)."
            "Agent优先设计, 近Pro级能力, 原生多模态支持(文本,图像,音频,视频,PDF)."
            "1M上下文窗口, 65K最大输出, 支持思考模式/JSON模式/工具调用."
            "官方定价: $1.50/M输入, $9.00/M输出, 上下文缓存$0.15/M.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": None},
                "max_output_tokens": {"default": 16384},
                "thinking_level": {"default": None},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.AUDIO_INPUT,
                ModelCapability.VIDEO_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            pricing=ModelPricing(
                input=1.50, output=9.00, cached_input=0.15, currency="USD"
            ),
        ),
    )

    # Gemini 2.5 Flash Lite (工具调用专用, 支持grounding权限)
    models.append(
        ModelMetadata(
            id="gemini:gemini-2.5-flash-lite",
            name="Gemini 2.5 Flash Lite (工具调用专用)",
            provider="gemini",
            model_type=ModelType.CHAT,
            description="Google Gemini 2.5 Flash Lite 云端模型,专用于工具调用(支持grounding权限).支持1M上下文窗口,强大的语言理解能力."
            "支持多模态输入(文本/图像), 注意:此模型保留用于工具引擎(ResearchAssistant/WebExtractor)."
            "开发测试阶段暂不使用,后期复杂场景下替换本地模型",
            model_params={
                "temperature": {"default": 0.1},
                "top_p": {"default": 1.0},
                "max_output_tokens": {"default": 4096},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            # 来源: Google Gemini API Pricing(ai.google.dev), Standard 档位.
            pricing=ModelPricing(
                input=0.10, output=0.40, cached_input=0.01, currency="USD"
            ),
        ),
    )

    # Gemini 3.1 Flash-Lite Preview (2026-03-03发布, 高性价比)
    models.append(
        ModelMetadata(
            id="gemini:gemini-3.1-flash-lite-preview",
            name="Gemini 3.1 Flash-Lite Preview",
            provider="gemini",
            model_type=ModelType.CHAT,
            description="Google Gemini 3.1 Flash-Lite 预览版(2026-03-03发布).专为高并发低延迟场景优化,峰值输出363 tokens/秒."
            "1M上下文窗口,原生多模态支持(文本/图像/音频/视频), MMMU-Pro 76.8%."
            "官方定价: $0.25/M输入, $1.50/M输出, 上下文缓存$0.025/M, 极致性价比."
            "适合图片描述fallback,多模态理解,高吞吐场景.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": None},
                "max_output_tokens": {"default": 8192},
                "thinking_level": {"default": None},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.AUDIO_INPUT,
                ModelCapability.VIDEO_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            pricing=ModelPricing(
                input=0.25, output=1.50, cached_input=0.025, currency="USD"
            ),
        ),
    )

    # Gemma 4 26B A4B (MoE, 2026-04-02发布, 开源Apache 2.0)
    models.append(
        ModelMetadata(
            id="gemini:gemma-4-26b-a4b-it",
            name="Gemma 4 26B A4B",
            provider="gemini",
            model_type=ModelType.CHAT,
            description="Google Gemma 4 26B A4B开源模型(2026-04-02发布).25.2B总参/3.8B激活MoE架构(8 active/128 total+1 shared), Apache 2.0许可."
            "256K上下文窗口, 32K最大输出, 原生多模态(文本/图像), 函数调用, 结构化输出(JSON Schema), 可配置思考模式."
            "MoE架构使推理速度接近4B模型, 远快于31B Dense, 适合高吞吐场景."
            "MMLU-Pro 82.6%, AIME 2026 88.3%, GPQA Diamond 82.3%, LiveCodeBench v6 77.1%, MMMU-Pro 73.8%."
            "采样推荐: temperature=1.0, top_p=0.95, top_k=64."
            "思考控制: 仅thinkingLevel=minimal可用; thinkingBudget不可用."
            "通过Gemini API免费额度使用.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": 64},
                "max_output_tokens": {"default": 8192},
                "thinking_level": {"default": None, "options": ["minimal"]},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
        ),
    )

    # Gemma 4 31B (Dense, 2026-04-02发布, 开源Apache 2.0)
    models.append(
        ModelMetadata(
            id="gemini:gemma-4-31b-it",
            name="Gemma 4 31B",
            provider="gemini",
            model_type=ModelType.CHAT,
            description="Google Gemma 4 31B开源模型(2026-04-02发布).30.7B参数Dense架构(60层), Apache 2.0许可."
            "256K上下文窗口, 32K最大输出, 原生多模态(文本/图像), 函数调用, 结构化输出(JSON Schema), 可配置思考模式."
            "Dense架构提供最强推理深度, 适合需要高质量输出的复杂任务."
            "MMLU-Pro 85.2%, AIME 2026 89.2%, GPQA Diamond 84.3%, LiveCodeBench v6 80.0%, MMMU-Pro 76.9%."
            "采样推荐: temperature=1.0, top_p=0.95, top_k=64."
            "思考控制: 仅thinkingLevel=minimal可用; thinkingBudget不可用."
            "通过Gemini API免费额度使用.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": 64},
                "max_output_tokens": {"default": 8192},
                "thinking_level": {"default": None, "options": ["minimal"]},
                "stop": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Doubao Seed / Seedream / Seedance (火山引擎Ark API / Agent Plan)
    # 同一底层模型通过按量节点与订阅节点分别暴露, 引用共享定义.
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared(
            "doubao",
            "doubao-seed-2.0-pro",
            endpoint_name="doubao-seed-2-0-pro-260215",
        ),
    )

    models.append(
        bind_shared(
            "doubao",
            "doubao-seed-2.0-mini",
            endpoint_name="doubao-seed-2-0-mini-260428",
        ),
    )

    models.append(
        bind_shared(
            "doubao",
            "doubao-seedream-5.0",
            endpoint_name="doubao-seedream-5-0-260128",
        ),
    )

    models.append(
        bind_shared(
            "doubao",
            "doubao-seedance-2.0",
            endpoint_name="doubao-seedance-2-0-260128",
        ),
    )

    models.append(
        bind_shared(
            "doubao",
            "doubao-seedance-2.0-fast",
            endpoint_name="doubao-seedance-2-0-fast-260128",
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # MiniMax M2.7 (230B MoE, OpenAI兼容API, 使用Anthropic SDK)
    # SDK: langchain_anthropic.ChatAnthropic
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="minimax:MiniMax-M2.7",
            name="MiniMax M2.7",
            provider="minimax",
            model_type=ModelType.CHAT,
            description="MiniMax M2.7旗舰模型(2026).230B总参/10B激活MoE架构,1M上下文窗口,204.8K最大输出."
            "兼容OpenAI和Anthropic API格式.默认开启thinking推理,支持工具调用,流式输出,JSON模式."
            "SWE-Pro 56.22%(接近GPT-5.3-Codex),编码和工程能力突出."
            "官方定价: $0.30/M输入, $1.20/M输出, 缓存命中$0.03/M; 同时提供Token Plan免费额度."
            "适合编码辅助和Agent场景."
            "需要配置MINIMAX_API_KEY环境变量.",
            model_params={
                "temperature": {"default": 1.0},
                "top_p": {"default": 0.95},
                "top_k": {"default": None},
                "max_tokens": {"default": 8192},
                "thinking": {"default": None},
                "stop_sequences": {"default": None},
            },
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            pricing=ModelPricing(
                input=0.30, output=1.20, cached_input=0.03, currency="USD"
            ),
        ),
    )

    return models


def create_ark_agent_plan_models() -> list[ModelMetadata]:
    """创建Ark Agent Plan订阅模型列表.

    火山引擎Agent Plan订阅节点, 独立API Key, OpenAI兼容端点.
    """
    models: list[ModelMetadata] = []

    seed_chat_params = {
        "temperature": {"default": 0.3},
        "top_p": {"default": 0.95},
        "max_tokens": {"default": 16384},
        "reasoning_effort": {
            "default": "minimal",
            "options": ["minimal", "low", "medium", "high"],
        },
        "stop": {"default": None},
    }

    seed_chat_caps = [
        ModelCapability.TEXT_INPUT,
        ModelCapability.IMAGE_INPUT,
        ModelCapability.REASONING,
        ModelCapability.STREAMING,
        ModelCapability.JSON_MODE,
        ModelCapability.TOOL_CALLING,
    ]

    # ═══════════════════════════════════════════════════════════════════
    # Doubao Seed 2.0 Pro / Mini (Agent Plan, 共享定义)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared("ark-agent-plan", "doubao-seed-2.0-pro"),
    )

    models.append(
        bind_shared("ark-agent-plan", "doubao-seed-2.0-mini"),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Doubao Seed 2.0 Lite (Agent Plan, 仅订阅节点提供)
    # 全模态理解模型, 支持视频/图像/音频/文本.
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="ark-agent-plan:doubao-seed-2.0-lite",
            name="Doubao Seed 2.0 Lite",
            provider="ark-agent-plan",
            model_type=ModelType.CHAT,
            description="豆包Seed 2.0 Lite(Agent Plan).全模态理解模型, 支持视频/图像/音频/文本."
            "集成音画协同分析和长视频时序检索能力.",
            model_params=seed_chat_params,
            capabilities=[
                *seed_chat_caps,
                ModelCapability.VIDEO_INPUT,
                ModelCapability.AUDIO_INPUT,
            ],
            # 来源: 火山引擎官方定价(元/百万tokens, 分段计费). 此处取 ≤32K 档作为模型级参考.
            # 完整分段(输入/输出/缓存): ≤32K 0.6/3.6/0.12; 32K-128K 0.9/5.4/0.18; 128K-256K 1.8/10.8/0.36.
            pricing=ModelPricing(
                input=0.6, output=3.6, cached_input=0.12, currency="CNY"
            ),
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Doubao Seedream 5.0 Lite (Agent Plan)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared(
            "ark-agent-plan",
            "doubao-seedream-5.0",
            endpoint_name="doubao-seedream-5.0-lite",
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Doubao Seedance 2.0 / Fast (Agent Plan)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared("ark-agent-plan", "doubao-seedance-2.0"),
    )

    models.append(
        bind_shared("ark-agent-plan", "doubao-seedance-2.0-fast"),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Kimi K2.6 (Agent Plan, 共享定义)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared(
            "ark-agent-plan",
            "kimi-k2.6",
            endpoint_name="kimi-k2.6",
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # DeepSeek V4 Pro (Agent Plan, 共享定义)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared("ark-agent-plan", "deepseek-v4-pro"),
    )

    return models


def create_aliyun_token_plan_models() -> list[ModelMetadata]:
    """创建阿里云Token Plan订阅模型列表.

    阿里云百炼Token Plan订阅节点, 独立API Key, OpenAI兼容端点.
    base_url: https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    """
    models: list[ModelMetadata] = []

    # Qwen3.7-Plus 仅 Token Plan 提供, 保留本地定义.
    qwen37_chat_params = {
        "temperature": {"default": 0.6},
        "top_p": {"default": 0.95},
        "top_k": {"default": 20},
        "repetition_penalty": {"default": 1.05},
        "max_tokens": {"default": 32768},
        "stop": {"default": None},
        "extra_body": {
            "default": {"enable_thinking": True, "thinking_budget": None},
        },
    }

    # ═══════════════════════════════════════════════════════════════════
    # Qwen3.7-Max / DeepSeek V4 Pro / GLM-5.2 (共享定义)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared("aliyun-token-plan", "qwen3.7-max"),
    )

    models.append(
        bind_shared("aliyun-token-plan", "deepseek-v4-pro"),
    )

    models.append(
        bind_shared(
            "aliyun-token-plan",
            "glm-5.2",
            endpoint_name="glm-5.2",
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # Qwen3.7-Plus (Token Plan) - 多模态智能体模型, 仅 Token Plan 提供
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="aliyun-token-plan:qwen3.7-plus",
            name="Qwen3.7-Plus (Token Plan)",
            provider="aliyun-token-plan",
            model_type=ModelType.CHAT,
            description="阿里云百炼Qwen3.7-Plus多模态智能体模型(Token Plan订阅).'能看, 能想, 能动手',"
            "支持图像与视频输入理解, 能力与成本均衡, 完整工具调用支持, 1M上下文适合大型代码库."
            "1M上下文, 64k最大输出, 256k思考预算."
            "思考控制: enable_thinking(bool)+thinking_budget(int), 经extra_body透传."
            "官方参数: 结构化输出支持, 内置工具支持."
            "适合多模态Agent场景.",
            model_params=qwen37_chat_params,
            capabilities=[
                ModelCapability.TEXT_INPUT,
                ModelCapability.IMAGE_INPUT,
                ModelCapability.VIDEO_INPUT,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
                ModelCapability.JSON_MODE,
                ModelCapability.TOOL_CALLING,
            ],
            # 来源: 阿里云百炼官方定价(元/百万tokens, 分段计费). 此处取 <256K 档作为模型级参考.
            # 完整分段(输入/输出/缓存): <256K 2/8/0.4; 256K-1M 6/24/1.2.
            pricing=ModelPricing(
                input=2.0, output=8.0, cached_input=0.4, currency="CNY"
            ),
        ),
    )

    return models


def create_scnet_models() -> list[ModelMetadata]:
    """创建超算互联网(scnet.cn)聚合订阅模型列表.

    scnet.cn 多模型聚合服务, 独立 API Key, OpenAI 兼容端点.
    base_url: https://api.scnet.cn/api/llm/v1
    """
    models: list[ModelMetadata] = []

    # 通用对话参数: OpenAI 兼容聚合端点, 采样参数保持通用保守值
    scnet_chat_params = {
        "temperature": {"default": 0.7},
        "top_p": {"default": 0.95},
        "max_tokens": {"default": 32768},
        "stop": {"default": None},
    }

    scnet_chat_caps = [
        ModelCapability.TEXT_INPUT,
        ModelCapability.REASONING,
        ModelCapability.STREAMING,
        ModelCapability.JSON_MODE,
        ModelCapability.TOOL_CALLING,
    ]

    # ═══════════════════════════════════════════════════════════════════
    # Kimi-K2.6 / GLM-5.2 (共享定义)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        bind_shared(
            "scnet",
            "kimi-k2.6",
            endpoint_name="Kimi-K2.6",
        ),
    )

    models.append(
        bind_shared(
            "scnet",
            "glm-5.2",
            endpoint_name="GLM-5.2",
        ),
    )

    # ═══════════════════════════════════════════════════════════════════
    # MiniMax-M3 / MiMo-V2.5-Pro (scnet.cn 独占)
    # ═══════════════════════════════════════════════════════════════════

    models.append(
        ModelMetadata(
            id="scnet:MiniMax-M3",
            name="MiniMax M3",
            provider="scnet",
            model_type=ModelType.CHAT,
            description="MiniMax M3模型(经scnet.cn聚合订阅)."
            "思考推理模型, 默认返回reasoning_content, 支持工具调用, JSON模式, 流式输出."
            "OpenAI兼容端点(scnet.cn聚合), 需配置SCNET_API_KEY环境变量.",
            model_params=scnet_chat_params,
            capabilities=scnet_chat_caps,
        ),
    )

    models.append(
        ModelMetadata(
            id="scnet:MiMo-V2.5-Pro",
            name="MiMo V2.5 Pro",
            provider="scnet",
            model_type=ModelType.CHAT,
            description="小米MiMo V2.5 Pro模型(经scnet.cn聚合订阅)."
            "思考推理模型, 默认返回reasoning_content, 支持工具调用, JSON模式, 流式输出."
            "官方定价(小米): 输入3.0元/百万tokens(缓存未命中), 输出6.0元/百万tokens, 缓存命中0.025元/百万tokens."
            "OpenAI兼容端点(scnet.cn聚合), 需配置SCNET_API_KEY环境变量.",
            model_params=scnet_chat_params,
            capabilities=scnet_chat_caps,
            pricing=ModelPricing(
                input=3.0, output=6.0, cached_input=0.025, currency="CNY"
            ),
        ),
    )

    return models
