export type CreationMode = "single" | "multi_speaker";
export type MultiSpeakerQualityPreset = "stable" | "balanced" | "expressive";

export const MULTI_SPEAKER_QUALITY_PRESETS: Array<{
  id: MultiSpeakerQualityPreset;
  name: string;
  description: string;
}> = [
  { id: "stable", name: "稳定优先（推荐）", description: "固定角色音色并统一稳定参数，减少机械感与生硬断句" },
  { id: "balanced", name: "自然平衡", description: "兼顾稳定、自然停顿与生成速度" },
  { id: "expressive", name: "表现力优先", description: "保留更多情绪变化，波动风险略高" },
];

export type ParsedSpeakerLine = {
  lineNumber: number;
  speaker: string;
  text: string;
};

export type MultiSpeakerAssignmentSelection = {
  voiceProfileId: string;
  presetId: string;
};

export type MultiSpeakerProjectSettings = {
  lineIntervalMs: number;
  qualityPreset?: MultiSpeakerQualityPreset;
  assignments: Record<string, MultiSpeakerAssignmentSelection>;
};

export const GPT_VOICE_PARAMETER_KEYS = new Set([
  "gpt_weights_path", "sovits_weights_path", "ref_audio_path", "aux_ref_audio_paths",
  "prompt_text", "prompt_lang", "version",
]);

export const GPT_VOICE_API_KEYS = new Set([
  "t2s_weights_path", "vits_weights_path", "reference_audio", "aux_reference_audios",
  "prompt_text", "prompt_language", "version",
]);

export function onlyGptVoiceParameters(parameters: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(parameters).filter(([key]) => GPT_VOICE_PARAMETER_KEYS.has(key)));
}

export function withoutGptVoiceParameters(parameters: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(parameters).filter(([key]) => !GPT_VOICE_PARAMETER_KEYS.has(key)));
}

export function withoutGptVoiceApiParameters(parameters: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(parameters).filter(([key]) => !GPT_VOICE_API_KEYS.has(key)));
}

export function parseMultiSpeakerScript(script: string) {
  const lines: ParsedSpeakerLine[] = [];
  const invalidLines: number[] = [];
  script.split(/\r?\n/).forEach((rawLine, index) => {
    if (!rawLine.trim()) return;
    const match = rawLine.match(/^\s*【([^】\r\n]{1,80})】\s*[：:]\s*(.*?)\s*$/);
    const speaker = match?.[1]?.trim() ?? "";
    const text = match?.[2]?.trim() ?? "";
    if (!match || !speaker || !text) {
      invalidLines.push(index + 1);
      return;
    }
    lines.push({ lineNumber: index + 1, speaker, text });
  });
  const speakers = Array.from(new Set(lines.map(line => line.speaker)));
  return { lines, speakers, invalidLines, valid: lines.length > 0 && invalidLines.length === 0 };
}
