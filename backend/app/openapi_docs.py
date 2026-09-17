from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse


SWAGGER_UI_VERSION = "5.30.3"
SWAGGER_JS_URL = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js"
SWAGGER_CSS_URL = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css"

OPENAPI_TITLE = "Voice Studio 接口中心"
OPENAPI_DESCRIPTION = """
面向 Voice Studio 前端、自动化工具和 Agent 的统一接口说明。

使用时请保留接口路径、参数名、JSON 字段、枚举值和 `operationId` 的原始写法。
耗时处理优先通过“视频本土化”中的后台任务接口提交，并根据 `operation_id`
查询进度、取消或重试。业务失败统一返回 `error.code`、`error.message` 和
`error.detail`，便于调用方判断是否需要修正参数或重试。
""".strip()


TAG_METADATA = {
    "engines": ("语音引擎", "查看、启停和诊断本地或云端语音引擎。"),
    "voices": ("音色库", "管理音色、参考音频和云端复刻音色。"),
    "generate": ("语音生成", "生成单段语音，或先获取生成计划。"),
    "longform": ("长文本生成", "提交、跟踪和导出长文本语音任务。"),
    "batches": ("批量生成", "创建并查看批量语音生成任务。"),
    "tasks": ("任务中心", "查看、取消、重试或删除通用后台任务。"),
    "history": ("生成历史", "查看生成记录、音频文件和波形数据。"),
    "projects": ("项目管理", "创建项目并管理角色、分段和转写内容。"),
    "video-localization": (
        "视频本土化",
        "导入视频，完成源音轨、分轨、ASR、翻译、配音、时间线和成品导出。",
    ),
    "exports": ("导出管理", "创建、查看和下载导出文件。"),
    "evaluations": ("TTS 校对", "校验合成结果与预期文本是否一致。"),
    "presets": ("生成预设", "保存和复用语音生成参数。"),
    "voice-seeds": ("音色种子", "查看和导入音色种子。"),
    "community-voice-packs": ("社区音色包", "浏览和导入社区共享的音色包。"),
    "text-tools": ("文本工具", "拆分、清洗文本并规范化数字读法。"),
    "audio-tools": (
        "音频工具",
        "合并音频，并通过独立后台任务分离人声和背景声。",
    ),
    "seed-audio-assets": ("种子音频素材", "管理音色种子的图片等配套素材。"),
    "asr": ("语音识别", "提交、管理和导出语音转写任务。"),
    "ser": ("情绪识别", "识别文本或音频表达的情绪。"),
    "settings": ("系统设置", "管理服务配置、模型档案、密钥、存储和联网能力。"),
    "health": ("服务状态", "检查 Voice Studio 后端服务是否可用。"),
}


SUMMARY_TRANSLATIONS = {
    "Repair Localized Source Bindings": "原子修正本土化来源绑定",
    "Refresh Current Dubbing Projection": "保留实际剪辑位置，刷新当前配音片段的断句证据",
    # 语音识别
    "Transcribe Audio": "转写音频",
    "Transcription History": "查看转写历史",
    "List Transcription Tasks": "查看转写任务",
    "Create Transcription Task": "创建转写任务",
    "Get Transcription Task": "获取转写任务详情",
    "Delete Transcription Task": "删除转写任务",
    "Cancel Transcription Task": "取消转写任务",
    "Retry Transcription Task": "重试转写任务",
    "Get Transcription": "获取转写结果",
    "Delete Transcription": "删除转写结果",
    "Batch Delete Transcriptions": "批量删除转写结果",
    "Supplement Transcription Timestamps": "补全转写时间戳",
    "Supplement Transcription Timestamps Batch": "批量补全转写时间戳",
    "Export Transcription": "导出转写结果",
    "Transcription Source Audio": "读取转写的源音频",
    "生成严格字幕时间证据": "生成严格字幕时间证据",
    # 通用音频、批次和音色包
    "Merge Audio": "合并音频",
    "创建人声分离任务": "创建人声分离任务",
    "获取人声分离任务": "获取人声分离任务",
    "下载人声分离音轨": "下载人声分离音轨",
    "取消人声分离任务": "取消人声分离任务",
    "删除人声分离任务": "删除人声分离任务",
    "List Batches": "查看批量任务",
    "Generate Batch": "创建批量生成任务",
    "Get Batch": "获取批量任务详情",
    "List Community Voice Packs": "查看社区音色包",
    "Import Community Voice Pack": "导入社区音色包",
    # 引擎
    "List Engines": "查看语音引擎",
    "List Engine Installations": "查看引擎安装情况",
    "Get Engine": "获取引擎详情",
    "List Speakers": "查看引擎说话人",
    "Get Doubao Speaker Catalog Status": "查看豆包说话人目录状态",
    "Sync Doubao Speaker Catalog": "同步豆包说话人目录",
    "Get Doubao Speaker Preview": "获取豆包说话人试听音频",
    "Start Engine": "启动语音引擎",
    "Stop Engine": "停止语音引擎",
    "Health Check": "检查引擎健康状态",
    "Diagnose Audio": "诊断音频",
    "Get Asr Selection": "读取 ASR 自动选择",
    "Get Diagnostic Audio": "获取诊断音频",
    # 评测、导出与生成
    "Verify Tts Output": "验证 TTS 输出质量",
    "Latest Evaluation": "获取最新评测结果",
    "Evaluation File": "获取评测文件",
    "Evaluation Audio": "获取评测音频",
    "List Exports": "查看导出记录",
    "Create Export": "创建导出任务",
    "Download Export": "下载导出文件",
    "Generate Plan": "获取语音生成计划",
    "Generate": "生成语音",
    "Refresh Dubbing Plan Timing": "只刷新当前配音计划的词级时间",
    "Reset Dubbing Timeline Groups": "清理指定配音分组的时间线摆放",
    # 历史
    "List History": "查看生成历史",
    "List History Page": "分页查看生成历史",
    "Delete History": "删除生成历史",
    "Get Audio": "获取历史音频",
    "Get Waveform": "获取音频波形",
    "Get Voice File Waveform": "获取受管音色文件波形",
    # 长文本
    "List Longform Tasks": "查看长文本任务",
    "Generate Longform": "创建长文本生成任务",
    "Get Longform Task": "获取长文本任务详情",
    "Dismiss Longform Task": "移除长文本任务记录",
    "Retry Failed Segments": "重试失败的长文本分段",
    "Cancel Longform Task": "取消长文本任务",
    "Cancel Longform Segment": "取消长文本分段",
    "Download Longform Export": "下载长文本导出文件",
    # 预设和项目
    "List Presets": "查看生成预设",
    "Create Preset": "创建生成预设",
    "Get Preset": "获取生成预设详情",
    "Update Preset": "更新生成预设",
    "Delete Preset": "删除生成预设",
    "List Projects": "查看项目",
    "List Project Summaries": "查询轻量项目目录",
    "Create Project": "创建项目",
    "Get Project": "获取项目详情",
    "Update Project": "更新项目",
    "Delete Project": "删除项目",
    "Add Role": "添加项目角色",
    "Put Segments": "保存项目分段",
    "Import Transcriptions": "导入转写结果",
    "Generate Project": "生成项目语音",
    "Sync Video Localization Project Summaries": "同步本地视频项目目录",
    # 种子素材和情绪识别
    "Upload Seed Audio Image": "上传种子音频图片",
    "Get Seed Audio Asset": "获取种子音频素材",
    "Delete Seed Audio Asset": "删除种子音频素材",
    "Predict Emotion": "识别文本情绪",
    "Predict File Emotion": "识别文件内容情绪",
    "Batch Predict Emotions": "批量识别情绪",
    # 设置
    "Get Settings": "获取系统设置",
    "Update Settings": "更新系统设置",
    "Update Mimo Secret": "更新 MiMo 密钥",
    "Update Doubao Secret": "更新豆包密钥",
    "Update Volcengine Directory Secret": "更新火山引擎目录密钥",
    "Test Cloud Connection": "测试云服务连接",
    "Get Llm Profiles": "查看大模型配置档案",
    "Save Llm Profile": "保存大模型配置档案",
    "Remove Llm Profile": "删除大模型配置档案",
    "Set Default Llm Profile": "设置默认大模型配置档案",
    "Get Llm Models": "获取大模型列表",
    "Test Llm Profile": "测试大模型配置档案",
    "查看本机 Codex CLI 状态": "查看本机 Codex CLI 状态",
    "登录本机 Codex CLI": "登录本机 Codex CLI",
    "更换本机 Codex CLI 账号": "更换本机 Codex CLI 账号",
    "退出本机 Codex CLI": "退出本机 Codex CLI",
    "Get Web Search Settings": "获取联网搜索设置",
    "Save Web Search Settings": "保存联网搜索设置",
    "Test Web Search Settings": "测试联网搜索设置",
    "Get Storage Audit": "查看存储占用审计",
    "Cleanup Storage": "清理存储空间",
    "Update Storage Retention": "保存自动清理策略",
    "Cleanup Storage Retention": "按保留策略清理过程产物",
    "Open Storage Location": "打开存储目录",
    # 通用任务与文本
    "List Tasks": "查看任务",
    "List Tasks Page": "分页查看任务",
    "Get Task Summary": "获取任务摘要",
    "Get Task": "获取任务详情",
    "Delete Task": "删除任务",
    "Cancel Task": "取消任务",
    "Retry Task": "重试任务",
    "Split Text": "拆分文本",
    "Clean Text": "清洗文本",
    "Normalize Numbers": "规范化数字读法",
    # 视频本土化：项目、媒体与后台任务
    "Sync Video Localization Projects": "同步视频本土化项目",
    "Get Video Localization": "获取视频本土化项目详情",
    "Get Video Localization Workspace": "获取视频本土化工作区与媒体状态",
    "Get Video Localization Workspace Detail": "按需获取视频本土化工作区详情",
    "Get Video Localization Workspace Revision": "获取视频本土化工作区版本",
    "Get Video Localization Timeline Projection": "获取视频本土化实时轻量时间线",
    "下载最近一次时间线音频包": "下载最近一次时间线音频包",
    "生成并下载时间线音频包": "生成并下载时间线音频包",
    "下载最近一次本土化视频": "下载最近一次本土化视频",
    "生成并下载本土化视频": "生成并下载本土化视频",
    "按所选内容生成单个视频、音频或字幕文件": "按所选内容生成单个视频、音频或字幕文件",
    "预览媒体导出的默认文件名": "预览媒体导出的默认文件名",
    "选择媒体导出保存目录": "选择媒体导出保存目录",
    "修复视频本土化项目存储": "修复视频本土化项目存储",
    "Put Video Localization": "保存视频本土化项目",
    "Put Video Localization Workspace": "保存视频本土化工作区",
    "Reset Video Localization": "重置视频本土化项目",
    "Auto Name Video Localization Project": "自动生成视频本土化项目名称",
    "Patch Video Localization Ui State": "更新视频本土化界面状态",
    "Patch Video Localization Timeline Edit": "轻量保存时间线拖动与裁切",
    "Open Video Localization Project Directory": "打开视频本土化项目目录",
    "Import Video Localization Source Media": "导入待本土化的源视频",
    "Get Video Localization Source Video": "获取源视频文件",
    "Get Video Localization Source Preview Video": "获取源视频预览文件",
    "Prepare Video Localization Source Preview Video": "生成源视频预览文件",
    "Get Video Localization Source Preview Video Segment": "获取分段视频预览文件",
    "Get Video Localization Preview Cache": "获取视频预览缓存状态",
    "Build Video Localization Preview Cache": "生成视频预览缓存",
    "Refresh Video Localization Preview Cache": "刷新视频预览缓存",
    "Get Video Localization Preview Cache Sprite": "获取视频预览缩略图拼图",
    "Get Video Localization Source Audio": "获取源音轨文件",
    "Prepare Video Localization Source Preview Audio": "生成源音轨试听文件",
    "List Video Localization Operations": "查看视频本土化后台任务",
    "Submit Video Localization Operation": "提交视频本土化后台任务",
    "List Video Localization Operation Summaries": "获取各类后台任务的最新摘要",
    "Read Video Localization Operation Feed": "增量同步视频本土化后台任务",
    "Read Bounded Video Localization Operation Feed": "分页同步视频本土化后台任务",
    "Get Video Localization Operation": "获取后台任务详情",
    "Get Video Localization Development Asr Result": "获取开发单步的完整原始听写结果",
    "Get Video Localization Development Diarization Result": "获取开发单步的完整说话人区分结果",
    "Get Video Localization Development Initial Analysis Result": "获取并行初始分析的联合开发快照",
    "Cancel Video Localization Operation": "取消后台任务",
    "Retry Video Localization Operation": "重试后台任务",
    "Get Video Localization Stem Audio": "获取分轨音频",
    "Prepare Video Localization Stem Preview Audio": "生成分轨试听音频",
    # 视频本土化：识别、字幕、说话人和翻译
    "Update Video Localization Cue": "更新原文字幕片段",
    "Delete Video Localization Source Cue": "删除原文字幕片段",
    "Merge Video Localization Source Cues": "合并原文字幕片段",
    "Split Video Localization Source Cue": "拆分原文字幕片段",
    "Confirm Video Localization Cue Timing": "确认原文字幕时间",
    "Apply Video Localization Asr Vad Timing Correction": "保存分离人声 ASR/VAD 时间校正",
    "Apply Video Localization Asr Source Repair": "保存有证据的局部 ASR 来源修正",
    "Update Video Localization Localized Subtitle": "更新本土化字幕片段",
    "Edit Video Localization Localized Subtitle": "交互式编辑本土化字幕片段",
    "Update Video Localization Localized Spoken Segment": "更新本土化配音台词段",
    "Delete Video Localization Localized Subtitle": "删除本土化字幕片段",
    "Split Video Localization Localized Subtitle": "拆分本土化字幕片段",
    "Create Video Localization Speaker": "创建说话人",
    "Update Video Localization Speaker": "更新说话人",
    "Generate Video Localization Chinese Draft": "生成中文本土化初稿",
    # 视频本土化：配音
    "Read physical dubbing completion without generating or editing media": "只读核对实际配音覆盖与收尾缺失",
    "Submit Video Localization Tts Batch": "提交本土化配音批次",
    "Create Video Localization Dubbing Plan": "创建本土化配音生成计划",
    "Get Video Localization Dubbing Snapshot": "获取本土化配音规划快照",
    "按已确认分句或同说话人参考继续恢复配音": "按已确认分句或同说话人参考继续恢复配音",
    "生成前核对可用时间和同音色时长预估": "生成前核对可用时间和同音色时长预估",
    "读取可恢复的逐组配音生产进度": "读取可恢复的逐组配音生产进度",
    "读取候选最终投影的逐边界断句证据": "读取候选最终投影的逐边界断句证据",
    "提交 Agent 对候选逐边界断句的完整处置": "提交 Agent 对候选逐边界断句的完整处置",
    "取得当前配音候选的独立内容转写证据": "取得当前配音候选的独立内容转写证据",
    "在正式采纳前安全切分暂存配音候选": "在正式采纳前安全切分暂存配音候选",
    "将未变化的既有正式配音核对到当前计划": "将未变化的既有正式配音核对到当前计划",
    "按统一语义组流程继续配音": "按统一语义组流程继续配音",
    "将完整但超出时间窗的声音停放到第二配音轨": "将完整但超出时间窗的声音停放到第二配音轨",
    "刷新当前候选的自动 CQC 与逐词对齐证据": "刷新当前候选的自动 CQC 与逐词对齐证据",
    "保留当前可听候选、记录人工复核状态并继续后续配音": "保留当前可听候选、记录人工复核状态并继续后续配音",
    "记录单个配音组真实失败并继续不受影响的后续组": "记录单个配音组真实失败并继续不受影响的后续组",
    "Evaluate Video Localization Dubbing Candidate": "质检本土化配音候选",
    "Get Video Localization Dubbing Candidate Cqc Input": "获取本土化配音候选的冻结质检证据",
    "Install Managed Model": "安装受管模型",
    "Uninstall Managed Model": "卸载受管模型",
    "Audit Video Localization Dubbing Timeline": "审计本土化配音时间线",
    "Audit Current Video Localization Dubbing Timeline": "审计当前本土化配音时间线",
    "按语义锚点和真实停顿整理中文配音时间线": "按语义锚点和真实停顿整理中文配音时间线",
    "按当前逐组终态选择清理旧配音并归入主轨": "按当前逐组终态选择清理旧配音并归入主轨",
    "只裁掉相邻配音片段已证明的首尾静音重叠": "只裁掉相邻配音片段已证明的首尾静音重叠",
    "安全收口最终中文配音轨并在通过后重建字幕": "安全收口最终中文配音轨并在通过后重建字幕",
    "按声学边界切分并整理中文配音时间线": "按声学边界切分并整理中文配音时间线",
    "记录有界修正后仍存在的非破坏性时间线问题并继续": "记录有界修正后仍存在的非破坏性时间线问题并继续",
    "提交当前候选的逐气口时间线验收记录": "提交当前候选的逐气口时间线验收记录",
    "Prepare Video Localization Tts Parameter Pack": "生成本土化配音参数包",
    "Prepare Video Localization Tts Handoff": "生成单段配音交接数据",
    "Reserve Video Localization Tts Handoff": "登记单段配音交接任务",
    "Preview Video Localization Tts Handoff": "预览单段配音交接数据",
    "List Video Localization Tts Tasks": "查看本土化配音任务",
    "Get Video Localization Tts Task Feed": "增量读取本土化配音任务",
    "Get Video Localization Tts Task": "获取本土化配音任务详情",
    "Delete Video Localization Tts Task": "删除本土化配音任务",
    "Cancel Video Localization Tts Task": "取消本土化配音任务",
    "Cleanup Unused Video Localization Tts History": "清理未使用的配音历史结果",
    "Delete Video Localization Tts History": "按范围删除视频本土化配音历史",
    "Sync Video Localization Tts Batch": "同步本土化配音批次状态",
    "Get Video Localization Cue Tts Audio": "获取字幕片段配音",
    "Get Video Localization Candidate Audio": "获取候选配音",
    "Apply Video Localization Candidate": "采用候选配音",
    # 视频本土化：时间线和导出
    "Get Video Localization Timeline Clip Audio": "获取时间线片段音频",
    "Apply Video Localization History To Timeline Clip": "将历史配音应用到时间线片段",
    "Apply Video Localization History To Timeline": "将历史配音应用到时间线",
    "Commit Video Localization Local Phrase Repair": "原子提交本土化局部短语修补",
    "Get Video Localization Timeline Clip Waveform": "获取时间线片段波形",
    "Get Video Localization Cue Source Audio": "获取字幕片段原声音频",
    "Get Video Localization Reference Clip Audio": "获取参考片段音频",
    "Get Video Localization Reference Clip Cover": "获取参考片段封面",
    "Export Video Localization Subtitles": "导出视频本土化字幕",
    "Clear Video Localization Subtitles": "清空视频本土化字幕",
    "Import Video Localization Subtitles": "导入视频本土化字幕",
    "Export Video Localization": "导出视频本土化项目包",
    "Export Video Localization Timeline": "导出视频本土化时间线",
    "Export Video Localization Timeline Audio Package": "导出时间线音频包",
    "Export Video Localization Timeline Video": "导出时间线视频",
    "Export Video Localization Readiness": "检查视频本土化导出条件",
    # 音色种子与音色库
    "List Voice Seeds": "查看音色种子",
    "Import Voice Seed": "导入音色种子",
    "List Voices": "查看音色",
    "Create Voice": "创建音色",
    "Upload Reference Audio": "上传参考音频",
    "List Doubao Cloud Voices": "查看豆包云端音色",
    "Refresh Doubao Cloud Voices": "刷新豆包云端音色",
    "Register Voice": "登记音色",
    "Get Voice File Audio": "获取音色文件音频",
    "Clip Voice File": "裁剪音色文件",
    "Clip And Transcribe Voice File": "裁剪并转写音色文件",
    "Get Voice": "获取音色详情",
    "Update Voice": "更新音色",
    "Delete Voice": "删除音色",
    "Train Doubao Voice Clone": "训练豆包复刻音色",
    "Refresh Doubao Voice Status": "刷新豆包音色状态",
    "Unbind Doubao Voice": "解除豆包音色绑定",
    "Get Reference Audio": "获取参考音频",
    "启动听写工作流": "启动听写工作流",
    "查看听写工作流结构": "查看听写工作流结构",
    "启动本土化工作流": "启动本土化工作流",
    "查看本土化工作流结构": "查看本土化工作流结构",
    "启动合成配音字幕工作流": "启动合成配音字幕工作流",
    "预检调用方提供的配音时间线": "预检调用方提供的配音时间线",
    "查看合成配音字幕工作流结构": "查看合成配音字幕工作流结构",
    "提交合成配音字幕复审结果": "提交合成配音字幕复审结果",
    "查看可选的本土化要求": "查看可选的本土化要求",
    "查看本土化源输入开发结果": "查看本土化源输入开发结果",
    "查看本土化上下文与交付目标开发结果": (
        "查看本土化上下文与交付目标开发结果"
    ),
    "查看连续语义单元开发结果": "查看连续语义单元开发结果",
    "查看全文理解开发结果": "查看全文理解开发结果",
    "查看画面取证开发结果": "查看画面取证开发结果",
    "查看画面取证截图": "查看画面取证截图",
    "查看正式任务的画面取证截图": "查看正式任务的画面取证截图",
    "查看资料查询开发结果": "查看资料查询开发结果",
    "查看名称与术语统一开发结果": "查看名称与术语统一开发结果",
    "查看第 1 轮分段复查开发结果": "查看第 1 轮分段复查开发结果",
    "查看第 1 轮修改汇总开发结果": "查看第 1 轮修改汇总开发结果",
    "查看第 1 轮全文复核开发结果": "查看第 1 轮全文复核开发结果",
    "查看进入校时前检查开发结果": "查看进入校时前检查开发结果",
    "查看语义单元 LLM 复核开发结果": "查看语义单元 LLM 复核开发结果",
    "查看本土化风险分析开发结果": "查看本土化风险分析开发结果",
    "查看字幕与口播预算开发结果": "查看字幕与口播预算开发结果",
    "查看本土化策略包开发结果": "查看本土化策略包开发结果",
    "查看本土化资料证据收集结果": "查看本土化资料证据收集结果",
    "查看本土化画面证据收集结果": "查看本土化画面证据收集结果",
    "查看本土化证据裁决结果": "查看本土化证据裁决结果",
    "查看本土化限定补查结果": "查看本土化限定补查结果",
    "查看本土化相邻画面结果": "查看本土化相邻画面结果",
    "查看本土化最终证据裁决结果": "查看本土化最终证据裁决结果",
    "查看本土化中文初稿结果": "查看本土化中文初稿结果",
    "查看本土化中文首轮校对结果": "查看本土化中文首轮校对结果",
    "查看本土化中文原意与事实复核结果": (
        "查看本土化中文原意与事实复核结果"
    ),
    "查看中文表达校对与全文通读结果": (
        "查看中文表达校对与全文通读结果"
    ),
    "查看字幕断句与语义时间映射结果": (
        "查看字幕断句与语义时间映射结果"
    ),
    "查看本土化字幕本地质量门结果": (
        "查看本土化字幕本地质量门结果"
    ),
    "查看正式中文字幕轨写入结果": (
        "查看正式中文字幕轨写入结果"
    ),
    # 服务状态
    "Health": "检查服务健康状态",
}


CORE_OPERATION_DETAILS = {
    (
        "/api/projects/{project_id}/video-localization/operations/localization",
        "post",
    ): {
        "summary": "启动本土化工作流",
        "description": """
锁定最终 ASR 与交付要求，完成全文本土化、独立复核、语义时间映射和双轨写入。
可识别台词继续翻译；证据支持的非语言表演保留原声。内容质检超过有限重试后记录
配音分组复核状态并继续，只有正文、来源、顺序、时间或媒体完整性错误阻断受影响分支。
full 模式从头执行；development_target 模式复用有效开发快照并在指定节点停止。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/workflows/localization",
        "get",
    ): {
        "summary": "查看本土化工作流结构",
        "description": "返回当前本土化工作流的阶段、原子子任务、执行方式和输出契约。",
    },
    (
        "/api/projects/{project_id}/video-localization/operations/english-asr",
        "post",
    ): {
        "summary": "启动听写工作流",
        "description": """
供 WebUI、自动化工具和 Agent 共用的 typed ASR 入口。正式模式会执行完整
听写流程；开发模式可停在 `asr`、`initial_analysis`，也可以读取指定上游
结果后单独运行 `understand_document`、`visual_evidence`、`research`、
`normalize_entities`、`section_review_r1`、`review_decisions_r1` 或
`whole_recheck_r1`。
初始语音分析会并行
生成原始听写和匿名说话人结果，再通过独立汇合任务校验音轨与音频指纹。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-result",
        "get",
    ): {
        "summary": "查看画面取证开发结果",
        "description": """
返回开发单步保存的完整画面取证结果，包括定点截图、逐字可见文字、可供资料查询
使用的关键词、可信度和仍无法确认的限制。

这个步骤只读取同项目中明确指定的 `understand_document` 结果和项目源视频，
不接受调用方传入本地视频路径。它不会根据长相识别人，不会决定规范名称，也不会
修改听写、字幕或时间线。第一轮只看一张关键帧；证据不足时最多再补一轮相邻画面，
不会无限截图。模型不支持图片时会保留可追溯状态并安全跳过。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-frames/{frame_id}",
        "get",
    ): {
        "summary": "查看画面取证截图",
        "description": """
只返回当前项目、当前开发任务清单中登记的截图。后台会校验任务归属、文件范围和
图片指纹；不能通过这个接口读取任意本地文件。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/visual-evidence-frames/{frame_id}",
        "get",
    ): {
        "summary": "查看正式任务的画面取证截图",
        "description": """
只返回当前项目、当前正式 ASR 任务专属清单中登记的截图。后台会校验任务归属、
任务隔离目录、文件范围和图片指纹；不同任务的截图互不覆盖，也不能通过这个接口
读取任意本地文件。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/workflows/asr",
        "get",
    ): {
        "summary": "查看听写工作流结构",
        "description": """
返回父级阶段、原子子任务、执行方式、依赖关系和输出契约版本。页面、自动化工具
和 Agent 可用它理解当前工作流，不需要自行维护另一份步骤名称和顺序。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-document-understanding-result",
        "get",
    ): {
        "summary": "查看全文理解开发结果",
        "description": """
返回开发单步保存的完整全文理解结果，包括全文概述、内容展开、说话方式、
待查名称、建议复查范围、实际模型和质量检查。

这个步骤只读取同项目中明确指定的 `initial_analysis` 联合快照，不接受本地
文件路径；不会联网查询、不会核实名称、不会修改听写，也不会继续执行后续流程。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-research-evidence-result",
        "get",
    ): {
        "summary": "查看资料查询开发结果",
        "description": """
返回开发单步保存的完整资料查询结果，包括每轮查询、有效来源、语言模型对证据
充分性的判断、是否继续搜索、停止原因和仍未解决的疑点。

这个步骤只读取同项目中明确指定的 `understand_document` 结果。搜索范围不会
越过上游提出的候选；可选的 `visual_evidence` 结果只能给已有候选补充查询关键词，
不能直接成为网页证据。最多轮次和最多查询数都有硬上限。结果只作为下一步名称归一
或人工核对的证据，不会直接决定规范名称，也不会修改听写或字幕。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-entity-normalization-result",
        "get",
    ): {
        "summary": "查看名称与术语统一开发结果",
        "description": """
返回有证据支持的规范名称、全文对应修改、保留原文的待确认项和模型用量。
该开发结果不会写入正式项目字幕；页面只把它作为当前阶段字幕快照显示。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-section-review-result",
        "get",
    ): {
        "summary": "查看第 1 轮分段复查开发结果",
        "description": """
返回各连续区块发现的可能听写问题、可信度、证据引用、失败区块和模型用量。
该步骤显式组合名称统一结果和全文理解的复查计划，只提问题、不接受修改，
也不改变当前字幕。真正应用修改由后续“汇总第 1 轮修改”任务负责。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-review-decisions-result",
        "get",
    ): {
        "summary": "查看第 1 轮修改汇总开发结果",
        "description": """
返回上一任务每条疑点的采纳、拒绝或待确认结论，以及应用后的完整字幕快照。
本步骤不能增加新的修改候选；数字、否定关系、规范名称证据和前面已锁定的修改
由本地安全规则再次校验。片段 ID 和时间码保持不变。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-whole-recheck-result",
        "get",
    ): {
        "summary": "查看第 1 轮全文复核开发结果",
        "description": """
返回本轮修改放回完整上下文后的复核结论：可以结束、进入下一轮，或保留建议复听项。
如果进入下一轮，会返回连续且完整覆盖全文的检查分段和每段重点；如果只剩无法自动
确认的小问题，会列出原因而不硬凑下一轮。本步骤只读，不修改字幕文字、片段 ID
或时间码。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-transcript-quality-gate-result",
        "get",
    ): {
        "summary": "查看进入校时前检查开发结果",
        "description": """
返回文字是否已经稳定到可以开始逐词校时、具体阻断原因和建议复听片段。
本步骤只做本地规则判断，不调用语言模型，也不修改字幕文字、片段 ID 或时间码。
低把握文字采用当前最高概率结果继续，文字疑点不阻断正式流程；
只有技术或结构阻断项会让正式流程停止。
开发单步按断点要求不会执行逐词时间对齐。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations",
        "post",
    ): {
        "summary": "提交视频本土化后台任务",
        "description": """
提交独立媒体子任务，并返回后台任务记录。支持的 `kind`：

- `source_audio`：从已导入的视频抽取源音轨；该任务不接受额外 `parameters`。
- `stems`：从源音轨分离 `vocals`（人声）和 `background`（背景声）；
  该任务不接受额外 `parameters`，成功详情不返回服务器文件路径。
- `reference_clips`：根据已有 cue 自动生成参考音候选；该后台任务不接受额外
  `parameters`。
- `speaker_diarization`：开发单步，只根据音频生成匿名说话人时间段；
  需要先开启后台的开发子流程控制，不会覆盖正式字幕或说话人数据。

完整 ASR 和本土化工作流分别使用 `/operations/english-asr` 和
`/operations/localization`，不接受通用 `kind` 请求。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations",
        "get",
    ): {
        "summary": "查看视频本土化后台任务",
        "description": "按项目查看后台任务。可使用查询参数筛选任务类型或状态，并用于恢复页面进度。",
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}",
        "get",
    ): {
        "summary": "获取后台任务详情",
        "description": """
根据 `operation_id` 查询任务的当前状态、进度、阶段、错误信息和
`result_summary`。调用方应轮询本接口，直到状态进入 `success`、
`failed` 或 `cancelled`。

`semantic-tts-grouping-workflow-v2` 的详情由 ledger、typed detail core、
workflow registry、step store 和已提交的受管 artifact 在同一个只读快照中组装，
不读取 Project JSON。任一新权威缺失或损坏时返回
`VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED`（HTTP 409），不会静默退回
兼容镜像；旧 workflow 仍通过明确的 legacy adapter 读取。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-asr-result",
        "get",
    ): {
        "summary": "获取开发单步的完整原始听写结果",
        "description": """
仅用于读取通过 `execution_mode=stop_after`、`stop_after_step=asr` 生成的
开发快照。接口返回 `asr-raw-v2` 的完整输入输出契约，包括自动名称提示、原始听写文本、
粗时间片段、完整性自检和识别阶段统计；不会继续执行说话人区分、文本复核、时间对齐
或字幕轨写入。

快照路径由后端校验，调用方只需提供当前项目的 `project_id` 和任务的
`operation_id`，不能指定或读取任意本地文件。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-diarization-result",
        "get",
    ): {
        "summary": "获取开发单步的完整说话人区分结果",
        "description": """
读取 `speaker_diarization` 开发单步生成的 `speaker-diarization-v1` 快照。
结果包含匿名说话人时间段、声纹分组、重叠讲话提醒、声纹复核状态和人数范围
质量检查。该步骤只依赖音频，不需要 ASR 文字或时间戳，也不会猜测真实姓名。

`min_speakers`、`max_speakers` 在当前 MOSS 引擎中仅用于结果复核，不会作为
模型硬约束。快照路径由后端校验，调用方不能借此读取任意本地文件。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-initial-analysis-result",
        "get",
    ): {
        "summary": "获取并行初始分析的联合开发快照",
        "description": """
读取 `execution_mode=stop_after`、`stop_after_step=initial_analysis` 生成的
`asr-initial-analysis-snapshot-v1` 联合快照。原始听写与说话人区分使用正式
ASR 相同的并行入口和同一份音频指纹，完成后在汇合边界一次性保存。

结果包含完整原始听写、匿名说话人分段、两项质量报告、汇合后的转写段和警告。
不会继续执行全文复核、逐词对齐、声音停顿分析、字幕断句或正式字幕写入。
该快照只用于当前开发调试，正式任务不会读取。
""".strip(),
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/cancel",
        "post",
    ): {
        "summary": "取消后台任务",
        "description": "请求取消仍在排队或运行中的任务。已经结束的任务不会重新执行。",
    },
    (
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/retry",
        "post",
    ): {
        "summary": "重试后台任务",
        "description": "沿用原任务类型和参数创建一次重试；请使用返回的新任务记录继续查询进度。",
    },
    (
        "/api/projects/{project_id}/video-localization/stems/{kind}/audio",
        "get",
    ): {
        "summary": "获取分轨音频",
        "description": """
下载已完成的分轨音频。`kind` 仅支持 `vocals`（人声）或
`background`（背景声）；调用前应确认分轨任务已经成功完成。
""".strip(),
    },
}


UI_LOCALIZATION_SCRIPT = r"""
<script>
(() => {
  const translations = new Map([
    ["Authorize", "配置认证"],
    ["Available authorizations", "可用认证方式"],
    ["Expand operation", "展开接口"],
    ["Collapse operation", "收起接口"],
    ["Filter by tag", "按功能筛选"],
    ["Try it out", "调试接口"],
    ["Execute", "发送请求"],
    ["Cancel", "取消"],
    ["Clear", "清空"],
    ["Parameters", "请求参数"],
    ["No parameters", "无需参数"],
    ["Request body", "请求体"],
    ["Responses", "返回结果"],
    ["Response body", "返回内容"],
    ["Response headers", "返回头"],
    ["Response content type", "返回内容类型"],
    ["Server response", "服务端返回"],
    ["Request URL", "请求地址"],
    ["Request duration", "请求耗时"],
    ["Schemas", "数据结构"],
    ["Models", "数据结构"],
    ["Model", "结构"],
    ["Example Value", "示例值"],
    ["Description", "说明"],
    ["Name", "名称"],
    ["Required", "必填"],
    ["Available values", "可选值"],
    ["Default value", "默认值"],
    ["Download", "下载"],
    ["Copy to clipboard", "复制到剪贴板"],
    ["Copied", "已复制"],
    ["Close", "关闭"],
    ["Loading", "正在加载"],
    ["Failed to load API definition.", "接口定义加载失败。"],
    ["Fetch error", "请求失败"]
  ]);
  const skipSelector = [
    "code", "pre", "samp", "kbd", "textarea", "select",
    ".opblock-summary-path", ".opblock-summary-operation-id",
    ".parameter__name", ".prop-name", ".prop-type", ".model-title",
    ".curl-command", ".microlight", ".highlight-code"
  ].join(",");

  const localize = (root) => {
    if (!(root instanceof Element) || root.matches(skipSelector)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || parent.closest(skipSelector)) continue;
      const value = node.nodeValue.trim();
      const localized = translations.get(value);
      if (localized) node.nodeValue = node.nodeValue.replace(value, localized);
    }
    const attributed = [
      ...(root.matches("[aria-label], [title], [placeholder]") ? [root] : []),
      ...root.querySelectorAll("[aria-label], [title], [placeholder]")
    ];
    for (const element of attributed) {
      if (element.closest(skipSelector)) continue;
      for (const attribute of ["aria-label", "title", "placeholder"]) {
        const value = element.getAttribute(attribute);
        const localized = value && translations.get(value.trim());
        if (localized) element.setAttribute(attribute, localized);
      }
    }
  };

  const root = document.getElementById("swagger-ui");
  if (!root) return;
  localize(root);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData" && record.target.parentElement) {
        localize(record.target.parentElement);
      }
      if (record.type === "attributes" && record.target instanceof Element) {
        localize(record.target);
      }
      for (const node of record.addedNodes) {
        if (node instanceof Element) localize(node);
      }
    }
  }).observe(root, {
    attributes: true,
    attributeFilter: ["aria-label", "title", "placeholder"],
    characterData: true,
    childList: true,
    subtree: true
  });
})();
</script>
"""


def _tag_definitions() -> list[dict[str, str]]:
    return [
        {"name": localized_name, "description": description} for localized_name, description in TAG_METADATA.values()
    ]


def _localize_schema(schema: dict[str, Any]) -> None:
    tag_name_map = {original_name: localized_name for original_name, (localized_name, _) in TAG_METADATA.items()}
    response_description_map = {
        "Successful Response": "请求成功",
        "Validation Error": "请求参数校验失败",
    }
    untranslated: list[str] = []

    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            original_summary = operation.get("summary", "")
            localized_summary = SUMMARY_TRANSLATIONS.get(original_summary)
            if not localized_summary:
                untranslated.append(original_summary or f"{method.upper()} {path}")
                localized_summary = f"调用接口：{original_summary or path}"
            operation["summary"] = localized_summary
            original_tags = operation.get("tags", [])
            operation["tags"] = (
                [tag_name_map.get(tag, tag) for tag in original_tags]
                if original_tags
                else [TAG_METADATA["health"][0]]
            )

            detail = CORE_OPERATION_DETAILS.get((path, method.lower()))
            if detail:
                operation.update(detail)

            for response in operation.get("responses", {}).values():
                if not isinstance(response, dict):
                    continue
                description = response.get("description")
                if description in response_description_map:
                    response["description"] = response_description_map[description]

    if untranslated:
        raise RuntimeError("OpenAPI operation summary 缺少中文本土化映射: " + ", ".join(sorted(set(untranslated))))

    schema["tags"] = _tag_definitions()


def build_localized_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=OPENAPI_TITLE,
        version=app.version,
        description=OPENAPI_DESCRIPTION,
        routes=app.routes,
        tags=_tag_definitions(),
    )
    _localize_schema(schema)
    app.openapi_schema = schema
    return schema


def _swagger_html(app: FastAPI) -> HTMLResponse:
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="Voice Studio 接口文档",
        swagger_js_url=SWAGGER_JS_URL,
        swagger_css_url=SWAGGER_CSS_URL,
        swagger_favicon_url="data:image/svg+xml,"
        + html.escape(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="14" fill="%232c5f78"/>'
            '<path d="M18 22h28v6H18zm0 14h20v6H18z" fill="white"/></svg>',
            quote=True,
        ),
        swagger_ui_parameters={
            "deepLinking": True,
            "displayOperationId": True,
            "displayRequestDuration": True,
            "docExpansion": "none",
            "filter": True,
            "persistAuthorization": True,
            "showExtensions": True,
            "showCommonExtensions": True,
            "tryItOutEnabled": False,
        },
    )
    body = response.body.decode("utf-8")
    body = body.replace("<html>", '<html lang="zh-CN">', 1)
    body = body.replace("</body>", UI_LOCALIZATION_SCRIPT + "\n</body>", 1)
    return HTMLResponse(body)


def install_openapi_docs(app: FastAPI) -> None:
    app.openapi = lambda: build_localized_openapi(app)

    @app.get("/docs", include_in_schema=False)
    async def localized_swagger_ui() -> HTMLResponse:
        return _swagger_html(app)
