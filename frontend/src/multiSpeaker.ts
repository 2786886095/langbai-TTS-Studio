export type CreationMode = "single" | "multi_speaker";

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
