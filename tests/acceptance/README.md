# Acceptance tests

该目录属于独立验收框架，不实现产品功能。

```powershell
python -m pip install -r tests/acceptance/requirements.txt
python -m pytest tests/acceptance -q
python -m pytest tests/acceptance -q --require-implementation
python -m pytest tests/acceptance -q --require-implementation --live-engines
python -m pytest tests/acceptance -q --require-implementation --prepackage
python -m pytest tests/acceptance/test_live_engines.py -q --require-implementation --live-engines
python -m pytest tests/acceptance/test_packaged_cleanup.py -q --packaged-evidence --packaged-exe="D:\path\langbai-TTS-Studio.exe"
```

测试层级：

- 默认模式只校验参数基线文件；实现尚未落地的测试会明确 `SKIP`。
- `--require-implementation` 用于正式 Mock 闭环验收，缺实现即失败。
- `--live-engines` 只在三套模型路径、Python 环境与参考音频都配置好后运行；它可能长时间占用 GPU。
- `--prepackage` 开启严格 UI/API 商业门槛：真实 Electron 字号/目标尺寸/键盘/缩放采集、项目与设置重启持久化、音频库播放器和诊断脱敏。
- `--packaged-evidence` 只运行必须针对本轮 Windows 成品执行的门禁。
- `--packaged-exe` 必须指向本轮构建的 Windows 成品；测试通过正常关闭窗口验证 Electron 与 Python/引擎子进程均退出，并在失败后强制清理本轮进程树。

PR/日常 CI 只运行前两条 Mock/静态命令，不应因为没有数 GB 模型或安装包而失败。tag 发布必须依次运行商业 UI/API、真实三引擎、包内清理三个阶段；后两阶段的 `SKIP` 不得被发布编排器当作成功证据。

真实引擎测试配置通过环境变量传入，严禁把用户本地绝对路径写死进产品配置：

- `LANGBAI_INDEXTTS2_PROJECT`、`LANGBAI_INDEXTTS2_PYTHON`、`LANGBAI_INDEXTTS2_MODEL_DIR`
- `LANGBAI_VOXCPM_PROJECT`、`LANGBAI_VOXCPM_PYTHON`、可选 `LANGBAI_VOXCPM_MODEL_PATH`
- `LANGBAI_GPT_SOVITS_PROJECT`、`LANGBAI_GPT_SOVITS_PYTHON`、`LANGBAI_GPT_SOVITS_RUNTIME_ROOT`
- GPT-SoVITS 自定义权重可再提供 `LANGBAI_GPT_SOVITS_TTS_CONFIG`、`LANGBAI_GPT_SOVITS_T2S_WEIGHTS`、`LANGBAI_GPT_SOVITS_VITS_WEIGHTS`
- `LANGBAI_ACCEPTANCE_REFERENCE_WAV`
- `LANGBAI_ACCEPTANCE_OUTPUT_DIR`
- `LANGBAI_ACCEPTANCE_BINDINGS_FILE`：可选；指向软件生成的 `engine-bindings.json`，用于验证真实持久化绑定可直接启动三引擎

Mock 测试固定设置 `LANGBAI_TTS_MOCK=1`，不得联网或加载模型。
