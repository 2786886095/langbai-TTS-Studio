# Third-party notices

langbai-TTS-Studio 的应用代码使用 MIT 许可证。下载器只负责帮助用户从上游官方来源安装组件；上游源码、模型、数据和生成内容不因此变为 MIT。

| 组件 | 官方项目 | 源码许可证 | 模型许可证/说明 |
| --- | --- | --- | --- |
| IndexTTS | [index-tts/index-tts](https://github.com/index-tts/index-tts) | bilibili Model Use License Agreement（覆盖模型与最终代码） | IndexTTS 2 权重受同一协议约束，下载前必须单独接受 |
| VoxCPM | [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) | Apache-2.0 | 官方 README 声明代码与权重均为 Apache-2.0 |
| GPT-SoVITS | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | MIT | 官方预训练模型仓库标注 MIT；其他第三方模型和依赖可能有独立许可证 |
| Breeze TTS 2 | [breezeblue-ai/breeze-tts](https://github.com/breezeblue-ai/breeze-tts) / [BreezeBlue/Breeze-TTS-2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) | 上游源码 Apache-2.0 | 权重适用 BreezeBlue Research and Non-Commercial License Agreement v1.1，限制研究和非商业用途，不是 Apache-2.0 模型许可；首版只读绑定用户已有 WSL 安装 |
| Astral uv 0.11.29 | [astral-sh/uv](https://github.com/astral-sh/uv/releases/tag/0.11.29) | Apache-2.0 OR MIT | 应用按用户确认下载固定 Windows x64 归档并校验上游 SHA-256 |
| CPython 3.11.13 / 3.10.18 | [Python license](https://docs.python.org/3/license.html) | Python Software Foundation License Version 2 | 由托管 uv 创建引擎独立环境；安装前必须单独确认 |
| FFmpeg 8.1.2 essentials | [FFmpeg Windows download](https://ffmpeg.org/download.html#build-windows) / [gyan.dev build](https://www.gyan.dev/ffmpeg/builds/#release-builds) | GPLv3（gyan.dev 静态构建） | GPT-SoVITS 的托管依赖；v2 还在检测到受管理安装后用于 MP3 导出，固定归档并校验发布者 SHA-256 |

## Breeze TTS 2：代码、权重与参考素材分别处理

1.3.0 开发分支的接入目标是用户自行准备的本机 WSL 安装，不附带 Breeze 权重或游戏角色原音，也没有 Breeze 自动下载器。当前绑定的验证基线为：

- 上游源码提交：`6eba901d1845353802652c708a4740fdfc5baa75`。
- 模型 revision：`3e28c5151381a722f1d8661b4118c298caa77aa4`。
- 已验证 KV 缓存修复文件 `models/stream_runtime/stream/kv_cache.py` 的 SHA-256：`9b27b1fefc3d867abf9ac477f03e3a5ecc2c030a06fe4f18a4e56a3c1abe3ac6`。绑定只验证，不向用户已有安装写补丁。
- 默认 `accelerated`、CFG=1 参考声音克隆，主干解码 CUDA Graph 与深度解码器编译开启。该配置不改变任何模型或素材的许可。

上游明确按 Apache-2.0 授权的源码不属于权重许可定义中的 Model Materials。若将来再分发上游源码或 KV 补丁，仍需保留适用的许可证、NOTICE 和修改声明。Studio 自有适配代码的 MIT 声明不能覆盖上游代码、模型或其他素材。

权重许可为 **BreezeBlue Research and Non-Commercial License Agreement v1.1（2026-09-01）**，以固定 revision 中的 [LICENSE](https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/3e28c5151381a722f1d8661b4118c298caa77aa4/LICENSE) 为准：

- 研究和非商业用途之外的使用须取得单独书面许可，包括条款明确涉及的商业音频输出；不存在自动的个人创作者、小企业或收入门槛例外。
- §1.7(b) 列出 API、application、plug-in、website 等功能提供方式。免费且不含权重的本地适配代码公开分发如何适用该条，**尚待上游澄清**；“可选插件”不是自动豁免，也不把编写适配器本身表述成已经取得商业许可。
- 如将来分发 Model Materials，需要遵守其独立分发、完整协议与声明等条件；当前方案不在安装包内分发权重。
- 参考音频、声音身份、来源授权及必要同意分别由使用者核实。记录“同意”字段不替代真实授权，仓库不授予角色原音的再分发或商业使用权。

Breeze TTS 2 仅作准确的描述性提及，不作为本应用主名，也不表示 BreezeBlue 为本项目背书。应用开源、模型许可确认界面和本地运行均不能据此推定所有用途均获许可。

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
