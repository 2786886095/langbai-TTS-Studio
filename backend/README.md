# langbai TTS Studio 后端

FastAPI 统一任务服务。三个引擎分别运行在自己的常驻 Python 子进程中，避免依赖冲突并避免长音频每段重复加载模型。任务按单 GPU 队列执行，状态和分段结果写入 `data/jobs/<job-id>/manifest.json`，进程重启后会保留已完成段并续作。

## 启动

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe run.py
```

服务仅监听 `127.0.0.1:18765`。环境变量和三个引擎的解释器、源码及运行资产路径见 `.env.example`。GPT-SoVITS 明确区分：

- `LANGBAI_GPT_SOVITS_PROJECT`：最新官方源码目录；
- `LANGBAI_GPT_SOVITS_PYTHON`：可用发行版的 Python；
- `LANGBAI_GPT_SOVITS_RUNTIME_ROOT`：模型、YAML、权重所在的发行版目录。

`GET /api/engines/status` 的 `installed` 只表示路径完整；`ready` 才表示该引擎工作进程已启动。首次真实生成会完成模型权重加载与最终兼容性验证。

## API

- `GET /health`
- `GET /api/engines`：引擎、状态、完整参数元数据和中文调试说明
- `GET /api/engines/status`
- `GET /api/engines/{engine}/parameters`
- `GET /api/jobs`
- `POST /api/jobs`
- `GET /api/jobs/{id}`
- `POST /api/jobs/{id}/cancel`
- `POST /api/jobs/{id}/retry`
- `GET /api/events`：全局 SSE
- `GET /api/jobs/{id}/events`：单任务 SSE

创建任务示例：

```json
{
  "engine": "indextts2",
  "text": "需要合成的长文本……",
  "params": {
    "speaker_audio": "F:/voices/reference.wav"
  },
  "longAudio": {
    "maxChars": 180,
    "silenceMs": 250,
    "targetSampleRate": 44100,
    "keepSegments": true,
    "maxRetries": 2
  }
}
```

任务状态为 `queued / running / completed / failed / cancelled`；分段状态为 `pending / running / completed / failed`。`attempts` 是跨手工重试累计的真实调用次数。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest ..\tests\acceptance
```

测试替身模式使用 `LANGBAI_TTS_MOCK=1`。仅该模式允许 `mock_fail_segment_once`、`mock_segment_delay_ms` 与 `mock_sample_rate`，正式引擎对未知参数返回 HTTP 400。

## 安装管理器

安装器不会接受客户端传入的仓库 URL、可执行文件或任意命令。官方来源、提交和模型版本固定在 `app/installer/catalog.py`：

| 引擎 | 官方源码 | 固定提交 | 代码许可证 | 独立环境安装方式 |
| --- | --- | --- | --- | --- |
| IndexTTS2 | `index-tts/index-tts` | `13495845e3028f0bb6ca1462ad22aa0e76349e40` | Bilibili IndexTTS Model Use License | Python 3.11.13，官方 `uv sync --frozen` 工作流 |
| VoxCPM2 | `OpenBMB/VoxCPM` | `616d3d3e630a9c96c2853250eef91b0f39dcd5fa` | Apache-2.0 | Python 3.11.13，从固定源码执行 `uv pip install` |
| GPT-SoVITS | `RVC-Boss/GPT-SoVITS` | `be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2` | MIT | Python 3.10.18，按官方手动安装顺序处理 PyTorch、`extra-req.txt`、`requirements.txt` |

模型权重不会随源码环境自动下载。用户明确选择并接受模型许可证后，才会从固定 Hugging Face 仓库与 revision 下载：`IndexTeam/IndexTTS-2`、`openbmb/VoxCPM2`、`lj1995/GPT-SoVITS`。

默认布局：

```text
<install-root>/
  installations/<engine>/
    source/
    env/
    installation.json
  models/<engine>/<model-id>/
    ...model files...
    model-manifest.json
  tools/uv/0.11.29/
  tools/ffmpeg/8.1.2/
  .installer-tmp/<job-id>/
```

干净 Windows 不需要预装 Git、uv、Python 或 FFmpeg。安装器直接下载三个固定提交的 GitHub 官方 ZIP，并校验目录内固定 SHA-256；uv 0.11.29 和 GPT-SoVITS 所需的 FFmpeg 8.1.2 essentials 也由应用以固定 URL、版本和 SHA-256 托管。uv 再按精确 Python 补丁版本创建应用管理的解释器环境。源码、工具和环境都先写入任务专属临时目录，校验成功后再原子改名；失败或取消会清理任务临时目录，不会发布半成品。

安装前必须分别确认引擎代码许可证、Python Software Foundation License，以及该引擎实际需要的 uv/FFmpeg 许可证。`acceptLicense`、`acceptPythonLicense` 和 `acceptedToolLicenses` 缺一不可；服务端不会替用户补全或推定接受。模型同样只在用户之后明确选择并单独接受模型许可证时下载，不会随引擎安装自动获取。

已有完成目录只检测，不覆盖；若仅托管工具缺失或损坏，设置请求只修复工具，不重写已识别的引擎目录。无法识别的不完整目录返回冲突。目录必须是绝对路径且不能是磁盘根目录，所有派生路径都会再次验证仍位于安装根目录内。GPT-SoVITS 工作进程启动时会把已校验的托管 FFmpeg `bin` 目录加入该子进程 PATH，不依赖系统 PATH。

安装 API：

- `GET /api/installer/catalog`
- `GET /api/installer/tools?installRoot=...`
- `POST /api/installer/tools/{tool-id}/repair`
- `GET /api/installer/status?installRoot=...`
- `GET /api/installer/jobs`
- `GET /api/installer/events`：SSE 进度
- `GET /api/installations?installRoot=...`
- `POST /api/installations/{engine}/setup`
- `POST /api/installations/{engine}/models`
- `GET /api/downloads`、`GET /api/downloads/{id}`
- `POST /api/downloads/{id}/pause|resume|cancel|retry`

源码及依赖安装支持取消和失败重试；模型下载额外支持暂停/继续。暂停或取消会终止受管子进程并清理该任务临时目录；继续会重新发出相同固定 revision 的下载请求，底层提供方缓存可复用已校验块。所有子进程均使用固定参数数组和 `shell=False`。

源码采用固定 Git commit archive + SHA-256 双重钉住，并在解压后保存逐文件内容清单；不调用系统 Git。Hugging Face 快照没有官方单一压缩包 SHA-256，安装器以固定 revision 下载，完成后计算“相对路径 + 每文件 SHA-256”的总内容哈希并保存到 `model-manifest.json`。模型下载 CLI 也固定为 `huggingface-hub 1.24.0`。目录大小是磁盘预检用保守估计，不是上游承诺值。

安装请求示例：

```json
{
  "installRoot": "F:/AI/peyin/langbai-managed",
  "acceptLicense": true,
  "acceptPythonLicense": true,
  "acceptedToolLicenses": ["uv", "ffmpeg"],
  "device": "CU128"
}
```

模型请求示例：

```json
{
  "installRoot": "F:/AI/peyin/langbai-managed",
  "modelId": "voxcpm2-official",
  "acceptLicense": true
}
```

安装任务清单包含 `source_url`、`revision`、代码/模型许可证、接受时间、阶段检查点、进度、下载字节、输出路径、预期与计算哈希、日志尾部及明确错误。失败任务不会创建完成目录，也不会被标为成功。

### 托管安装与推理适配器衔接

推理适配器按以下优先级解析环境：

1. `LANGBAI_INDEXTTS2_*`、`LANGBAI_VOXCPM_*`、`LANGBAI_GPT_SOVITS_*` 显式覆盖；
2. `LANGBAI_INSTALL_ROOT/installations/<engine>/{source,env}` 托管安装；
3. 原有 `F:/AI/peyin/...` 本机目录回退。

适配器会在状态查询和新任务开始前重新检测托管目录，因此软件运行期间刚完成的安装无需重启后端。IndexTTS2 自动把独立模型目录传为 `model_dir`；VoxCPM2 自动传为 `model_path` 并启用仅本地文件；GPT-SoVITS 自动映射托管源码的 `tts_infer.yaml` 以及官方模型中的 BERT/CNHuBERT 目录。GPT-SoVITS 官方快照同时包含多个模型版本，无法无歧义判断用户想使用哪一对音色权重，因此托管状态会返回 `configuration_required`，并要求明确提供 `t2s_weights_path` 与 `vits_weights_path`，不会擅自选择。

独立后端 EXE/NSIS 打包时可设置 `LANGBAI_BACKEND_ROOT=<resources/backend>`，子进程会优先从该目录启动 `engine_worker.py`。
