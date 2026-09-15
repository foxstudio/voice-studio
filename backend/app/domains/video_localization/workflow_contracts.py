from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION,
    SOURCE_AUDIO_WORKFLOW_VERSION,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION,
    STEM_SEPARATION_WORKFLOW_VERSION,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION,
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION,
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (
    SEMANTIC_TTS_GROUPING_INPUT_SCHEMA_VERSION,
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_raw_step import (
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_initial_analysis_step import (
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION,
    ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_document_understanding_managed_contracts import (
    ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_visual_evidence_managed_contracts import (
    ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_research_evidence_managed_contracts import (
    ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_entity_normalization_managed_contracts import (
    ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_section_review_managed_contracts import (
    ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_review_decisions_managed_contracts import (
    ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_whole_recheck_step import (
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_whole_recheck_managed_contracts import (
    ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_asr_transcript_quality_gate_step import (
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_transcript_quality_gate_managed_contracts import (
    ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION,
)
from app.schemas.video_localization_dub_subtitle_step import (
    DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION,
)


class WorkflowAtomicTaskDefinition(BaseModel):
    """Stable public description of one independently callable workflow task."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    order: int = Field(ge=0)
    execution: Literal["parallel", "serial", "join"] = "serial"
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = Field(
        default=False,
        description="是否为按需执行的可选子任务；任务面板仍保留显示，未触发时明确标为跳过。",
    )
    dependency_mode: Literal["all", "latest_completed"] = Field(
        default="all",
        description=(
            "依赖解释方式：all 表示全部依赖都要完成；latest_completed "
            "表示从候选依赖中使用最后一个实际完成的结果。"
        ),
    )
    output_contract_version: str | None = None


class WorkflowStageDefinition(BaseModel):
    """A user-facing parent stage that groups related atomic tasks."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    order: int = Field(ge=0)
    atomic_tasks: list[WorkflowAtomicTaskDefinition] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """Versioned workflow contract shared by the UI, API and other agents."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "video-localization-workflow-v1",
        "video-localization-workflow-v2",
        "source-audio-workflow-v1",
        "stem-separation-workflow-v1",
        "reference-candidates-workflow-v1",
        "speaker-diarization-workflow-v1",
        "asr-raw-development-workflow-v1",
        "asr-initial-analysis-development-workflow-v1",
        "asr-document-understanding-development-workflow-v1",
        "asr-visual-evidence-development-workflow-v1",
        "asr-research-evidence-development-workflow-v1",
        "asr-entity-normalization-development-workflow-v1",
        "asr-section-review-development-workflow-v1",
        "asr-review-decisions-development-workflow-v1",
        "asr-whole-recheck-development-workflow-v1",
        "asr-transcript-quality-gate-development-workflow-v1",
        "dub-subtitle-workflow-v1",
        "semantic-tts-grouping-workflow-v2",
    ] = "video-localization-workflow-v1"
    workflow_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    stages: list[WorkflowStageDefinition] = Field(default_factory=list)


SOURCE_AUDIO_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=SOURCE_AUDIO_WORKFLOW_VERSION,
    workflow_id="source-audio",
    label="提取原始音轨",
    stages=[
        WorkflowStageDefinition(
            id="source_audio",
            label="提取原始音轨",
            description=(
                "从当前源视频提取可供后续听写、分离和配音使用的原始音轨，"
                "并在同一次正式提交中保存媒体结果和任务成功状态。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="extract_source_audio",
                    label="提取并保存原始音轨",
                    description=(
                        "锁定当前源视频，完成本地音轨提取和结构校验，"
                        "再把结果与任务状态一起写入项目。"
                    ),
                    order=10,
                    output_contract_version=(
                        SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION
                    ),
                )
            ],
        )
    ],
)

STEM_SEPARATION_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=STEM_SEPARATION_WORKFLOW_VERSION,
    workflow_id="stem-separation",
    label="分离人声与背景声",
    stages=[
        WorkflowStageDefinition(
            id="stem_separation",
            label="分离人声与背景声",
            description=(
                "锁定当前源音轨，用本地分离引擎生成可校验的人声和背景声，"
                "并在同一次正式提交中保存媒体结果和任务成功状态。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="separate_stems",
                    label="生成并保存双轨分离结果",
                    description=(
                        "固定源音轨指纹，生成两条内容校验过的音轨；"
                        "服务中断后优先复用同一任务已完成的媒体文件。"
                    ),
                    order=10,
                    output_contract_version=(
                        STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION
                    ),
                )
            ],
        )
    ],
)

REFERENCE_CANDIDATES_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    workflow_id="reference-candidates",
    label="生成参考音候选",
    stages=[
        WorkflowStageDefinition(
            id="reference_candidates",
            label="生成参考音候选",
            description=(
                "锁定当前干净人声和有序候选 cue，生成可人工复核的参考音，"
                "并在同一次正式提交中关联 cue、说话人与任务成功状态。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="generate_reference_candidates",
                    label="裁切并保存参考音候选",
                    description=(
                        "从干净人声裁出候选片段，校验每个文件内容；"
                        "服务中断后优先复用同一任务已完成的媒体。"
                    ),
                    order=10,
                    output_contract_version=(
                        REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION
                    ),
                )
            ],
        )
    ],
)

SPEAKER_DIARIZATION_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    workflow_id="speaker-diarization",
    label="区分说话人",
    stages=[
        WorkflowStageDefinition(
            id="speaker_diarization",
            label="区分说话人",
            description=(
                "锁定实际输入音轨、内容指纹与人数提示，"
                "使用本地引擎生成匿名说话人分组；"
                "开发结果不会修改正式字幕或说话人。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="diarization",
                    label="区分并校验匿名说话人",
                    description=(
                        "分析声音特征、校验分组质量并保存无路径的"
                        "版本化结果；服务中断后复用已提交的制品。"
                    ),
                    order=10,
                    output_contract_version=(
                        SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION
                    ),
                )
            ],
        )
    ],
)


ASR_RAW_DEVELOPMENT_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    workflow_id="asr-raw-development",
    label="原始听写（开发断点）",
    stages=[
        WorkflowStageDefinition(
            id="raw_asr",
            label="生成原始听写",
            description=(
                "锁定实际输入音轨、内容指纹、引擎和语言，"
                "只生成未经校对的文字与粗时间片段。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="asr",
                    label="生成并校验原始听写",
                    description=(
                        "调用当前听写引擎，校验文字、片段和时间顺序，"
                        "再保存无路径的版本化结果；不会修改正式字幕。"
                    ),
                    order=10,
                    output_contract_version=(
                        ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
                    ),
                )
            ],
        )
    ],
)


ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-initial-analysis-development",
        label="初始语音分析（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="initial_analysis",
                label="初始语音分析",
                description=(
                    "对同一份实际音轨并行生成原始听写和匿名说话人，"
                    "再用确定性规则汇合；不会修改正式字幕或说话人。"
                ),
                order=10,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="asr",
                        label="生成原始听写",
                        description=(
                            "锁定音频、引擎和语言，生成未经校对的"
                            "文字与粗时间片段。"
                        ),
                        order=10,
                        execution="parallel",
                        output_contract_version=(
                            ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    ),
                    WorkflowAtomicTaskDefinition(
                        id="diarization",
                        label="区分说话人",
                        description=(
                            "锁定同一音频与人数提示，生成匿名说话人；"
                            "普通失败会明确降级，不阻断可用听写。"
                        ),
                        order=20,
                        execution="parallel",
                        output_contract_version=(
                            ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION
                        ),
                    ),
                    WorkflowAtomicTaskDefinition(
                        id="initial_analysis_join",
                        label="汇合听写与说话人",
                        description=(
                            "验证两条分支的制品指纹和音频身份，"
                            "生成带匿名说话人的讲话片段。"
                        ),
                        order=25,
                        execution="join",
                        depends_on=["asr", "diarization"],
                        output_contract_version=(
                            ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
                        ),
                    ),
                ],
            )
        ],
    )
)


ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id=(
            "asr-document-understanding-development"
        ),
        label="全文理解（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="理解全文",
                description=(
                    "从指定初始分析任务锁定讲话片段和场景说明，"
                    "只理解主题、逻辑和复查范围；不联网、不修改正式文字。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="understand_document",
                        label="理解全文并规划复查",
                        description=(
                            "逐次持久化模型调用并在本地确定性校验，"
                            "未知的付费结果不会自动重发。"
                        ),
                        order=30,
                        output_contract_version=(
                            ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-visual-evidence-development",
        label="画面取证（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="读取画面证据",
                description=(
                    "锁定指定全文理解结果和源视频身份，只读取问题附近"
                    "直接可见的信息；不识别人、不决定规范名称、不修改文字。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="visual_evidence",
                        label="画面取证",
                        description=(
                            "逐轮持久化截图和模型调用；服务中断后复用已完成"
                            "步骤，未知的付费结果不会自动重发。"
                        ),
                        order=35,
                        output_contract_version=(
                            ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-research-evidence-development",
        label="资料查询（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="核对名称与背景",
                description=(
                    "锁定全文理解和可选画面证据，只查询已提出的疑点；"
                    "不决定规范名称，不修改原听写。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="research",
                        label="查询必要资料",
                        description=(
                            "逐次持久化搜索和模型判断；服务中断后复用"
                            "已完成步骤，未知的付费结果不会自动重发。"
                        ),
                        order=40,
                        output_contract_version=(
                            ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-entity-normalization-development",
        label="名称与术语统一（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="统一名称与术语",
                description=(
                    "锁定资料查询结果和提交时词表，只应用有证据支持的"
                    "规范名称；不改变片段时间和原始听写。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="normalize_entities",
                        label="统一名称与术语",
                        description=(
                            "词表规则本地执行；必要的规范名判断和变体映射"
                            "逐次持久化，未知的付费结果不会自动重发。"
                        ),
                        order=45,
                        output_contract_version=(
                            ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-section-review-development",
        label="第 1 轮分段复查（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="第 1 轮分段复查",
                description=(
                    "锁定名称统一与全文理解结果，逐段查找可能的听写问题；"
                    "只提出疑点，不修改字幕。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="section_review_r1",
                        label="第 1 轮分段复查",
                        description=(
                            "各区块可以并行检查；每次模型尝试独立持久化，"
                            "服务恢复时只补跑缺失区块。"
                        ),
                        order=50,
                        execution="parallel",
                        output_contract_version=(
                            ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-review-decisions-development",
        label="第 1 轮复查结论汇总（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="汇总第 1 轮修改",
                description=(
                    "锁定分段复查结果，只判断其中已经提出的疑点；"
                    "本地安全规则决定修改能否应用。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="review_decisions_r1",
                        label="汇总第 1 轮修改",
                        description=(
                            "首轮判断和缺项补充分别持久化；"
                            "服务恢复时不会重复已完成的模型请求。"
                        ),
                        order=60,
                        output_contract_version=(
                            ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-whole-recheck-development",
        label="第 1 轮全文复核（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="第 1 轮全文复核",
                description=(
                    "锁定修改汇总与全文理解结果，只判断是否可以结束"
                    "自动复查；字幕文字和时间码保持只读。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="whole_recheck_r1",
                        label="第 1 轮全文复核",
                        description=(
                            "每次模型尝试独立持久化；结构错误才创建"
                            "第二次尝试，服务恢复不会重复成功请求。"
                        ),
                        order=70,
                        output_contract_version=(
                            ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)

ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_DEFINITION = (
    WorkflowDefinition(
        schema_version=(
            ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        workflow_id="asr-transcript-quality-gate-development",
        label="进入校时前检查（开发断点）",
        stages=[
            WorkflowStageDefinition(
                id="transcript_review",
                label="进入校时前检查",
                description=(
                    "锁定最后一次全文复核结果，只用本地规则判断"
                    "文字与结构是否可以进入逐词校时。"
                ),
                order=20,
                atomic_tasks=[
                    WorkflowAtomicTaskDefinition(
                        id="transcript_quality_gate",
                        label="进入校时前检查",
                        description=(
                            "本地确定性重算并持久化结果；不调用模型、"
                            "不读取当前草稿，也不修改字幕。"
                        ),
                        order=290,
                        output_contract_version=(
                            ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION
                        ),
                    )
                ],
            )
        ],
    )
)


ASR_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version="video-localization-workflow-v2",
    workflow_id="asr",
    label="听写与字幕准备",
    stages=[
        WorkflowStageDefinition(
            id="initial_analysis",
            label="生成原始听写",
            description="先把讲话转换为带粗略时间范围的源语言文本；局部未识别会明确提醒，但不丢弃其余可用结果。",
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="asr",
                    label="生成原始听写",
                    description="把讲话转成原始文字和粗时间片段，不在这一步校对或改写。",
                    order=10,
                    execution="parallel",
                    output_contract_version="asr-raw-v2",
                ),
                WorkflowAtomicTaskDefinition(
                    id="diarization",
                    label="区分匿名说话人",
                    description=(
                        "默认对同一音轨并行分析声纹；只有检测到多个稳定声音角色"
                        "时才给字幕加匿名标签，也不猜真实人物姓名。"
                    ),
                    order=20,
                    execution="parallel",
                    optional=True,
                    output_contract_version=(
                        ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="initial_analysis_join",
                    label="汇合听写与说话人",
                    description=(
                        "核对两条并行结果来自同一音轨；单一声音只保留分析证据，"
                        "多个稳定声音才整理为谁在什么时候说了什么。"
                    ),
                    order=25,
                    execution="join",
                    depends_on=["asr", "diarization"],
                    optional=True,
                    output_contract_version=(
                        ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
                    ),
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="transcript_review",
            label="理解与校对全文",
            description="理解整段对话、核对名称，并按需要复查原始听写。",
            order=20,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="understand_document",
                    label="理解全文并规划复查",
                    description="先通读整篇内容，明确主题、人物关系和需要重点复查的地方。",
                    order=30,
                    depends_on=["asr"],
                    output_contract_version="asr-document-understanding-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="visual_evidence",
                    label="画面取证",
                    description=(
                        "只在确实需要时查看对应时间附近的画面，"
                        "读取字幕条、图表和其他直接可见信息。"
                    ),
                    order=35,
                    depends_on=["understand_document"],
                    optional=True,
                    output_contract_version="asr-visual-evidence-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="research",
                    label="核对名称与背景",
                    description="只查询确实需要核对的疑点；每轮后判断证据是否足够，再决定是否继续。",
                    order=40,
                    depends_on=["understand_document", "visual_evidence"],
                    optional=True,
                    output_contract_version="asr-research-evidence-v2",
                ),
                WorkflowAtomicTaskDefinition(
                    id="normalize_entities",
                    label="统一名称与术语",
                    description="根据项目术语表和查证证据统一规范写法，只修改有明确依据的片段。",
                    order=45,
                    depends_on=["research"],
                    output_contract_version="asr-entity-normalization-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="section_review_r1",
                    label="定位听写疑点",
                    description="根据全文理解和查证结果，一次覆盖全文，只定位可能听错的内容。",
                    order=50,
                    depends_on=[
                        "normalize_entities",
                        "understand_document",
                    ],
                    output_contract_version="asr-section-review-v4",
                ),
                WorkflowAtomicTaskDefinition(
                    id="review_decisions_r1",
                    label="重听并应用明确修改",
                    description=(
                        "只对已定位的高价值疑点截取短音频重新识别，结合上下文"
                        "做一次最终判断；拿不准时保留原文。"
                    ),
                    order=60,
                    depends_on=["section_review_r1"],
                    output_contract_version="asr-review-decisions-v4",
                ),
                WorkflowAtomicTaskDefinition(
                    id="whole_recheck_r1",
                    label="本地收尾检查",
                    description=(
                        "不用模型，只检查本轮决定、片段引用和相邻结构是否完整；"
                        "不再开启第二轮全文审核。"
                    ),
                    order=70,
                    depends_on=[
                        "review_decisions_r1",
                        "understand_document",
                    ],
                    output_contract_version="asr-whole-recheck-v3",
                ),
                WorkflowAtomicTaskDefinition(
                    id="transcript_quality_gate",
                    label="进入校时前检查",
                    description=(
                        "确认全文和时间结构可以安全进入校时；低把握文字采用"
                        "当前最高概率结果继续，并保留建议复听提示。"
                    ),
                    order=290,
                    depends_on=["whole_recheck_r1"],
                    output_contract_version="asr-transcript-quality-gate-v2",
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="subtitle_timing",
            label="时间与字幕整理",
            description="把确认后的文字贴合原音时间，再根据停顿生成可用字幕。",
            order=30,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="alignment",
                    label="对齐逐词时间",
                    description="为每个词确定在原音中的出现时间。",
                    order=300,
                    depends_on=["transcript_quality_gate"],
                    output_contract_version="asr-alignment-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="audio_boundaries",
                    label="分析声音停顿",
                    description="找出适合断句的自然停顿和声音变化。",
                    order=310,
                    depends_on=["alignment"],
                    output_contract_version="asr-audio-boundaries-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="boundary_review",
                    label="本地确定字幕断句",
                    description="综合时间、标点、说话人和停顿，确定字幕如何分行分段。",
                    order=320,
                    depends_on=[
                        "alignment",
                        "audio_boundaries",
                    ],
                    output_contract_version="asr-boundary-review-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="subtitle_track",
                    label="生成并检查字幕轨",
                    description="把最终字幕写入时间轴，并检查空文本和时间重叠。",
                    order=330,
                    depends_on=["boundary_review"],
                    output_contract_version="asr-subtitle-track-v1",
                ),
            ],
        ),
    ],
)


def asr_development_replay_tasks() -> tuple[WorkflowAtomicTaskDefinition, ...]:
    """Return the superset available to explicit development-only replays."""

    ordered: list[WorkflowAtomicTaskDefinition] = []
    seen: set[str] = set()
    for definition in (
        ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_DEFINITION,
        ASR_WORKFLOW_DEFINITION,
    ):
        for stage in definition.stages:
            for task in stage.atomic_tasks:
                if task.id in seen:
                    continue
                seen.add(task.id)
                ordered.append(task)
    return tuple(ordered)


DUB_SUBTITLE_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION,
    workflow_id="dub-subtitle-generation",
    label="根据合成配音生成字幕",
    stages=[
        WorkflowStageDefinition(
            id="dub_subtitles",
            label="根据合成配音生成字幕",
            description=(
                "先按视频时间线生成完整配音音频，再依次听写、"
                "用实际合成台词校对、按声音检查时间并保存独立配音字幕。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="prepare_track",
                    label="准备整条配音音轨",
                    description=(
                        "按当前配音片段的真实时间线位置生成完整音频，"
                        "保留开头、中间和结尾的静音，不移动任何片段。"
                    ),
                    order=10,
                    execution="serial",
                    output_contract_version=(
                        DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="transcribe_track",
                    label="识别整条中文配音",
                    description=(
                        "只读取上一步生成的完整音频，调用一次中文 ASR，"
                        "输出实际听到的文字和计算块范围；块范围不是字幕时间。"
                    ),
                    order=20,
                    execution="serial",
                    depends_on=["prepare_track"],
                    output_contract_version=(
                        DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="proofread_text",
                    label="用实际合成台词校对文字",
                    description=(
                        "校对以实际合成台词（台词字幕）为主，只有确认读音一致时，才参考上屏字幕统一英文、数字和单位的显示写法；"
                        "不读取上屏字幕时间，也不补入音频里没有的台词。"
                    ),
                    order=30,
                    execution="serial",
                    depends_on=["transcribe_track"],
                    output_contract_version=(
                        DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="align_words",
                    label="取得逐字真实时间",
                    description=(
                        "按听写音频块运行严格字词级声学对齐，"
                        "把块内发音位置还原成完整音频绝对时间；失败时不插值。"
                    ),
                    order=40,
                    execution="serial",
                    depends_on=["proofread_text"],
                    output_contract_version=(
                        DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="segment_subtitles",
                    label="按语义组成字幕",
                    description=(
                        "只在已对齐字词之间选择语义断点，"
                        "字幕文字不改写，起止时间取首尾字词的真实时间。"
                    ),
                    order=50,
                    execution="serial",
                    depends_on=["align_words"],
                    output_contract_version=(
                        DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="commit",
                    label="保存准确的配音字幕",
                    description=(
                        "确认配音音频和校对文字仍是同一版本，"
                        "只替换独立配音字幕轨。"
                    ),
                    order=60,
                    execution="serial",
                    depends_on=["segment_subtitles"],
                    output_contract_version=(
                        DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION
                    ),
                ),
            ],
        )
    ],
)


LOCALIZATION_WORKFLOW_V3_DEFINITION = WorkflowDefinition(
    workflow_id="localization-v3",
    label="本土化字幕",
    stages=[
        WorkflowStageDefinition(
            id="localization_understanding",
            label="理解全文与人物",
            description=(
                "锁定同一份 ASR、逐词时间和交付目标，再从全文理解内容结构、"
                "人物口吻、情绪、硬事实和真正需要补证的问题。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="lock_localization_source",
                    label="固定本次英文源数据",
                    description=(
                        "保存本次使用的最终英文 ASR、逐词时间、停顿、说话人和术语；"
                        "不调用模型，也不翻译。"
                    ),
                    order=10,
                    output_contract_version="localization-source-lock-v1",
                ),
                WorkflowAtomicTaskDefinition(
                    id="lock_localization_context_intent",
                    label="固定本次本土化要求",
                    description=(
                        "读取版本化配置，固定目标观众、中文字幕、配音台词、"
                        "中文表达和时间对应要求；不调用模型。"
                    ),
                    order=20,
                    execution="serial",
                    depends_on=["lock_localization_source"],
                    output_contract_version="localization-context-intent-v2",
                ),
                WorkflowAtomicTaskDefinition(
                    id="analyze_localization_document",
                    label="建立全文本土化创作提纲",
                    description=(
                        "通读完整原文，形成后续中文创作共同使用的篇章、人物、情绪、"
                        "事实、文化表达和动态创作策略草案；需要补证的问题交给后续流程，"
                        "不写中文稿。"
                    ),
                    order=30,
                    execution="join",
                    depends_on=[
                        "lock_localization_source",
                        "lock_localization_context_intent",
                    ],
                    output_contract_version="localization-document-brief-v3",
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="localization_evidence",
            label="按需补充证据",
            description=(
                "资料查询和画面取证只回答全文理解中留下的疑点；没有疑点时直接跳过。"
            ),
            order=20,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="collect_localization_research_evidence_v3",
                    label="查询必要资料",
                    description=(
                        "执行流程按疑点数量生成的限定查询并保存来源；"
                        "查询次数由当前任务决定，不由设置页人为限制。"
                    ),
                    order=40,
                    execution="parallel",
                    depends_on=["analyze_localization_document"],
                    optional=True,
                    output_contract_version=(
                        "localization-document-research-evidence-v1"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="collect_localization_visual_evidence_v3",
                    label="查看必要画面",
                    description=(
                        "只截取疑点对应时间附近的原视频画面；不识别真实人物，"
                        "也不修改台词。"
                    ),
                    order=41,
                    execution="parallel",
                    depends_on=["analyze_localization_document"],
                    optional=True,
                    output_contract_version=(
                        "localization-document-visual-evidence-v1"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="adjudicate_localization_evidence_v3",
                    label="确认资料与画面结论",
                    description=(
                        "把资料和画面汇合成简短、可追溯的中文约束；证据不足时"
                        "保持保守，不循环搜索，也不编造事实。"
                    ),
                    order=50,
                    execution="join",
                    depends_on=[
                        "collect_localization_research_evidence_v3",
                        "collect_localization_visual_evidence_v3",
                    ],
                    output_contract_version=(
                        "localization-document-evidence-adjudication-v6"
                    ),
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="localization_creation",
            label="创作并验收本土化初稿",
            description=(
                "先锁定当前视频专属的创作策略和非语言表演结论，再按稳定连续"
                "范围生成并确定性汇合全文本土化初稿，随后分别检查原意和"
                "目标语言自然度。"
            ),
            order=30,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="lock_localization_creation_context",
                    label="锁定本土化创作策略",
                    description=(
                        "把本土化要求、全文提纲、术语选择、重点语义和证据结论汇合成"
                        "下一步唯一使用的创作上下文；不翻译，也不调用模型。"
                    ),
                    order=55,
                    execution="join",
                    depends_on=[
                        "analyze_localization_document",
                        "adjudicate_localization_evidence_v3",
                    ],
                    output_contract_version=(
                        "localization-creation-context-v6"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="generate_localization_spoken_script",
                    label="生成全文本土化初稿",
                    description=(
                        "复用全文理解和已锁定的动态创作上下文，按语义停顿生成稳定"
                        "连续分块；程序保证完整覆盖、原顺序和确定性合并，模型不负责"
                        "自由重排全文。"
                    ),
                    order=60,
                    depends_on=["lock_localization_creation_context"],
                    output_contract_version="localization-spoken-script-v4",
                ),
                WorkflowAtomicTaskDefinition(
                    id="review_localization_fidelity",
                    label="复核原意与事实",
                    description=(
                        "检查章节、案例、事实、关键关系、数字、否定、因果和人设；"
                        "允许自然重组，不按英文句子逐条打分。"
                    ),
                    order=70,
                    execution="parallel",
                    depends_on=["generate_localization_spoken_script"],
                    output_contract_version=(
                        "localization-spoken-script-review-v2"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="review_localization_naturalness",
                    label="盲测中文自然度",
                    description=(
                        "只看中文稿，判断它像母语者在当前场景下的自然表达，还是翻译稿或"
                        "书面文章；不接触英文原文。"
                    ),
                    order=71,
                    execution="parallel",
                    depends_on=["generate_localization_spoken_script"],
                    output_contract_version=(
                        "localization-spoken-script-review-v2"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="finalize_localization_spoken_script",
                    label="本土化台词终审",
                    description=(
                        "汇合两份质检；通过时原样锁定，有明确问题时只重生成对应的"
                        "原英文分块一次，再做一次原意与自然度复核；超过有限尝试仍"
                        "不理想时留下精确标记并继续，不靠多轮审核反复修补。"
                    ),
                    order=80,
                    execution="join",
                    depends_on=[
                        "review_localization_fidelity",
                        "review_localization_naturalness",
                    ],
                    output_contract_version=(
                        "localization-spoken-script-final-v1"
                    ),
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="localization_alignment",
            label="映射语义时间并生成双轨",
            description=(
                "中文先独立写好，再把中文语义段映射回英文逐词时间；"
                "台词轨用于配音，上屏字幕轨用于阅读。"
            ),
            order=40,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="align_localization_semantics",
                    label="本地映射语义时间",
                    description=(
                        "用本地跨语言向量模型和单调路径算法，把中文语义段对应到"
                        "英文时间；程序保证完整覆盖和原顺序，不调用 LLM。"
                    ),
                    order=90,
                    depends_on=["finalize_localization_spoken_script"],
                    output_contract_version=(
                        "localization-semantic-alignment-v14"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="adjudicate_localization_alignment",
                    label="复核时间歧义",
                    description=(
                        "只对低把握边界让模型从程序候选中选择；不能重写中文、"
                        "新增时间或改变完整覆盖。没有歧义时直接跳过。"
                    ),
                    order=100,
                    depends_on=["align_localization_semantics"],
                    optional=True,
                    output_contract_version=(
                        "localization-alignment-adjudication-v1"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="build_localization_dual_tracks",
                    label="生成台词轨与上屏字幕",
                    description=(
                        "保留完整中文台词作为配音文本，并在同一语义时间窗内"
                        "按中文标点和阅读长度生成上屏字幕；不再翻译。"
                    ),
                    order=110,
                    depends_on=["adjudicate_localization_alignment"],
                    output_contract_version="localization-dual-tracks-v7",
                ),
                WorkflowAtomicTaskDefinition(
                    id="adjudicate_localization_display_boundaries",
                    label="复核上屏字幕时间",
                    description=(
                        "程序只筛出有语义或时长歧义的相邻字幕边界，并给出少量"
                        "逐词候选；模型只能选择候选，不能改文字或生成时间。"
                    ),
                    order=120,
                    depends_on=["build_localization_dual_tracks"],
                    optional=True,
                    output_contract_version=(
                        "localization-display-adjudication-v1"
                    ),
                ),
            ],
        ),
        WorkflowStageDefinition(
            id="localization_delivery",
            label="质量门与正式保存",
            description=(
                "最后检查内容、来源、时间和输入版本；内容问题记录标记继续，"
                "通用数据完整性错误才阻断受影响分支。"
            ),
            order=50,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="validate_localization_tracks",
                    label="检查本土化结果",
                    description=(
                        "本地检查中文段落和英文时间是否完整、上屏字幕是否重叠、"
                        "关键事实是否仍受保护；内容问题输出 warning，来源或时间"
                        "损坏才阻断。不调用模型，不修改结果。"
                    ),
                    order=130,
                    depends_on=[
                        "adjudicate_localization_display_boundaries"
                    ],
                    output_contract_version=(
                        "localization-tracks-quality-gate-v1"
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="commit_localization_tracks",
                    label="保存正式本土化双轨",
                    description=(
                        "再次确认英文源输入没有变化，把中文台词、推荐口播文本和"
                        "上屏字幕写入项目；英文 ASR 轨保持不变。"
                    ),
                    order=140,
                    depends_on=["validate_localization_tracks"],
                    output_contract_version=(
                        "localization-formal-dual-track-write-v1"
                    ),
                ),
            ],
        ),
    ],
)


SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION = WorkflowDefinition(
    schema_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
    workflow_id="semantic-tts-grouping",
    label="按语义组合配音字幕",
    stages=[
        WorkflowStageDefinition(
            id="semantic_tts_grouping",
            label="整理并保存语义分组",
            description=(
                "固定字幕、说话人和模型配置，按连续语义生成分组，"
                "经本地完整性校验后写入项目。"
            ),
            order=10,
            atomic_tasks=[
                WorkflowAtomicTaskDefinition(
                    id="prepare",
                    label="整理字幕和说话人",
                    description="按时间顺序固定字幕文字、编号和说话人，不调用模型。",
                    order=10,
                    output_contract_version=(
                        SEMANTIC_TTS_GROUPING_INPUT_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="group",
                    label="判断语义和场景",
                    description=(
                        "通过持久化 Provider 步骤生成连续语义分组；"
                        "不确定的付费调用不会自动重放。"
                    ),
                    order=20,
                    depends_on=["prepare"],
                    output_contract_version=(
                        SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
                    ),
                ),
                WorkflowAtomicTaskDefinition(
                    id="validate",
                    label="检查分组完整性",
                    description=(
                        "本地检查原顺序、完整覆盖、连续性、说话人和字数上限。"
                    ),
                    order=30,
                    depends_on=["group"],
                ),
                WorkflowAtomicTaskDefinition(
                    id="write",
                    label="保存语义分组",
                    description=(
                        "再次确认字幕输入未变化，再把通过校验的分组写入项目。"
                    ),
                    order=40,
                    depends_on=["validate"],
                    output_contract_version="semantic-tts-grouping-result-v1",
                ),
            ],
        )
    ],
)


def asr_workflow_summary(
    *,
    include_diarization: bool = False,
) -> dict:
    """Return the canonical JSON shape stored in task summaries."""

    summary = ASR_WORKFLOW_DEFINITION.model_dump(mode="json")
    if not include_diarization:
        initial_tasks = summary["stages"][0]["atomic_tasks"]
        summary["stages"][0]["atomic_tasks"] = [
            task
            for task in initial_tasks
            if task["id"] not in {"diarization", "initial_analysis_join"}
        ]
    return summary


def asr_raw_development_workflow_summary() -> dict:
    return (
        ASR_RAW_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_initial_analysis_development_workflow_summary() -> dict:
    return (
        ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_document_understanding_development_workflow_summary() -> dict:
    return (
        ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_visual_evidence_development_workflow_summary() -> dict:
    return (
        ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_research_evidence_development_workflow_summary() -> dict:
    return (
        ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )

def asr_entity_normalization_development_workflow_summary() -> dict:
    return (
        ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_section_review_development_workflow_summary() -> dict:
    return (
        ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_review_decisions_development_workflow_summary() -> dict:
    return (
        ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )

def asr_whole_recheck_development_workflow_summary() -> dict:
    return (
        ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def asr_transcript_quality_gate_development_workflow_summary() -> dict:
    return (
        ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_DEFINITION
        .model_dump(mode="json")
    )


def source_audio_workflow_summary() -> dict:
    """Return the durable source-audio workflow contract."""

    return SOURCE_AUDIO_WORKFLOW_DEFINITION.model_dump(mode="json")


def stem_separation_workflow_summary() -> dict:
    return STEM_SEPARATION_WORKFLOW_DEFINITION.model_dump(
        mode="json"
    )


def reference_candidates_workflow_summary() -> dict:
    return REFERENCE_CANDIDATES_WORKFLOW_DEFINITION.model_dump(
        mode="json"
    )


def speaker_diarization_workflow_summary() -> dict:
    return SPEAKER_DIARIZATION_WORKFLOW_DEFINITION.model_dump(
        mode="json"
    )


def localization_workflow_summary() -> dict:
    """Return the production document-first localization workflow."""

    return LOCALIZATION_WORKFLOW_V3_DEFINITION.model_dump(mode="json")


def dub_subtitle_workflow_summary() -> dict:
    """Return the only synthesized-dub subtitle workflow contract."""

    return DUB_SUBTITLE_WORKFLOW_DEFINITION.model_dump(mode="json")


def semantic_tts_grouping_workflow_summary() -> dict:
    """Return the durable semantic grouping workflow contract."""

    return SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION.model_dump(
        mode="json"
    )


__all__ = [
    "ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_RAW_DEVELOPMENT_WORKFLOW_DEFINITION",
    "ASR_WORKFLOW_DEFINITION",
    "DUB_SUBTITLE_WORKFLOW_DEFINITION",
    "LOCALIZATION_WORKFLOW_V3_DEFINITION",
    "SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION",
    "REFERENCE_CANDIDATES_WORKFLOW_DEFINITION",
    "SPEAKER_DIARIZATION_WORKFLOW_DEFINITION",
    "SOURCE_AUDIO_WORKFLOW_DEFINITION",
    "STEM_SEPARATION_WORKFLOW_DEFINITION",
    "WorkflowAtomicTaskDefinition",
    "WorkflowDefinition",
    "WorkflowStageDefinition",
    "asr_workflow_summary",
    "asr_raw_development_workflow_summary",
    "asr_initial_analysis_development_workflow_summary",
    "asr_document_understanding_development_workflow_summary",
    "asr_visual_evidence_development_workflow_summary",
    "asr_research_evidence_development_workflow_summary",
    "asr_entity_normalization_development_workflow_summary",
    "asr_section_review_development_workflow_summary",
    "asr_review_decisions_development_workflow_summary",
    "asr_whole_recheck_development_workflow_summary",
    "asr_transcript_quality_gate_development_workflow_summary",
    "dub_subtitle_workflow_summary",
    "localization_workflow_summary",
    "semantic_tts_grouping_workflow_summary",
    "source_audio_workflow_summary",
    "stem_separation_workflow_summary",
    "reference_candidates_workflow_summary",
    "speaker_diarization_workflow_summary",
]
