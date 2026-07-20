export type EngineId = "indextts2" | "voxcpm" | "gpt_sovits";
export type FieldType = "range" | "number" | "text" | "select" | "toggle" | "file" | "textarea";
export type Field = { key: string; label: string; type: FieldType; default: string | number | boolean; help: string; min?: number; max?: number; step?: number; options?: string[]; unit?: string; fileKind?: "audio" | "gpt-weight" | "sovits-weight" | "directory" | "yaml" | "lora" };
export type Group = { title: string; summary: string; fields: Field[] };
export type GptSovitsVersion = "auto" | "v1" | "v2" | "v3" | "v4" | "v2Pro" | "v2ProPlus";

const gptSovitsVersions: GptSovitsVersion[] = ["auto", "v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus"];

export function resolveGptSovitsVersion(parameters: Record<string, unknown>): GptSovitsVersion {
  const selected = String(parameters.version ?? "auto") as GptSovitsVersion;
  if (gptSovitsVersions.includes(selected) && selected !== "auto") return selected;
  const paths = `${parameters.gpt_weights_path ?? parameters.t2s_weights_path ?? ""} ${parameters.sovits_weights_path ?? parameters.vits_weights_path ?? ""}`.toLowerCase();
  if (/(?:^|[\\/_-])v2proplus(?:[\\/_-]|$)/i.test(paths)) return "v2ProPlus";
  if (/(?:^|[\\/_-])v2pro(?:[\\/_-]|$)/i.test(paths)) return "v2Pro";
  if (/(?:^|[\\/_-])v4(?:[\\/_-]|$)/i.test(paths)) return "v4";
  if (/(?:^|[\\/_-])v3(?:[\\/_-]|$)/i.test(paths)) return "v3";
  if (/(?:^|[\\/_-])v2(?:[\\/_-]|$)/i.test(paths)) return "v2";
  if (/(?:^|[\\/_-])v1(?:[\\/_-]|$)/i.test(paths)) return "v1";
  return "auto";
}

export function gptSovitsDefaultsFor(parameters: Record<string, unknown>) {
  const version = resolveGptSovitsVersion(parameters);
  return { version, sample_steps: version === "v3" ? 32 : 8, super_sampling: false };
}

export const engines: Record<EngineId, { name: string; description: string; accent: string }> = {
  indextts2: { name: "IndexTTS 2", description: "参考音频克隆 · 情感解耦", accent: "#2563eb" },
  voxcpm: { name: "VoxCPM 2", description: "参考音频克隆 · 音色设计", accent: "#7c3aed" },
  gpt_sovits: { name: "GPT-SoVITS", description: "双权重模型 · 参考音频", accent: "#059669" },
};

const longAudio: Group = { title: "长音频与输出", summary: "无软件时长上限、断点续作、重试与流式合并", fields: [
  { key: "split_mode", label: "智能分段方式", type: "select", default: "按标点与长度", options: ["按标点与长度", "仅按标点", "按段落", "不分段"], help: "决定长文本如何拆成稳定的小段，通常推荐按标点与长度。" },
  { key: "segment_chars", label: "每段最大字数", type: "number", default: 180, min: 40, max: 1000, step: 10, unit: "字", help: "越小越稳定；越大更连贯，但显存与失败风险更高。" },
  { key: "segment_pause", label: "段间停顿", type: "number", default: 280, min: 0, max: 3000, step: 20, unit: "ms", help: "合并时插入的静音，旁白通常使用 200–400ms。" },
  { key: "retry_count", label: "失败自动重试", type: "number", default: 2, min: 0, max: 10, step: 1, unit: "次", help: "只重试失败分段，已经完成的段不会重做。" },
  { key: "resume", label: "断点续作", type: "toggle", default: true, help: "应用重启后从最后完成的分段继续。" },
  { key: "keep_segments", label: "保留分段音频", type: "toggle", default: true, help: "保留每段 WAV，便于局部重做和后期剪辑。" },
  { key: "output_format", label: "最终输出格式", type: "select", default: "WAV", options: ["WAV", "FLAC", "MP3", "OGG"], help: "WAV 无损且兼容性最好；MP3 更省空间。" },
  { key: "sample_rate", label: "统一采样率", type: "select", default: "保持引擎原始", options: ["保持引擎原始", "24000 Hz", "32000 Hz", "44100 Hz", "48000 Hz"], help: "合并前统一采样率，视频后期通常选 48kHz。" },
] };

const emotions = ["喜", "怒", "哀", "惧", "厌恶", "低落", "惊喜", "平静"].map((label, i): Field => ({ key: `emo_${i}`, label: `${label}向量`, type: "range", default: label === "平静" ? 0.4 : 0, min: 0, max: 1, step: 0.05, help: "八维情感之一；总强度会被模型限制在安全范围内。" }));

export const parameterGroups: Record<EngineId, Group[]> = {
  indextts2: [
    { title: "音色与情感", summary: "音色克隆与八维情感控制", fields: [
      { key: "spk_audio_prompt", label: "音色参考音频", type: "file", default: "", help: "必填。建议使用干净、无背景声的单人语音。" },
      { key: "emo_control", label: "情感控制方式", type: "select", default: "与音色参考一致", options: ["与音色参考一致", "情感参考音频", "情感向量", "情感描述文本"], help: "音色与情感可分离，四种方式中选择一种。" },
      { key: "emo_audio_prompt", label: "情感参考音频", type: "file", default: "", help: "仅提取表达方式，不改变主体音色；与向量、描述互斥。" },
      { key: "emo_alpha", label: "情感权重 emo_alpha", type: "range", default: 0.65, min: 0, max: 1, step: 0.01, help: "0 接近平静，1 最大程度使用情感提示；过高可能影响清晰度。" },
      { key: "emo_text", label: "情感描述文本", type: "textarea", default: "克制、温暖，带有可靠的叙述感", help: "用自然语言描述情绪和说话方式。" },
      { key: "use_emo_text", label: "使用文本情感", type: "toggle", default: false, help: "让模型从情感描述提取八维情感向量。" },
      { key: "use_random", label: "情感随机采样", type: "toggle", default: false, help: "增加情感变化，但同一文本的结果可能不同。" },
      ...emotions,
    ] },
    { title: "GPT 采样", summary: "随机性、稳定性与重复控制", fields: [
      { key: "do_sample", label: "do_sample", type: "toggle", default: true, help: "启用随机采样；关闭后更确定，但表现力可能下降。" },
      { key: "temperature", label: "temperature", type: "range", default: 0.8, min: 0.1, max: 2, step: 0.1, help: "降低更稳定，提高更活跃；过高可能错读。" },
      { key: "top_p", label: "top_p", type: "range", default: 0.8, min: 0, max: 1, step: 0.01, help: "核采样概率范围，降低可收窄候选。" },
      { key: "top_k", label: "top_k", type: "range", default: 30, min: 0, max: 100, step: 1, help: "每步仅从概率最高的 K 个候选中采样。" },
      { key: "num_beams", label: "num_beams", type: "number", default: 3, min: 1, max: 10, step: 1, help: "束搜索宽度；提高可能更稳，但更慢、更占显存。" },
      { key: "repetition_penalty", label: "repetition_penalty", type: "number", default: 10, min: 0.1, max: 20, step: 0.1, help: "抑制重复音节；过高可能吞字。" },
      { key: "length_penalty", label: "length_penalty", type: "number", default: 0, min: -2, max: 2, step: 0.1, help: "调节长度偏好，通常保持 0。" },
      { key: "max_mel_tokens", label: "max_mel_tokens", type: "number", default: 1500, min: 50, max: 4000, step: 10, help: "单段声学 Token 上限；过小会截断。" },
      { key: "max_text_tokens_per_segment", label: "单段文本 Token", type: "number", default: 120, min: 20, max: 240, step: 2, help: "内部句段上限，官方建议 80–200。" },
      { key: "interval_silence", label: "内部句间停顿", type: "number", default: 200, min: 0, max: 2000, step: 20, unit: "ms", help: "IndexTTS 内部分句间插入的静音。" },
      { key: "stream_return", label: "流式返回", type: "toggle", default: false, help: "逐块返回音频，首段更快可听。" },
      { key: "verbose", label: "详细推理日志", type: "toggle", default: false, help: "记录内部耗时与分段信息，用于排错。" },
    ] },
    { title: "运行环境与加速", summary: "模型目录、设备、精度与实验性加速", fields: [
      { key: "model_dir", label: "模型目录", type: "file", fileKind: "directory", default: "", help: "覆盖绑定程序的默认模型目录；留空时使用本地项目配置。" },
      { key: "device", label: "计算设备", type: "text", default: "cuda:0", help: "例如 cuda:0 或 cpu；多显卡时可指定设备编号。" },
      { key: "use_fp16", label: "FP16 半精度", type: "toggle", default: true, help: "降低显存并加速；旧显卡或出现数值异常时关闭。" },
      { key: "use_cuda_kernel", label: "CUDA 自定义内核", type: "toggle", default: true, help: "启用上游 CUDA 加速内核；本地环境不兼容时关闭。" },
      { key: "use_accel", label: "通用加速路径", type: "toggle", default: false, help: "启用上游可选加速实现；不兼容时关闭以回退到标准推理。" },
      { key: "use_torch_compile", label: "torch.compile", type: "toggle", default: false, help: "首次编译较慢，重复生成可能更快；显存紧张或报错时关闭。" },
      { key: "use_deepspeed", label: "DeepSpeed", type: "toggle", default: false, help: "使用已安装的 DeepSpeed 推理优化；普通 Windows 环境通常保持关闭。" },
      { key: "quick_streaming_tokens", label: "快速流式 Token", type: "number", default: 0, min: 0, max: 50, step: 1, help: "控制首段提前返回阈值；0 使用上游默认，过小可能影响衔接。" },
    ] }, longAudio,
  ],
  voxcpm: [
    { title: "音色模式", summary: "音色设计、续写与声音克隆", fields: [
      { key: "mode", label: "生成模式", type: "select", default: "可控音色克隆", options: ["音色设计", "可控音色克隆", "极致克隆", "普通合成"], help: "音色设计使用文字；极致克隆同时使用参考和精确转写。" },
      { key: "reference_wav_path", label: "音色参考音频", type: "file", default: "", help: "可控音色克隆与极致克隆模式必填；音色设计和普通合成可不填。建议使用干净、清晰的单人语音。" },
      { key: "prompt_wav_path", label: "续写提示音频", type: "file", default: "", help: "需与提示文本成对提供，用于保持韵律和上下文。" },
      { key: "prompt_text", label: "提示音频精确转写", type: "textarea", default: "", help: "必须准确对应提示音频，错字会降低相似度。" },
      { key: "voice_instruction", label: "音色 / 风格指令", type: "text", default: "成熟、清晰、节奏舒缓的中文旁白", help: "控制音色设计或克隆语音的语速、情绪与风格。" },
      { key: "denoise", label: "参考音频降噪", type: "toggle", default: false, help: "加载降噪器后清理参考；过度降噪可能损伤音色。" },
    ] },
    { title: "生成参数", summary: "扩散采样、长度与坏例重试", fields: [
      { key: "cfg_value", label: "CFG 引导强度", type: "range", default: 2, min: 0.5, max: 5, step: 0.1, help: "控制遵循文本与风格指令的程度，过高可能僵硬。" },
      { key: "inference_timesteps", label: "推理步数", type: "number", default: 10, min: 4, max: 50, step: 1, help: "越多通常越细致但越慢；10 是官方平衡值。" },
      { key: "min_len", label: "最小音频长度", type: "number", default: 2, min: 1, max: 64, step: 1, help: "生成器最小长度边界，通常无需修改。" },
      { key: "max_len", label: "最大 Token 长度", type: "number", default: 4096, min: 256, max: 8192, step: 128, help: "单次上限；过小会截断，过大增加显存。" },
      { key: "normalize", label: "文本规范化", type: "toggle", default: false, help: "处理数字、符号等；需保留特殊读法时关闭。" },
      { key: "retry_badcase", label: "坏例自动重试", type: "toggle", default: true, help: "音频时长与文本比例异常时自动重做。" },
      { key: "retry_badcase_max_times", label: "坏例最多重试", type: "number", default: 3, min: 0, max: 10, step: 1, help: "VoxCPM 内部坏例重试次数。" },
      { key: "retry_badcase_ratio_threshold", label: "坏例比例阈值", type: "number", default: 6, min: 1, max: 15, step: 0.5, help: "音频/文本比超过此值判为异常，太低会误判。" },
      { key: "seed", label: "随机种子", type: "number", default: 42, min: -1, max: 2147483647, step: 1, help: "固定数值可复现；-1 表示随机。" },
      { key: "streaming", label: "流式生成", type: "toggle", default: false, help: "边生成边返回音频块，适合即时试听。" },
    ] },
    { title: "模型、降噪与 LoRA", summary: "本地模型路径、缓存、优化和 LoRA 微调", fields: [
      { key: "model_path", label: "本地模型目录", type: "file", fileKind: "directory", default: "", help: "覆盖默认 VoxCPM2 模型目录；留空时使用绑定项目中的模型。" },
      { key: "hf_model_id", label: "Hugging Face 模型 ID", type: "text", default: "openbmb/VoxCPM2", help: "从缓存或网络解析的模型仓库 ID；已有本地模型目录时由本地路径优先。" },
      { key: "cache_dir", label: "模型缓存目录", type: "file", fileKind: "directory", default: "", help: "指定 Hugging Face 缓存位置，便于将大模型放到其他磁盘。" },
      { key: "local_files_only", label: "仅使用本地文件", type: "toggle", default: true, help: "禁止推理时联网补下载；离线和可复现环境建议开启。" },
      { key: "optimize", label: "启用模型优化", type: "toggle", default: true, help: "启用上游推理优化；出现兼容性问题时关闭。" },
      { key: "enable_denoiser", label: "启用内置降噪器", type: "toggle", default: true, help: "加载参考音频降噪模型；会额外占用显存和启动时间。" },
      { key: "zipenhancer_model_id", label: "降噪模型 ID", type: "text", default: "iic/speech_zipenhancer_ans_multiloss_16k_base", help: "覆盖 ZipEnhancer 降噪模型来源；仅启用内置降噪器时生效。" },
      { key: "device", label: "计算设备", type: "text", default: "cuda:0", help: "例如 cuda:0 或 cpu；多显卡时可指定设备编号。" },
      { key: "lora_weights_path", label: "LoRA 权重", type: "file", fileKind: "lora", default: "", help: "加载 VoxCPM2 LoRA 微调权重；留空时使用基础模型。" },
      { key: "lora_r", label: "LoRA Rank", type: "number", default: 32, min: 1, max: 256, step: 1, help: "LoRA 低秩维度，必须与训练权重配置一致。" },
      { key: "lora_alpha", label: "LoRA Alpha", type: "number", default: 16, min: 1, max: 512, step: 1, help: "LoRA 缩放系数，必须与训练权重配置一致。" },
      { key: "lora_dropout", label: "LoRA Dropout", type: "range", default: 0, min: 0, max: 1, step: 0.01, help: "推理通常为 0；仅用于兼容特定训练配置。" },
      { key: "lora_disable_lm", label: "LoRA 不作用于 LM", type: "toggle", default: false, help: "权重未训练语言模型层时开启，避免层结构不匹配。" },
      { key: "lora_disable_dit", label: "LoRA 不作用于 DiT", type: "toggle", default: false, help: "权重未训练 DiT 层时开启。" },
      { key: "lora_enable_proj", label: "LoRA 作用于投影层", type: "toggle", default: false, help: "只有训练时包含投影层 LoRA 才应开启。" },
    ] }, longAudio,
  ],
  gpt_sovits: [
    { title: "角色模型与参考", summary: "GPT/SoVITS 权重、参考音频与精确文本", fields: [
      { key: "gpt_weights_path", label: "GPT 权重（.ckpt）", type: "file", fileKind: "gpt-weight", default: "", help: "必填。决定语义与韵律能力；必须与下方 SoVITS 权重属于同一角色模型。" },
      { key: "sovits_weights_path", label: "SoVITS 权重（.pth）", type: "file", fileKind: "sovits-weight", default: "", help: "必填。决定声学音色；必须与 GPT 权重成对使用，不能混用其他角色。" },
      { key: "version", label: "模型版本", type: "select", default: "auto", options: ["auto", "v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus"], help: "auto 会从权重路径和实际加载结果识别版本，并自动套用对应采样默认值。" },
      { key: "ref_audio_path", label: "主参考音频", type: "file", fileKind: "audio", default: "", help: "必填。用于确定当前说话风格，建议 3–10 秒、单人、清晰无混响。" },
      { key: "aux_ref_audio_paths", label: "辅助参考音频", type: "file", fileKind: "audio", default: "", help: "可加入一条辅助参考，融合音色与语气。" },
      { key: "prompt_text", label: "参考音频文本", type: "textarea", default: "", help: "精确转写；留空时相似度可能下降。" },
      { key: "prompt_lang", label: "参考文本语言", type: "select", default: "中文", options: ["中文", "英文", "日文", "韩文", "粤语", "中英混合", "日英混合", "多语种混合"], help: "必须与参考音频实际语言一致。" },
      { key: "text_lang", label: "目标文本语言", type: "select", default: "中文", options: ["中文", "英文", "日文", "韩文", "粤语", "中英混合", "日英混合", "多语种混合"], help: "决定前端预处理和发音模型选择。" },
      { key: "text_split_method", label: "内部分句方法", type: "select", default: "cut5｜按标点", options: ["cut0｜不切", "cut1｜四句一切", "cut2｜约 50 字", "cut3｜中文句号", "cut4｜英文句号", "cut5｜按标点"], help: "引擎自带分句，长音频还会经过应用级分段。" },
    ] },
    { title: "采样与性能", summary: "完整 API v2 推理参数", fields: [
      { key: "top_k", label: "top_k", type: "number", default: 15, min: 1, max: 100, step: 1, help: "从概率最高的 K 个候选采样。" },
      { key: "top_p", label: "top_p", type: "range", default: 1, min: 0.1, max: 1, step: 0.05, help: "核采样范围；降低可减少异常发音。" },
      { key: "temperature", label: "temperature", type: "range", default: 1, min: 0.1, max: 2, step: 0.05, help: "控制随机性；过高可能不稳定。" },
      { key: "batch_size", label: "批量大小", type: "number", default: 1, min: 1, max: 32, step: 1, help: "并行句段数，增大提速但增加显存。" },
      { key: "batch_threshold", label: "批处理阈值", type: "range", default: 0.75, min: 0.1, max: 1, step: 0.05, help: "控制不同长度句段能否进入同一批。" },
      { key: "split_bucket", label: "长度分桶", type: "toggle", default: true, help: "把相近长度句段分桶，提高效率。" },
      { key: "speed_factor", label: "语速倍率", type: "range", default: 1, min: 0.5, max: 2, step: 0.05, help: "1 为原速；极端值会影响自然度。" },
      { key: "fragment_interval", label: "片段间隔", type: "number", default: 0.3, min: 0, max: 2, step: 0.05, unit: "s", help: "内部片段之间的静音。" },
      { key: "seed", label: "随机种子", type: "number", default: -1, min: -1, max: 2147483647, step: 1, help: "-1 随机；固定数值便于复现。" },
      { key: "streaming_mode", label: "流式模式", type: "select", default: "0｜关闭", options: ["0｜关闭", "1｜最高质量", "2｜平衡", "3｜低延迟"], help: "数值越高首包越快，但衔接可能下降。" },
      { key: "parallel_infer", label: "并行推理", type: "toggle", default: true, help: "并行生成片段；关闭可降低显存峰值。" },
      { key: "repetition_penalty", label: "重复惩罚", type: "number", default: 1.35, min: 0.5, max: 3, step: 0.05, help: "抑制重复音节；过高可能漏字。" },
      { key: "sample_steps", label: "VITS 采样步数", type: "number", default: 8, min: 4, max: 128, step: 1, help: "按版本自动适配：v3 默认 32，v4 默认 8；v1、v2 与 Pro 系列不使用该参数。手动修改后保留自定义值。" },
      { key: "super_sampling", label: "超采样", type: "toggle", default: false, help: "仅 v3 支持；其他版本会自动关闭。会增加生成时间和显存。" },
      { key: "overlap_length", label: "流式重叠长度", type: "number", default: 2, min: 0, max: 16, step: 1, help: "流式块之间重叠，减轻接缝。" },
      { key: "min_chunk_length", label: "最小流式块长度", type: "number", default: 16, min: 1, max: 128, step: 1, help: "越小首包越快，但调用更频繁。" },
      { key: "media_type", label: "API 媒体格式", type: "select", default: "wav", options: ["wav", "raw", "ogg", "aac"], help: "引擎直接返回格式；最终导出由输出设置决定。" },
      { key: "return_fragment", label: "分片返回", type: "toggle", default: false, help: "使用上游旧版最佳质量分片返回；应用仍会汇总为完整段落。" },
      { key: "fixed_length_chunk", label: "固定长度流式块", type: "toggle", default: false, help: "更快返回固定长度音频块，但上游注明可能降低质量。" },
    ] },
    { title: "运行配置与基础模型", summary: "YAML、设备、精度与前处理模型目录", fields: [
      { key: "tts_config_path", label: "推理配置 YAML", type: "file", fileKind: "yaml", default: "", help: "覆盖项目默认推理配置，定义版本、设备与默认权重路径。" },
      { key: "device", label: "计算设备覆盖", type: "text", default: "", help: "例如 cuda、cuda:0 或 cpu；留空时跟随 YAML。" },
      { key: "is_half", label: "半精度覆盖", type: "select", default: "跟随配置", options: ["跟随配置", "开启", "关闭"], help: "跟随 YAML 最安全；显卡不支持 FP16 或出现异常时关闭。" },
      { key: "bert_base_path", label: "BERT 模型目录", type: "file", fileKind: "directory", default: "", help: "中文文本前处理 BERT 路径；留空时使用项目配置。" },
      { key: "cnhuhbert_base_path", label: "CNHuBERT 模型目录", type: "file", fileKind: "directory", default: "", help: "参考音频特征模型路径；留空时使用项目配置。" },
    ] }, longAudio,
  ],
};

export const defaultsFor = (engine: EngineId, context: Record<string, unknown> = {}) => {
  const defaults = Object.fromEntries(parameterGroups[engine].flatMap(g => g.fields).map(f => [f.key, f.default]));
  return engine === "gpt_sovits" ? { ...defaults, ...gptSovitsDefaultsFor(context) } : defaults;
};
