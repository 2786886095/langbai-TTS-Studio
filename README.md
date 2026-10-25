# langbai-TTS-Studio

<p align="center"><img src="assets/icon/langbai-icon.png" width="128" alt="langbai-TTS-Studio 图标"></p>

面向 Windows 的本地配音工作台，管理 IndexTTS2、VoxCPM2、GPT-SoVITS，并接入已安装在 WSL 中的 Breeze TTS 2。桌面和本机网页共用长文任务，可粘贴文稿、自动分段、查看进度、暂停和恢复，最后合并成完整配音。

> 当前版本为 **v1.3.0**。[下载安装包](https://github.com/2786886095/langbai-TTS-Studio/releases/tag/v1.3.0)。应用代码使用 MIT；上游源码、模型权重和素材各有独立条款。Breeze 权重限制研究和非商业用途，适配代码的公开分发边界仍待上游澄清，不能据此推定可商用。

完整使用方法、数据回滚和验收记录见 [长文工作台说明](docs/LONGFORM_V2.md)。

## 已实现功能

- P0 长文核心：保存原文与分段位置、逐段 hash 校验、段后暂停、立即停止、中断恢复、有限重试与拆分、单段重做和重新导出。
- 桌面与本机网页共享任务服务；默认先验收中文长文，输入预算为最多 10 万汉字、50 万总字符，不宣称无限长度。
- Breeze TTS 2 只读绑定已有 WSL 部署，默认 `accelerated`、CFG=1 参考克隆；不自动下载权重，不修改现有模型安装。
- P1 工作台：文稿与分段/输出双栏，小窗口改为标签切换；创作、任务、成品、声音、训练、引擎六项导航，另设设置。
- 任务通过 SSE 接收变化摘要，断线回退轮询；正文、合并、导出和校验分别显示阶段，成品发布后才显示完成。
- WAV / FLAC 输出；检测到受管理 FFmpeg 后开放 MP3。默认保持实际引擎采样率，Breeze 为 24kHz 单声道。
- 保留 IndexTTS2、VoxCPM2、GPT-SoVITS 的原生创作入口和推理参数。
- GPT-SoVITS 多人配音：按 `【角色名】：台词` 识别角色，每个角色独立选择已保存声音和推理参数预设，按顺序生成后合并为一条音频。
- 全量原生推理参数，每项带中文用途、调试影响和建议范围。
- 项目库、任务中心和成品库；角色声音按引擎隔离。新长文任务使用独立 v2 存储，旧任务仍保留旧版重试语义。
- 统一模型训练入口：GPT-SoVITS 官方本地工作台，以及 VoxCPM2 LoRA/全量 SFT 训练、日志与检查点恢复。
- 软件内可视化终端、引擎与训练的显卡占用协调；长文退出时可以段后暂停或后台继续，未确认 worker 退出时显示诊断信息。
- 播放、定位输出文件、另存音频副本和完成后自动显示文件。
- 舒适/紧凑两种界面密度、响应式高 DPI 布局、键盘快捷键，以及覆盖全部可编辑文本框的原生右键编辑菜单。
- 本地诊断包导出，敏感令牌会在导出前脱敏。
- GitHub Releases 更新检查、应用内下载进度和重启安装；稳定版/测试版通道可选。

## 软件内安装引擎

以下安装器适用于 IndexTTS2、VoxCPM2、GPT-SoVITS；**不包含 Breeze 自动安装**。进入“引擎 → 安装与绑定”后：

1. 选择安装根目录与 CPU/CUDA 环境。
2. 分别阅读并接受项目源码、CPython 和安装工具许可证。
3. 软件从固定官方提交下载源码 ZIP，校验 SHA-256，安装固定版本工具并创建隔离 Python 环境。
4. 源码与环境完成后，再由用户单独勾选需要的模型权重、阅读模型许可证并下载。

模型权重不会随引擎源码自动下载。下载和安装任务具有真实进度、日志、取消、重试与失败清理；非完整目录不会被伪装成成功安装。

默认托管内容：

- IndexTTS2 / VoxCPM2：托管 uv 与 CPython。
- GPT-SoVITS：托管 uv、CPython 与 FFmpeg。
- 以上三个引擎：固定官方源码提交及 SHA-256；模型使用固定 Hugging Face revision 和内容清单。

详细来源、固定版本和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 使用现有本地引擎

IndexTTS2、VoxCPM2 和 GPT-SoVITS 可以在引擎页快速扫描、绑定已有源码、Python 环境和模型，直接使用原目录，不移动或重复下载文件。Breeze 使用单独的 WSL 绑定表单，填写 Windows 源码与模型目录、发行版和 Linux Python 路径，并确认模型许可。

Breeze 首版只开放已经验证的参考声音克隆：`accelerated`、CFG=1、准确参考文字、声音描述留空。原生 Windows、其他 CFG、Breeze 情绪/声音设计、训练和多人配音尚未作为可用能力开放。现有 GPT-SoVITS 多人模式继续保留。

软件内安装默认写入用户选择的安装根目录，不会覆盖已存在的非完整目录。

## 应用内更新

“设置与路径 → 桌面偏好 → 软件更新”会读取 GitHub Releases：

- 手动检查稳定版或测试版。
- 有新版本时在应用内下载并显示进度。
- 下载完成后由用户点击“重启安装”。

首次正式 Release 发布前，更新检查会明确显示尚无可用发布，不会伪报“已是最新版本”。发布包由 `electron-updater` 使用 `latest.yml` 和 blockmap 完成差分更新。

## 开发

要求：Windows 10/11、Node.js 20+、Python 3.11/3.12 和 uv。这里的 Python/uv 只用于开发与构建；最终用户运行桌面应用无需预装它们。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\Start-Langbai-TTS-Studio.cmd
```

源码依赖准备后，默认启动入口使用已构建前端和本地 Electron；前端产物缺失时先构建，不启动 Vite。只用网页时运行 `Start-Longform-Web.cmd`。

两个入口默认共用 `http://127.0.0.1:18765/longform` 和当前用户“文档”中的 `langbai-TTS-Studio/data`。它们只复用协议、schema 和数据目录匹配的服务，不兼容的端口占用会明确拒绝。后台启动日志在数据目录的 `logs/`。

需要前端热更新时才运行 `scripts/start-dev.ps1`，Vite 为 `127.0.0.1:5173`，任务服务为 `127.0.0.1:18765`。它不能和另一套使用相同数据目录的后端同时运行。

## 数据与升级

首次运行对旧任务、项目和声音做逐文件校验备份，新长文数据写入独立 `data/v2/`。旧版无法显示 Breeze、暂停/中断状态和其他 v2 扩展；回滚存在功能与可见性损失，不提供双向同步。不要在两个版本间共享正在使用的数据目录，也不要直接删除 v2 数据。详见 [备份与回滚说明](docs/LONGFORM_V2.md#数据备份和回滚)。

## 验收与打包

2026-10-25 本地冻结源码的后端 151 项测试通过，非 live acceptance 44 项通过、27 项跳过；发布准备时修正迁移后的静态验收检查，CI 等价严格验收为 59 项通过、12 项跳过，跳过不计为通过。真实 Breeze 万字生成与恢复验收完成：点击至成品发布 31 分 09.383 秒（含主动暂停和两次冷启动），产出 40 分 33.09 秒音频。正式版使用已实测的未签名 NSIS 安装包，该包另完成真实 Breeze 短句测试；详细口径、限制和文件 hash 见 [验收记录](docs/LONGFORM_V2.md#本轮验收记录)。

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
backend\.venv\Scripts\python.exe -m pytest tests\acceptance -q --require-implementation --prepackage
npm --prefix frontend run build
npm run build
backend\.venv\Scripts\python.exe -m pytest tests\acceptance\test_packaged_cleanup.py -q --packaged-evidence --packaged-exe="dist\win-unpacked\langbai-TTS-Studio.exe"
```

真实三引擎验收为发布前的本机 GPU 阶段，需要已授权的参考音频和本地模型路径：

```powershell
backend\.venv\Scripts\python.exe -m pytest tests\acceptance\test_live_engines.py -q --require-implementation --live-engines
```

`npm run build` 会将 FastAPI 后端编译为独立 Windows EXE，再生成 NSIS 安装包、更新元数据和未打包验证目录。

## 项目结构

- `frontend/`：React + TypeScript 桌面界面。
- `electron/`：安全 IPC、窗口、更新、文件操作和后端生命周期。
- `backend/`：FastAPI、任务队列、长音频流水线、三引擎适配器与安装器。
- `backend/app/longform/`：v2 长文存储、分段、校验、恢复、导出和事件 API。
- `backend/breeze_worker.py`：读取已有 WSL 安装的 Breeze 加速 worker；不附带源码安装或模型权重。
- `tests/acceptance/`：参数基线、API、安装安全、UI、打包进程和真实引擎验收。
- `assets/icon/`：应用图标源图与多尺寸图标。
- `docs/`：架构、商业验收和视觉审查证据。

## 许可证与素材

应用代码采用 MIT，详见 [LICENSE](LICENSE)。上游项目、工具和模型条款见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

仓库和安装包不带 Breeze 权重、游戏角色原音或参考声音授权。Breeze 模型输出的商业用途需要上游另行书面许可；本机免费运行或应用开源不会自动豁免模型条款。参考音频的来源和必要同意由使用者确认。

`assets/icon/` 中的布偶照片及其衍生图标由项目维护者提供并声明可用于本项目。其原始权属未由仓库独立验证，因此该图像素材不自动包含在应用代码的 MIT 许可范围内。

安全问题请阅读 [SECURITY.md](SECURITY.md)，贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。
