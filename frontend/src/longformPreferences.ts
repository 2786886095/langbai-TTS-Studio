import type { EngineId } from "./parameterSchemas";
import type { LongformEngineId } from "./LongformWorkspace";

export type EngineWorkspacePreference = {
  voiceId?: string; refAudio?: string; refText?: string;
  output?: { format: "WAV" | "FLAC" | "MP3"; sampleRate: "native" | number; silenceMs: number; keepSegments: boolean };
  maxChars?: number; strategy?: "breeze_han_v1" | "generic_v2" | "paragraph";
  params?: Record<string, unknown>;
};
type Preferences = { engine?: LongformEngineId; perEngine: Partial<Record<LongformEngineId, EngineWorkspacePreference>> };
const key = "langbai-longform-preferences-v2";
const engineIds = ["breeze", "indextts2", "voxcpm", "gpt_sovits"];
export function readLongformPreferences(): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "{}") as Partial<Preferences>;
    return { engine: engineIds.includes(String(value.engine)) ? value.engine : undefined, perEngine: value.perEngine && typeof value.perEngine === "object" ? value.perEngine : {} };
  } catch { return { perEngine: {} }; }
}
export function saveEnginePreference(engine: LongformEngineId, value: EngineWorkspacePreference) {
  try { const previous = readLongformPreferences(); localStorage.setItem(key, JSON.stringify({ ...previous, engine, perEngine: { ...previous.perEngine, [engine]: value } })); } catch { /* Draft export and server-side voices remain available if browser storage is full. */ }
}
export function savedLegacyParameters() {
  const saved = readLongformPreferences();
  return Object.fromEntries(["indextts2", "voxcpm", "gpt_sovits"].map(engine => [engine, saved.perEngine[engine as EngineId]?.params ?? {}])) as Partial<Record<EngineId, Record<string, unknown>>>;
}
