import { useMemo, useState } from "react";
import {
  AlertCircle, BookOpen, CheckCircle2, ClipboardPaste, FileText, Settings2,
  Upload, UserRoundCog, UsersRound,
} from "lucide-react";
import type { VoiceProfile } from "./VoiceProfiles";
import {
  MULTI_SPEAKER_QUALITY_PRESETS, parseMultiSpeakerScript,
  type MultiSpeakerAssignmentSelection,
  type MultiSpeakerQualityPreset,
} from "./multiSpeaker";

type Preset = { id: string; name: string; parameters: Record<string, unknown> };

export function MultiSpeakerEditor({
  projectName,
  onProjectNameChange,
  projectSaved,
  script,
  onScriptChange,
  assignments,
  onAssignmentsChange,
  voices,
  presets,
  lineIntervalMs,
  onLineIntervalChange,
  qualityPreset,
  onQualityPresetChange,
  onImport,
  onOpenVoices,
  onOpenParameters,
}: {
  projectName: string;
  onProjectNameChange: (value: string) => void;
  projectSaved: boolean;
  script: string;
  onScriptChange: (value: string) => void;
  assignments: Record<string, MultiSpeakerAssignmentSelection>;
  onAssignmentsChange: (value: Record<string, MultiSpeakerAssignmentSelection>) => void;
  voices: VoiceProfile[];
  presets: Preset[];
  lineIntervalMs: number;
  onLineIntervalChange: (value: number) => void;
  qualityPreset: MultiSpeakerQualityPreset;
  onQualityPresetChange: (value: MultiSpeakerQualityPreset) => void;
  onImport: () => void;
  onOpenVoices: () => void;
  onOpenParameters: () => void;
}) {
  const [guideOpen, setGuideOpen] = useState(false);
  const parsed = useMemo(() => parseMultiSpeakerScript(script), [script]);
  const unmapped = parsed.speakers.filter(speaker => !voices.some(voice => voice.id === assignments[speaker]?.voiceProfileId));
  const updateAssignment = (speaker: string, patch: Partial<MultiSpeakerAssignmentSelection>) => {
    const current = assignments[speaker] ?? { voiceProfileId: "", presetId: "" };
    onAssignmentsChange({ ...assignments, [speaker]: { ...current, ...patch } });
  };
  const paste = async () => {
    const value = await navigator.clipboard.readText();
    if (value) onScriptChange(value);
  };

  return <section className="multi-speaker-panel">
    <header className="multi-speaker-heading">
      <div className="section-label compact"><span>02</span><div><strong>多人剧本与角色映射</strong><small>每行一名说话人，按原始顺序生成并合并</small></div></div>
      <div className="multi-speaker-toolbar">
        <button onClick={onImport}><Upload size={16} />导入 TXT</button>
        <button onClick={() => void paste()}><ClipboardPaste size={16} />粘贴剧本</button>
        <button onClick={() => setGuideOpen(value => !value)}><BookOpen size={16} />格式规范</button>
        <button className="parameter-entry" onClick={onOpenParameters}><Settings2 size={16} />当前推理参数</button>
      </div>
    </header>

    <div className="document-title multi-project-title">
      <input aria-label="任务名称" value={projectName} onChange={event => onProjectNameChange(event.target.value)} placeholder="根据首条台词自动命名" />
      <span>{projectSaved ? "已保存项目" : "未保存"}</span>
    </div>

    {guideOpen && <div className="multi-format-guide">
      <BookOpen size={18} />
      <div><strong>多人剧本格式</strong><p>每个非空行必须写成 <code>【角色名】：台词</code>，旁白也必须明确写为 <code>【旁白】：内容</code>。同一角色连续出现时仍保持为独立台词。</p><pre>【旁白】：雨声敲打着窗户。{"\n"}【小明】：你终于来了。{"\n"}【小红】：路上耽搁了一会儿。</pre></div>
    </div>}

    <div className="multi-speaker-workspace">
      <article className="multi-script-card">
        <div className="multi-card-title"><span><FileText size={18} /></span><div><strong>剧本文本</strong><small>{parsed.lines.length} 条台词 · {parsed.speakers.length} 个角色</small></div></div>
        <textarea
          aria-label="多人配音剧本"
          aria-invalid={parsed.invalidLines.length > 0}
          value={script}
          onChange={event => onScriptChange(event.target.value)}
          placeholder="【旁白】：故事开始了。&#10;【角色名】：角色台词。"
        />
        {parsed.invalidLines.length > 0
          ? <div className="multi-validation error" role="alert"><AlertCircle size={17} /><div><span>第 {parsed.invalidLines.join("、")} 行缺少有效标签；修正前不能生成。</span><div className="multi-invalid-lines">{parsed.invalidLines.map(lineNumber => <code key={lineNumber}><b>{lineNumber}</b>{script.split(/\r?\n/)[lineNumber - 1] || "（空台词）"}</code>)}</div></div></div>
          : parsed.lines.length > 0
            ? <div className="multi-validation success" role="status"><CheckCircle2 size={17} /><span>格式有效，已按物理行保留 {parsed.lines.length} 条独立台词。</span></div>
            : <div className="multi-validation"><FileText size={17} /><span>导入或粘贴符合规范的 TXT 剧本。</span></div>}
      </article>

      <article className="multi-mapping-card">
        <div className="multi-card-title"><span><UsersRound size={18} /></span><div><strong>角色声音映射</strong><small>质量策略统一稳定参数；角色预设负责其余参数</small></div></div>
        <div className="multi-global-settings">
          <label className="multi-quality-setting" htmlFor="multi-quality-preset"><span>质量策略</span><select id="multi-quality-preset" value={qualityPreset} onChange={event => onQualityPresetChange(event.target.value as MultiSpeakerQualityPreset)}>{MULTI_SPEAKER_QUALITY_PRESETS.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select><small>{MULTI_SPEAKER_QUALITY_PRESETS.find(option => option.id === qualityPreset)?.description}</small></label>
          <label className="multi-interval-setting" htmlFor="multi-line-interval"><span>台词间隔</span><div><input id="multi-line-interval" type="number" min="0" max="10000" step="10" value={lineIntervalMs} onChange={event => onLineIntervalChange(Math.max(0, Math.min(10000, Number(event.target.value) || 0)))} /><b>ms</b></div></label>
        </div>
        {parsed.speakers.length === 0 ? <div className="multi-mapping-empty"><UserRoundCog size={24} /><strong>等待识别角色</strong><span>输入有效剧本后，角色会自动出现在这里。</span></div> : <div className="multi-role-list">{parsed.speakers.map(speaker => {
          const selected = assignments[speaker] ?? { voiceProfileId: "", presetId: "" };
          return <fieldset className="multi-role-row" key={speaker}><legend>【{speaker}】</legend><label><span>角色声音</span><select value={selected.voiceProfileId} onChange={event => updateAssignment(speaker, { voiceProfileId: event.target.value })}><option value="">请选择已保存声音</option>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select></label><label><span>参数预设</span><select value={selected.presetId} onChange={event => updateAssignment(speaker, { presetId: event.target.value })}><option value="">使用当前推理参数</option>{presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label></fieldset>;
        })}</div>}
        {voices.length === 0 && <button className="secondary-button multi-open-voices" onClick={onOpenVoices}><UserRoundCog size={16} />先创建 GPT-SoVITS 角色声音</button>}
        {parsed.speakers.length > 0 && <div className={`multi-readiness ${parsed.valid && unmapped.length === 0 ? "ready" : ""}`}>{parsed.valid && unmapped.length === 0 ? <><CheckCircle2 size={17} /><span>全部角色已匹配，可以生成。</span></> : <><AlertCircle size={17} /><span>{unmapped.length ? `还有 ${unmapped.length} 个角色未匹配声音。` : "请先修正剧本格式。"}</span></>}</div>}
      </article>
    </div>
  </section>;
}
