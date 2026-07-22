# langbai-TTS-Studio

<p align="center"><img src="assets/icon/langbai-icon.png" width="128" alt="langbai-TTS-Studio 图标"></p>

面向 Windows 的本地语音生成工作台。它在一个纯色桌面界面中统一管理 IndexTTS2、VoxCPM2 和 GPT-SoVITS；每个生成任务只选择一个引擎，并保留该引擎的原生推理参数及中文用途、调试说明。

> 当前仓库仍是发布前开发版。应用代码使用 MIT 许可证；三个上游项目、模型权重和图标素材各自遵循独立条款，不会自动转为 MIT。

![创作台](docs/audit/commercial-current/01-studio-1920x1080.png)

## 已实现功能

- 三引擎统一任务队列：IndexTTS2、VoxCPM2、GPT-SoVITS。
- 全量原生推理参数，每项带中文用途、调试影响和建议范围。
- 长文本分段、失败重试、断点恢复、分段保留、统一采样率及 WAV 合并。
- 项目库、任务中心、历史记录和音频库；项目保存后可完整恢复正文、引擎、参数及长音频配置。
- 统一模型训练入口：GPT-SoVITS 官方本地工作台，以及 VoxCPM2 LoRA/全量 SFT 训练、日志与检查点恢复。
- 三个引擎各自独立的软件内可视化终端；只有存在活动引擎、生成或训练任务时，退出才询问是否终止。
- 播放、定位输出文件、另存音频副本和完成后自动显示文件。
- 舒适/紧凑两种界面密度、响应式高 DPI 布局和键盘快捷键。
- 本地诊断包导出，敏感令牌会在导出前脱敏。
- GitHub Releases 更新检查、应用内下载进度和重启安装；稳定版/测试版通道可选。

## 软件内安装引擎

新电脑无需预装 Git、Python、uv 或 FFmpeg。进入“设置与路径 → 引擎管理”后：

1. 选择安装根目录与 CPU/CUDA 环境。
2. 分别阅读并接受项目源码、CPython 和安装工具许可证。
3. 软件从固定官方提交下载源码 ZIP，校验 SHA-256，安装固定版本工具并创建隔离 Python 环境。
4. 源码与环境完成后，再由用户单独勾选需要的模型权重、阅读模型许可证并下载。

模型权重不会随引擎源码自动下载。下载和安装任务具有真实进度、日志、取消、重试与失败清理；非完整目录不会被伪装成成功安装。

默认托管内容：

- IndexTTS2 / VoxCPM2：托管 uv 与 CPython。
- GPT-SoVITS：托管 uv、CPython 与 FFmpeg。
- 三个引擎：固定官方源码提交及 SHA-256；模型使用固定 Hugging Face revision 和内容清单。

详细来源、固定版本和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 使用现有本地引擎

进入“设置与路径 → 引擎管理”，点击“快速扫描本地程序”。软件会验证已有源码、Python 环境和模型后保存路径绑定，后续直接使用原目录，不会移动、覆盖或重复下载文件。扫描不到非标准目录时可通过引擎绑定接口或环境变量明确提供源码、Python、运行资源和模型路径。

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
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

开发端口：前端 `127.0.0.1:5173`，后端 `127.0.0.1:18765`。

## 验收与打包

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
- `tests/acceptance/`：参数基线、API、安装安全、UI、打包进程和真实引擎验收。
- `assets/icon/`：应用图标源图与多尺寸图标。
- `docs/`：架构、商业验收和视觉审查证据。

## 许可证与素材

应用代码采用 MIT，详见 [LICENSE](LICENSE)。上游项目、工具和模型条款见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

`assets/icon/` 中的布偶照片及其衍生图标由项目维护者提供并声明可用于本项目。其原始权属未由仓库独立验证，因此该图像素材不自动包含在应用代码的 MIT 许可范围内。

安全问题请阅读 [SECURITY.md](SECURITY.md)，贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。
