const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const cache = new Map();
function sourceModule(name) {
  const filename = path.resolve(__dirname, "../src", `${name}.ts`);
  if (cache.has(filename)) return cache.get(filename).exports;
  const source = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  cache.set(filename, module);
  new Function("require", "module", "exports", source)(specifier => {
    if (specifier.startsWith("./")) return sourceModule(specifier.slice(2));
    return require(specifier);
  }, module, module.exports);
  return module.exports;
}

const { defaultsFor, parameterGroups } = sourceModule("parameterSchemas");
const { toApiParams, legacySampleRate } = sourceModule("parameterMapping");
const uiOnly = ["split_mode", "segment_chars", "segment_pause", "retry_count", "resume", "keep_segments", "output_format", "sample_rate"];
for (const engine of ["indextts2", "voxcpm", "gpt_sovits"]) {
  const request = JSON.parse(JSON.stringify(toApiParams(engine, defaultsFor(engine))));
  for (const key of uiOnly) assert.equal(key in request, false, `${engine} leaked task option ${key}`);
  assert.equal(Object.keys(request).some(key => /^emo_\d$/.test(key)), false, "emotion sliders must become a vector");
  const legacyOutput = parameterGroups[engine].find(group => group.title === "长音频与输出");
  for (const unsupported of ["split_mode", "resume", "output_format"]) assert.equal(legacyOutput.fields.some(field => field.key === unsupported), false, `legacy UI must not advertise ${unsupported}`);
  assert.equal(defaultsFor(engine).sample_rate, "44100 Hz", "legacy default must explicitly name the fixed output rate");
}
assert.equal(legacySampleRate("44100 Hz"), 44100);
assert.equal(legacySampleRate(24000), 24000);
assert.equal(legacySampleRate("保持引擎原始"), null, "legacy API cannot silently substitute 44100 for native");
assert.equal(legacySampleRate("native"), null);
assert.equal(legacySampleRate(undefined), null);
assert.equal(legacySampleRate("0 Hz"), null);
const index = toApiParams("indextts2", { ...defaultsFor("indextts2"), spk_audio_prompt: "F:\\参考.wav", emo_control: "情感向量", emo_0: 0.7 });
assert.equal(index.speaker_audio, "F:\\参考.wav");
assert.equal(index.emotion_mode, "vector");
assert.equal(index.emotion_vector[0], 0.7);
assert.equal("spk_audio_prompt" in index, false);
const vox = toApiParams("voxcpm", { ...defaultsFor("voxcpm"), reference_wav_path: "F:\\声线.wav", prompt_wav_path: "F:\\提示.wav", prompt_text: "准确的提示文字" });
assert.equal(vox.reference_audio, "F:\\声线.wav");
assert.equal(vox.prompt_audio, "F:\\提示.wav");
assert.equal(vox.prompt_text, "准确的提示文字");
assert.equal("reference_wav_path" in vox, false);
const gpt = toApiParams("gpt_sovits", { ...defaultsFor("gpt_sovits"), gpt_weights_path: "F:\\role.ckpt", sovits_weights_path: "F:\\role.pth", ref_audio_path: "F:\\参考.wav", version: "v3" });
assert.equal(gpt.t2s_weights_path, "F:\\role.ckpt");
assert.equal(gpt.vits_weights_path, "F:\\role.pth");
assert.equal(gpt.reference_audio, "F:\\参考.wav");
assert.equal(gpt.prompt_language, "zh");
assert.equal(typeof gpt.streaming_mode, "number");
assert.equal(gpt.text_split_method, "cut5");
const storage = new Map();
global.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
const { readLongformPreferences, saveEnginePreference } = sourceModule("longformPreferences");
saveEnginePreference("indextts2", { voiceId: "index-voice", params: { spk_audio_prompt: "F:\\index.wav" }, maxChars: 180 });
saveEnginePreference("breeze", { voiceId: "breeze-voice", refAudio: "F:\\breeze.wav", refText: "参考文字", maxChars: 120 });
const preferences = readLongformPreferences();
assert.equal(preferences.engine, "breeze");
assert.equal(preferences.perEngine.indextts2.voiceId, "index-voice");
assert.equal(preferences.perEngine.breeze.voiceId, "breeze-voice");
assert.equal(preferences.perEngine.indextts2.refAudio, undefined, "Breeze references must not leak to another engine");
assert.equal(preferences.perEngine.breeze.params, undefined, "IndexTTS parameters must not leak to Breeze");
storage.set("langbai-longform-preferences-v2", "broken JSON");
assert.deepEqual(readLongformPreferences(), { perEngine: {} });
console.log("Parameter mapping and engine-isolated workspace persistence checks passed.");

async function checkControls() {
  const { controlLongformJob } = sourceModule("longformControls");
  const versions = [];
  let reads = 0;
  let writes = 0;
  const result = await controlLongformJob("job-1", "pause", async (_path, options) => {
    if (!options) return { id: "job-1", status: "generating", version: ++reads };
    writes++;
    versions.push(options.body.version);
    if (writes < 3) throw new Error("任务已被其他操作更新，请刷新后重试");
    return { id: "job-1", status: "paused", version: 4 };
  });
  assert.equal(result.job.status, "paused");
  assert.deepEqual(versions, [1, 2, 3]);
  let failures = 0;
  await assert.rejects(() => controlLongformJob("job-1", "stop", async (_path, options) => {
    if (!options) return { status: "generating", version: 5 };
    failures++; throw new Error("存储设备不可写");
  }), /存储设备不可写/);
  assert.equal(failures, 1, "non-version failures must not be retried");
  let duplicateWrites = 0;
  const paused = await controlLongformJob("job-1", "pause", async (_path, options) => {
    if (options) duplicateWrites++;
    return { status: "paused", version: 6 };
  });
  assert.equal(paused.job.status, "paused");
  assert.equal(duplicateWrites, 0, "already satisfied controls must not issue another mutation");
  let changedStateReads = 0;
  let changedStateWrites = 0;
  const finished = await controlLongformJob("job-1", "pause", async (_path, options) => {
    if (!options) return { status: ++changedStateReads === 1 ? "generating" : "completed", version: changedStateReads };
    changedStateWrites++; throw new Error("任务已被其他操作更新，请刷新后重试");
  });
  assert.equal(finished.job.status, "completed");
  assert.equal(changedStateWrites, 1, "a refreshed completed job must not receive another pause request");
  let exhaustedWrites = 0;
  await assert.rejects(() => controlLongformJob("job-1", "pause", async (_path, options) => {
    if (!options) return { status: "generating", version: 7 };
    exhaustedWrites++; throw new Error("任务已被其他操作更新，请刷新后重试");
  }), /其他操作更新/);
  assert.equal(exhaustedWrites, 3, "version retry must remain bounded");
  console.log("Fresh-version task controls passed: bounded retry, no unrelated retry, idempotent completion.");
}
checkControls().catch(error => { console.error(error); process.exitCode = 1; });
