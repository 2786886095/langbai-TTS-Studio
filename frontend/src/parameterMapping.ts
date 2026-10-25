import { gptSovitsDefaultsFor, type EngineId } from "./parameterSchemas";

const longAudioKeys = new Set(["split_mode", "segment_chars", "segment_pause", "retry_count", "resume", "keep_segments", "output_format", "sample_rate"]);
const languageCodes: Record<string, string> = { 中文: "zh", 英文: "en", 日文: "ja", 韩文: "ko", 粤语: "yue", 中英混合: "auto", 日英混合: "auto", 多语种混合: "auto" };

/** The legacy API accepts an explicit integer rate; it has no native sentinel. */
export function legacySampleRate(value: unknown): number | null {
  const match = /^(\d+)(?:\s*Hz)?$/i.exec(String(value ?? "").trim());
  if (!match) return null;
  const rate = Number(match[1]);
  return Number.isInteger(rate) && rate >= 8000 && rate <= 192000 ? rate : null;
}

export function toApiParams(engine: EngineId, values: Record<string, unknown>) {
  const clean = Object.fromEntries(Object.entries(values).filter(([key]) => !longAudioKeys.has(key)));
  if (engine === "indextts2") {
    const modeMap: Record<string, string> = { "与音色参考一致": "audio", "情感参考音频": "audio", "情感向量": "vector", "情感描述文本": "text" };
    return {
      speaker_audio: clean.spk_audio_prompt, emotion_mode: modeMap[String(clean.emo_control)] ?? "audio",
      emotion_audio: clean.emo_audio_prompt || null, emotion_alpha: clean.emo_alpha,
      emotion_text: clean.emo_text || null, emotion_vector: Array.from({ length: 8 }, (_, i) => Number(clean[`emo_${i}`] ?? 0)),
      use_random: clean.use_random, interval_silence: clean.interval_silence,
      max_text_tokens_per_segment: clean.max_text_tokens_per_segment, stream_return: clean.stream_return,
      quick_streaming_tokens: clean.quick_streaming_tokens, model_dir: clean.model_dir || null,
      device: clean.device || null, use_fp16: clean.use_fp16, use_cuda_kernel: clean.use_cuda_kernel,
      use_deepspeed: clean.use_deepspeed, use_accel: clean.use_accel, use_torch_compile: clean.use_torch_compile,
      do_sample: clean.do_sample, top_p: clean.top_p, top_k: clean.top_k, temperature: clean.temperature,
      length_penalty: clean.length_penalty, num_beams: clean.num_beams, repetition_penalty: clean.repetition_penalty, max_mel_tokens: clean.max_mel_tokens,
    };
  }
  if (engine === "voxcpm") {
    const { mode: _mode, voice_instruction, reference_wav_path, prompt_wav_path, ...rest } = clean;
    return { ...rest, control: voice_instruction || null, reference_audio: reference_wav_path || null, prompt_audio: prompt_wav_path || null };
  }
  const versionDefaults = gptSovitsDefaultsFor(clean);
  const { gpt_weights_path, sovits_weights_path, ref_audio_path, aux_ref_audio_paths, prompt_lang, text_lang, is_half, ...rest } = clean;
  return {
    ...rest, t2s_weights_path: gpt_weights_path, vits_weights_path: sovits_weights_path,
    reference_audio: ref_audio_path, aux_reference_audios: aux_ref_audio_paths ? [aux_ref_audio_paths] : null,
    prompt_language: languageCodes[String(prompt_lang)] ?? "auto", text_language: languageCodes[String(text_lang)] ?? "auto",
    is_half: is_half === "跟随配置" ? null : is_half === "开启",
    text_split_method: String(rest.text_split_method).split("｜")[0], streaming_mode: Number(String(rest.streaming_mode).split("｜")[0]),
    sample_steps_auto: Number(rest.sample_steps) === versionDefaults.sample_steps,
  };
}
