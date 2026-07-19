# 三引擎推理入口与参数基线

本文件用于锁定“完整照搬原生推理参数”的验收含义。范围是推理、模型选择/加载及推理期 LoRA；不包含数据切分、ASR 标注和训练。

机器可读基线位于 `tests/acceptance/engine_parameter_baseline.json`。中文说明可以优化措辞，但不得改变参数语义。

## IndexTTS2

真实入口：

- `<IndexTTS2-root>\indextts\infer_v2.py` 的 `IndexTTS2`；
- 非流式 `infer()`，流式 `infer_generator()`；
- 当前官方 Web UI 的实际生成参数位于 `webui.py::gen_single()`。

必须覆盖：正文、说话人参考音频、情感控制方式、情感参考音频、情感权重、8 维情感向量、情感描述文本、情感随机采样、段间静音、每段最大文本 token，以及 `do_sample`、`top_p`、`top_k`、`temperature`、`length_penalty`、`num_beams`、`repetition_penalty`、`max_mel_tokens`。若产品支持原生流式，还需覆盖 `stream_return` 与 `quick_streaming_tokens`。

运行时设置需覆盖模型目录、设备、FP16、DeepSpeed、CUDA kernel、GPT2 accel、`torch.compile`。这些设置不应在每个普通任务里反复出现，可放在引擎设置页。

关键互斥：使用情感文本或情感向量时，原实现会清除情感参考音频；向量顺序固定为喜、怒、哀、惧、厌恶、低落、惊喜、平静，原实现会归一化总强度。

## VoxCPM

真实入口：

- `<VoxCPM-root>\src\voxcpm\core.py` 的 `VoxCPM.generate()` / `generate_streaming()`；
- `app.py` 的“控制指令”会编码为 `(控制指令)正文`；
- `prompt_wav_path + prompt_text` 是续写/克隆提示对，`reference_wav_path` 是 VoxCPM2 的独立音色参考。

必须覆盖：正文、控制指令、提示音频、提示文本、独立参考音频、CFG、推理步数、最小/最大生成长度、文本规范化、参考降噪、坏例重试开关/次数/比例阈值、流式开关、随机种子。

运行时设置需覆盖本地模型路径或 Hub ID、缓存目录、离线模式、设备、是否优化、是否加载降噪器及 ZipEnhancer 路径。推理期 LoRA 权重与 LoRA 配置也属于可用推理能力，但不包含训练流程。

关键约束：`prompt_wav_path` 与 `prompt_text` 必须同时提供或同时为空；独立 `reference_wav_path` 仅 VoxCPM2 支持。

## GPT-SoVITS

真实入口：

- `<GPT-SoVITS-root>\GPT_SoVITS\TTS_infer_pack\TTS.py` 的 `TTS.run(inputs)`；
- `api_v2.py` 的 `TTS_Request` 是当前公开请求面；
- GPT 与 SoVITS 权重可分别切换，且模型版本会改变合法语言和部分参数能力。

必须覆盖：正文/正文语言、主参考音频、辅助参考音频列表、参考文本/参考语言、`top_k`、`top_p`、`temperature`、文本切分方法、批大小、批分桶阈值、是否分桶、语速、片段间隔、种子、媒体格式、流式模式、并行推理、重复惩罚、采样步数、超采样、流式重叠长度、最小块长度、逐片段返回与固定长度块。

运行时设置需覆盖 TTS 配置、GPT 权重、SoVITS 权重、设备、半精度与模型版本/能力显示。

关键约束：语言集合依 v1/v2+ 变化；`sample_steps` 和 `super_sampling` 只对特定模型版本有效；Studio 外层已负责长文本分段时，要避免再次使用会改变边界的内部分段策略。

## 参数说明验收

每个参数至少提供：

- 原生字段名（便于调试）；
- 中文显示名；
- 中文用途说明；
- 类型、默认值、合法范围或枚举；
- 生效条件/版本限制；
- 对速度、质量、显存或稳定性的主要影响；
- 是否会与另一参数互斥。

仅把英文名翻译成中文不算“用途说明”。例如 `temperature` 的合格说明应指出：提高后采样更随机、表现可能更多样但稳定性下降；降低后更保守、更可复现。
