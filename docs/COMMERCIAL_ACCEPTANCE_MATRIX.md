# langbai-TTS-Studio 1.0.0 商业验收矩阵

审计日期：2026-07-19。结论基于源码门禁、真实 Electron 截图、最终 Windows 包、三套本地引擎和托管安装器证据，不把“代码存在”当作“功能通过”。

## 当前结论

1.0.0 已达到公开测试版的功能与运行门槛。正式对外发布前仍需完成最终 GitHub Release、用已发布 feed 复核应用内更新，以及在无开发环境的独立 Windows 10/11 机器上完成安装/卸载与 SmartScreen 记录。仓库未配置商业代码签名证书，因此不能宣称为已签名发行版。

## 验收结果

| 领域 | 当前证据 | 状态 |
|---|---|---|
| 字号与点击目标 | 1920×1080、1180×720、150% zoom 的真实 Electron 计算样式；可见文字≥12px，主要动作≥15px，热区≥40px | 通过 |
| 导航与反馈 | 创作台、任务、音频库、历史、设置均有独立加载/空/错误/数据状态；网络错误转为中文建议 | 通过 |
| 项目闭环 | 新建、保存、打开、更新、复制、删除、搜索、分页和重启持久化；恢复正文、引擎、完整参数和长音频配置 | 通过 |
| 音频闭环 | 后端仅返回仍可读输出；播放器、另存副本、定位文件、复制路径均接真实 IPC/API | 自动层通过；独立机器声卡体验待复核 |
| 任务状态 | 排队、运行、完成、失败、取消、重试和重启恢复；cancelled 不混入 failed | 通过 |
| 本地已有程序 | 快速扫描、持久化绑定、启动时动态加载；三个组件与侧栏使用同一状态源，不移动或覆盖文件 | 通过 |
| 软件内安装 | 固定提交 ZIP、SHA-256、安全解压、原子替换、失败清理；托管 uv/CPython/FFmpeg；模型与源码分开下载 | 自动层和 uv/Vox 实网 smoke 通过 |
| 许可证 | 项目、CPython、uv/FFmpeg、模型分别明确确认；20px 可见勾选框保留 40px 点击热区 | 通过 |
| 参数完整性 | 三引擎基线与前后端 schema 对齐；每个参数含中文用途与调试建议 | 通过 |
| 真实三引擎 | IndexTTS2、VoxCPM2、GPT-SoVITS 均用本地模型生成有效非静音 WAV | 通过 |
| 真实长音频 | 每个引擎至少 3 个真实片段，逐段完成后统一为 32kHz 并合并；证据含时长与 SHA-256 | 通过 |
| 性能 | 防重入轮询；创作台空闲20s、活动3s、后台60s；队列空闲12s，音频库/历史30s；导航双 RAF 约3–10ms | 通过 |
| 诊断与隐私 | 诊断包会脱敏 Authorization、token、API key、password 和 secret；不做遥测 | 通过 |
| 更新 | GitHub stable/beta 通道、检查、下载进度、重启安装状态机和 electron-builder 元数据 | 契约通过；发布 feed 待首个 Release 复核 |
| Windows 打包 | 1.0.0 NSIS、win-unpacked、blockmap、latest.yml；正常关窗后完整进程树退出 | 通过 |
| 代码签名 | 未配置受信任证书 | 未完成，不得误称已签名 |

## 发布门禁命令

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
backend\.venv\Scripts\python.exe -m pytest tests\acceptance -q --require-implementation --prepackage
backend\.venv\Scripts\python.exe -m pytest tests\acceptance\test_live_engines.py -q --require-implementation --live-engines
npm run build
backend\.venv\Scripts\python.exe -m pytest tests\acceptance\test_packaged_cleanup.py -q --packaged-evidence --packaged-exe="dist\win-unpacked\langbai-TTS-Studio.exe"
```

真实引擎证据写入被 Git 忽略的 `runtime-smoke/`，避免把用户参考音频或本地路径发布到公开仓库。视觉证据位于 `docs/audit/`。
