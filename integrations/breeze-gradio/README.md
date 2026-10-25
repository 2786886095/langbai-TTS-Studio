# Breeze Gradio → Studio 长文本桥

这是可选的本机集成模块与版本绑定补丁。它不包含模型权重或参考音频，不替换 Studio 的任务服务，也不建立第二套长任务队列。

## 行为

- 原页面放入“短句试听”标签页，新增“长文本”标签页，嵌入并链接 `http://127.0.0.1:18765/longform`。
- iframe 和显卡状态检查都由 **Windows 浏览器**请求 Windows loopback；WSL Python 不反向请求 Windows 的 `127.0.0.1`。当前 NAT 模式下无需修改 WSL 网络或防火墙。
- `generate_audio` 仍为 11 输入、4 输出；`load_model` 仍为 0 输入、1 JSON 输出。默认 `accelerated`、CFG 1 和原采样配置不变。
- 新增隐藏 API `/runtime_status` 与 `/unload_model`，均为 0 输入、1 JSON 输出，继续使用 `concurrency_id="breeze-gpu"` 串行队列。状态查询不导入 torch、不加载模型；显式卸载只清理该 Gradio 进程自己的 runtime/model/tokenizer/audio_tokenizer，执行 GC 和已经初始化的 CUDA 缓存清理。
- “长文本”页的“释放短句试听模型”按钮显式执行卸载。它不会停止 Studio worker、其他服务或 WSL。输出 WAV、参考文件与采样配置保留。状态同时报告残留张量分配；清空 Python 引用不等于证明所有进程显存均已归还。
- 浏览器短句生成和模型加载前，GET `/api/gpu/lease` 检查 `{owner,residents:[{id,running,warmed}]}`。已知 Studio 占用或热驻留时阻止这次调用并显示原因；任务服务离线时保留原短句行为。
- 冷加载前检查实际空闲显存：accelerated 预算 9 GiB、eager 预算 8 GiB，不足时明确拒绝加载。这是基于本机已验证模型的保守可用性守卫，不改变采样或音质参数。

浏览器检查属于协作式检查，不能代替跨进程的原子显卡锁。直接使用 Gradio API 的客户端不运行浏览器 JS；它们仍受冷加载显存守卫约束，应自行遵守 Studio 调度。两个独立进程同时通过检查仍可能竞争；不要将此补丁描述成完全消除并发竞争。

## 审核基线

- `local_webui.py` SHA256：`48b665760cdf3332e0eca6be9cc87298321973e82cf9b77305fa04af3dded64e`
- 已核对本机 Gradio：`5.49.1`。
- 审阅文件：[local_webui.py.patch](local_webui.py.patch)
- 侧边模块：[studio_gradio_bridge.py](studio_gradio_bridge.py)
- 应用工具：[apply_bridge.py](apply_bridge.py)

补丁匹配原文件 SHA，并逐项核对预期替换内容；任何不一致都会停止。不要直接使用忽略上下文的补丁工具强行套用。

## 应用与回滚

在 Windows PowerShell 中先做只读检查：

```powershell
& 'C:\Python314\python.exe' 'F:\AI\peyin\langbai-TTS-Studio-src\integrations\breeze-gradio\apply_bridge.py' --target 'F:\AI\Breeze-TTS-2\local_webui.py'
```

应用前先确认 Gradio 空闲，并安排由明确归属的启动器重启页面服务。应用工具本身不停止或启动任何进程：

```powershell
& 'C:\Python314\python.exe' 'F:\AI\peyin\langbai-TTS-Studio-src\integrations\breeze-gradio\apply_bridge.py' --target 'F:\AI\Breeze-TTS-2\local_webui.py' --apply
```

工具先以独占方式保存原始字节备份并校验 hash，安装侧边模块后才原子替换 UI 文件。成功 JSON 包含备份路径及新旧 SHA。若目标已存在不同桥接模块，则拒绝覆盖。默认不带 `--apply` 时只检查。

回滚时先停止这一份 Gradio 服务，校验备份 SHA 等于审核基线，再把成功 JSON 中指定的备份恢复为 `local_webui.py`。旧文件没有桥接 import，侧边模块即使保留也不被使用。不要回滚、覆盖其他 Studio 或 Breeze 文件。

可通过 `LANGBAI_STUDIO_ORIGIN` 指定其他本机端口，值只能是 `http://127.0.0.1:<端口>`；默认不开放 LAN 或公网地址。若 iframe 被宿主策略拒绝，可使用“在新窗口打开”链接；部署验收应验证 Studio 的 `frame-ancestors` 允许 `http://127.0.0.1:7862`，不全局放宽来源。

## 已执行的验证

- 6 项 CPU 单元测试：只读状态、忙锁、显式卸载、显存预算、SHA 拒绝、临时目录备份/应用。
- Node 浏览器预处理器模拟：Studio owner、热驻留、离线降级、11 输入与 0 输入原样传递。
- 在本机 Gradio 5.49.1 环境构建真实 `Blocks` 配置，确认两个标签、原 API 输入输出数量、新隐藏 API 和统一队列；明确断言没有模型加载、没有 CUDA 初始化、没有启动服务。

```powershell
& 'C:\Python314\python.exe' integrations\breeze-gradio\test_bridge.py
node integrations\breeze-gradio\test_guard.cjs
wsl --distribution Ubuntu --exec /home/langbai666/.venvs/breeze-tts-2/bin/python /mnt/f/AI/peyin/langbai-TTS-Studio-src/integrations/breeze-gradio/test_gradio_config.py
```

本轮模块生成与检查没有修改 `F:\AI\Breeze-TTS-2`，没有占用 GPU。真实页面嵌入、浏览器拒绝生成和实际模型释放仍须在主任务安排的部署验收中检查。

Gradio 官方说明：事件监听器的 `js` 在 Python 回调之前执行；本集成依赖该机制，并绑定当前已核对的 Gradio 版本。[Custom CSS and JS](https://www.gradio.app/guides/custom-CSS-and-JS)
