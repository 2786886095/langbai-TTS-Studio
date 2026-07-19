# Third-party notices

langbai-TTS-Studio 的应用代码使用 MIT 许可证。下载器只负责帮助用户从上游官方来源安装组件；上游源码、模型、数据和生成内容不因此变为 MIT。

| 组件 | 官方项目 | 源码许可证 | 模型许可证/说明 |
| --- | --- | --- | --- |
| IndexTTS | [index-tts/index-tts](https://github.com/index-tts/index-tts) | bilibili Model Use License Agreement（覆盖模型与最终代码） | IndexTTS 2 权重受同一协议约束，下载前必须单独接受 |
| VoxCPM | [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) | Apache-2.0 | 官方 README 声明代码与权重均为 Apache-2.0 |
| GPT-SoVITS | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | MIT | 官方预训练模型仓库标注 MIT；其他第三方模型和依赖可能有独立许可证 |
| Astral uv 0.11.29 | [astral-sh/uv](https://github.com/astral-sh/uv/releases/tag/0.11.29) | Apache-2.0 OR MIT | 应用按用户确认下载固定 Windows x64 归档并校验上游 SHA-256 |
| CPython 3.11.13 / 3.10.18 | [Python license](https://docs.python.org/3/license.html) | Python Software Foundation License Version 2 | 由托管 uv 创建引擎独立环境；安装前必须单独确认 |
| FFmpeg 8.1.2 essentials | [FFmpeg Windows download](https://ffmpeg.org/download.html#build-windows) / [gyan.dev build](https://www.gyan.dev/ffmpeg/builds/#release-builds) | GPLv3（gyan.dev 静态构建） | 仅 GPT-SoVITS 需要；固定版本归档并校验发布者 SHA-256 |

## 托管供应链固定值

- IndexTTS2 源码：提交 `13495845e3028f0bb6ca1462ad22aa0e76349e40`，归档 SHA-256 `7ed8bc742e2eeeb83f922247ef0e27f96327f418acacb6c63f182cafd66887ba`。
- VoxCPM2 源码：提交 `616d3d3e630a9c96c2853250eef91b0f39dcd5fa`，归档 SHA-256 `131acb3c4741e63bcc33cfa5499f3ccaa3eb58bc00d352721a656a9ca12e448f`。
- GPT-SoVITS 源码：提交 `be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2`，归档 SHA-256 `d16ddb222ef573d122a7ce16816bfe9c7536dc51acaae59422f8835617e47026`。
- uv Windows x64 0.11.29：SHA-256 `a047d55651bc3e0ca24595b25ec4cfcb10f9dca9fb56514e661269b37d4fae68`。
- gyan.dev FFmpeg 8.1.2 essentials ZIP：SHA-256 `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`。

以上值在 2026-07-19 从对应上游提交/发布页和校验文件复核。安装器不使用 `latest` URL，不接受前端覆盖 URL 或哈希，也不会代替用户接受第三方许可证。

模型管理器必须展示实际下载源、目标路径、预计体积、许可证链接和校验信息。用户主动确认后才会开始下载。

## Application icon

`assets/icon/` 中的布偶照片及其衍生图标由项目维护者提供并声明可用于本项目。除非素材权利人另行授权，该图像素材不自动包含在应用代码的 MIT 许可范围内。
