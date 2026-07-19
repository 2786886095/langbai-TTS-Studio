# 安装器与模型管理验收计划

目标：让首次使用者在 Windows 软件内完成三套引擎的源码获取、独立 Python 环境安装与模型选择下载，同时避免把安装器变成任意命令执行器或破坏已有目录的工具。

## 1. 强制目录边界

建议的受管布局：

```text
<install_root>/
  sources/<engine_id>/<source_revision>/
  environments/<engine_id>/
  models/<engine_id>/<model_id>/<model_revision>/
  manifests/<installation_id>.json
  downloads/*.part
```

验收要求：

- [ ] 源码、Python 环境、模型、安装 manifest 物理分离；更新源码不得删除模型。
- [ ] 路径由可信根目录与白名单 ID 组合，拒绝绝对子路径、`..`、驱动器切换、UNC 和符号链接逃逸。
- [ ] 解压 ZIP/TAR 前逐项校验目标路径，拒绝 Zip Slip、绝对成员和链接成员。
- [ ] 目标目录已存在且非空时默认拒绝，不覆盖、不删除、不自动改名；只有带受管 manifest 的同一安装允许续作。
- [ ] 下载先写同盘 `.part`，校验长度与 SHA-256 后原子改名。

## 2. 来源与许可证

- [ ] 源码只允许三个内置官方仓库，不接受前端提交的任意 Git URL：
  - `https://github.com/index-tts/index-tts.git`
  - `https://github.com/OpenBMB/VoxCPM.git`
  - `https://github.com/RVC-Boss/GPT-SoVITS.git`
- [ ] 重定向后的最终 URL 仍需落在白名单；禁止 `file://`、UNC、环回地址、内网地址和非 HTTPS（本地测试 fixture 仅通过依赖注入启用）。
- [ ] 源码 revision 必须解析为固定 commit；不能只记录会漂移的 `main`。
- [ ] 模型只能来自内置 catalog 的官方 Hugging Face/ModelScope 仓库与已声明文件，用户输入 URL 不得直通下载器。
- [ ] manifest 记录源码 URL/commit、模型 provider/repo/revision/files/SHA-256、代码许可证、模型许可证、许可证链接与用户确认时间。
- [ ] 代码许可证与模型许可证分别显示；未知或缺失许可证时阻止一键安装，而不是猜测许可证。

## 3. 执行安全

- [ ] 所有外部进程都使用参数数组且 `shell=False`；代码库不得出现 `shell=True`、字符串拼接命令或 `cmd /c`/`powershell -Command` 包装用户输入。
- [ ] Git、Python 与包管理器路径来自受信任探测结果，不从请求体接收任意可执行文件。
- [ ] 安装环境按引擎分离；不把三套依赖装进 Studio 后端环境。
- [ ] 环境安装使用锁定/受控的 requirements 来源，并将解释器、pip/uv 版本和命令参数写入日志。
- [ ] 日志对本机 token、代理认证、URL credentials 和环境变量密钥脱敏。

## 4. 状态、磁盘与恢复

- [ ] 安装阶段至少为 `source`、`environment`、`models`、`verify`；每阶段有状态、进度、错误、开始/结束时间和可恢复检查点。
- [ ] 总状态至少包括 `queued`、`running`、`completed`、`failed`、`cancelling`、`cancelled`；取消不记为失败。
- [ ] 开始写入前计算源码、环境、所选模型、下载临时副本与安全余量；可用磁盘不足时返回可读错误且不创建目标目录。
- [ ] 下载过程中持续检查磁盘；空间耗尽时保留合法 `.part` 与断点信息，不留下伪完成文件。
- [ ] 取消会终止当前受管子进程/HTTP 请求并等待收尾，不遗留 Git、Python、pip/uv 子进程。
- [ ] 失败或重启后只续作未完成阶段；已通过 commit/checksum/环境探针验证的阶段不重复执行。
- [ ] 恢复前重新验证已有产物；文件损坏时只重做对应阶段，不信任 manifest 的单方面“完成”。

## 5. API 最小契约

最终路径可调整，但必须提供等价能力：

- `GET /api/installer/catalog`：三引擎、官方源码、可选模型、大小、SHA-256、代码/模型许可证与安装要求。
- `POST /api/installations`：创建安装，输入只能是 engine/model ID、受管根目录和明确安装选项。
- `GET /api/installations/{id}`：返回持久化 manifest 和分阶段进度。
- `POST /api/installations/{id}/cancel`：请求取消。
- `POST /api/installations/{id}/retry`：从可信检查点恢复。

请求不能携带任意 `source_url`、`model_url`、`executable` 或命令字符串。若为高级用户提供镜像，镜像也应来自本机可信设置，不由普通安装请求临时指定。

## 6. 无网络自动化策略

自动验收不得下载真实仓库或模型：

- 用 `tmp_path` 创建只含小文本文件的本地 Git 仓库；
- 用绑定 `127.0.0.1` 随机端口的 HTTP fixture 提供几 KB 假模型、断点响应和恶意归档；
- 通过 installer app factory 注入测试 catalog、HTTP transport、磁盘查询、进程 runner 与安装根目录；
- 测试模式不得通过生产 API 或环境变量在正式构建中启用；
- 测试结束检查没有访问 fixture 之外的网络地址。

## 7. P0 自动化用例

- [ ] catalog 恰好包含三个引擎且每项许可证/来源元数据完整。
- [ ] 任意 URL、未知 engine/model ID、`../`、绝对子路径、UNC 被拒绝。
- [ ] 恶意归档无法在安装根外创建文件。
- [ ] 非空已有目录内容与哈希在失败后保持不变。
- [ ] 模拟磁盘不足时请求失败，根目录没有新增安装内容。
- [ ] 源码成功、环境失败后 retry 不重新克隆源码。
- [ ] 模型下载中取消后为 `cancelled`，`.part` 可恢复，最终文件不存在。
- [ ] Range 恢复后校验通过并原子完成；服务端不支持 Range 时安全重下。
- [ ] SHA-256 不匹配时模型不进入完成目录。
- [ ] 更新/重装源码后已有模型目录和哈希不变。
- [ ] 静态 AST 扫描确认无 `subprocess(..., shell=True)`。
- [ ] 安装完成后以独立解释器运行轻量 `--help`/import 探针，不加载大模型。

## 8. 最终人工验收

- [ ] 首次启动能区分“已检测到本地安装”“可续作安装”“全新安装”。
- [ ] 模型选择显示预计下载量、安装后磁盘量、许可证、来源、能力与版本限制。
- [ ] 安装前明确展示目标源码/环境/模型目录，不用模糊的单一路径。
- [ ] 进度显示当前阶段、文件、速度、已用/预计时间；取消和重试入口状态正确。
- [ ] 磁盘不足、代理/证书、校验失败、环境冲突和许可证缺失均有可执行中文说明。
- [ ] 打包态 Electron 实窗完成小 fixture 安装演示并截图；不能只以浏览器开发模式代替。
