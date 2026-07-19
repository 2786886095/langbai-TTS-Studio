# langbai TTS Studio 本地 API

基础地址为 `http://127.0.0.1:18765`。接口只服务本机桌面应用。FastAPI 同时提供 `/docs` 与 `/openapi.json`。

## 持久化与错误约定

- 项目记录和全局设置均带 `schemaVersion`，当前版本为 `1`。`GET /api/storage/schema` 返回可读取版本。
- v0 无版本记录会在首次读取时迁移并原子重写；高于当前版本的记录返回 `409`，不会覆盖原文件。
- 项目和设置以同目录临时文件、`fsync`、`os.replace` 原子提交，不会暴露半写入 JSON。
- `400` 表示 ID、时间或路径等输入无效；`404` 表示资源不存在；`409` 表示版本冲突、输出未生成或记录不安全；`410` 表示任务清单仍在但输出文件已丢失；`422` 表示请求结构不符合 OpenAPI 模型。

## 项目

### `GET /api/projects`

查询参数：`query`、`engine`、`offset`（默认 0）、`limit`（默认 50，最大 200）。返回：

```json
{"items": [], "total": 0, "offset": 0, "limit": 50}
```

### `POST /api/projects`

```json
{
  "name": "有声书第一章",
  "description": "正式版本",
  "engine": "indextts2",
  "text": "待合成正文",
  "params": {"speaker_audio": "F:/voices/a.wav"},
  "longAudio": {"maxChars": 180, "silenceMs": 250}
}
```

返回的项目字段为 `schemaVersion`、`id`、`name`、`description`、`engine`、`text`、`params`、`longAudio`、`sourceProjectId`、`createdAt`、`updatedAt`。

### 单项目操作

- `GET /api/projects/{id}`：读取。
- `PUT /api/projects/{id}`：部分保存，只修改请求中出现的字段。
- `POST /api/projects/{id}/copy`：请求体可为 `{"name":"副本名"}`；返回新 ID，并设置 `sourceProjectId`。
- `DELETE /api/projects/{id}`：成功返回 `204`。

## 全局设置

### `GET /api/settings`

返回 `schemaVersion`、`revision`、`theme`、`language`、`defaultEngine`、`outputDirectory`、`autoRevealOutput`、`updateChannel`、`updatedAt`。

### `PATCH /api/settings`

```json
{
  "expectedRevision": 3,
  "theme": "dark",
  "defaultEngine": "voxcpm",
  "outputDirectory": "F:/TTS-Exports",
  "autoRevealOutput": true,
  "updateChannel": "stable"
}
```

客户端应发送上次读取的 `expectedRevision`。其他窗口已修改设置时返回 `409`，客户端应重新读取后再让用户决定是否覆盖。`outputDirectory` 只接受非磁盘根目录的绝对本地路径；传 `null` 可清除它。

## 历史与音频库

### `GET /api/history`

查询参数：`query`、`engine`、`status`、`createdAfter`、`createdBefore`、`hasOutput`、`offset`、`limit`。时间使用 ISO 8601。返回所有符合条件的任务。

### `GET /api/library/audio`

查询参数：`query`、`engine`、`createdAfter`、`createdBefore`、`offset`、`limit`。只返回仍有可读 WAV 输出的任务。

两者均使用分页结构。每个 item 额外包含真实 `output`：

```json
{
  "state": "available",
  "exists": true,
  "path": "F:/.../output.wav",
  "filename": "output.wav",
  "sizeBytes": 123456,
  "durationSeconds": 12.5,
  "sampleRate": 44100,
  "channels": 1,
  "frames": 551250,
  "format": "WAV",
  "subtype": "PCM_16"
}
```

`state` 还可能是 `none`、`missing`、`unsafe` 或 `unreadable`，不会把缺失文件伪装为可播放音频。

## 打开或定位任务输出

`GET /api/jobs/{id}/output` 只解析属于该任务目录的 WAV 文件，阻止被篡改清单指向任意本地文件。成功返回音频元数据以及：

```json
{
  "openContract": {
    "executor": "electron",
    "open": {"method": "shell.openPath", "path": "F:/.../output.wav"},
    "reveal": {"method": "shell.showItemInFolder", "path": "F:/.../output.wav"}
  }
}
```

后端不调用系统 Shell，也不会声称文件已经打开；Electron 主进程必须再次校验 IPC 来源后执行对应动作。

## 诊断导出

- `GET /api/diagnostics/exports`：列出现有诊断包和真实的大小、SHA-256。
- `POST /api/diagnostics/exports`：原子创建 ZIP，返回 `id`、`path`、`sizeBytes`、`sha256`、`createdAt`。
- `GET /api/diagnostics/exports/{id}`：下载 ZIP。

ZIP 内的 `diagnostics.json` 带 `schemaVersion=1`，包含 Python/Windows、引擎状态、安装状态、设置摘要、项目计数和最近 200 条任务摘要；不包含项目正文和任务参数。日志只收集 `data/logs` 下最近 20 个 `.log` 的末尾 256 KiB。

## 更新配置契约

`GET /api/update/config` 返回更新通道、可选 provider/feed URL 以及 Electron 状态字段约定。`handledBy` 恒为 `electron`，`backendPerformsUpdateChecks` 恒为 `false`。后端不会伪造 `updateAvailable`；检查、下载、签名验证和安装都属于 Electron 自动更新层。

## 托管引擎安装

- `GET /api/installer/catalog`：返回三个固定源码提交/归档 SHA-256、精确 Python 版本、许可证、模型 revision 和所需托管工具。
- `GET /api/installer/tools?installRoot=...`：返回 uv/FFmpeg 的固定版本、实际完整性状态、路径、许可证和归档哈希。
- `POST /api/installations/{engine}/setup`：只安装源码、托管工具和独立 Python 环境，不下载模型。
- `POST /api/installer/tools/{toolId}/repair`：重新下载、校验并原子替换损坏的托管工具。
- `POST /api/installations/{engine}/models`：用户之后明确选择模型并接受模型许可证时单独调用。

设置请求必须显式提交三类许可记录，后端不推定接受：

```json
{
  "installRoot": "F:/AI/peyin/langbai-managed",
  "acceptLicense": true,
  "acceptPythonLicense": true,
  "acceptedToolLicenses": ["uv", "ffmpeg"],
  "device": "CU128"
}
```

IndexTTS2/VoxCPM2 的 `acceptedToolLicenses` 只允许 `uv`；GPT-SoVITS 需要 `uv` 和 `ffmpeg`。请求包含无关工具或漏掉任何必需许可都会返回 HTTP 400。失败、取消、重试与任务状态继续通过 `/api/downloads/{id}` 和 `/api/downloads/{id}/{action}` 管理。

## 既有接口

任务、引擎、SSE 和安装器接口保持不变：`/api/jobs`、`/api/engines`、`/api/events`、`/api/installations`、`/api/downloads`、`/api/installer/*`。
