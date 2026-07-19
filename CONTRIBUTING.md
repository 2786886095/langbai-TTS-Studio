# Contributing

感谢参与 langbai-TTS-Studio。

## 开发准备

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

提交前请运行：

```powershell
npm run build:frontend
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
backend\.venv\Scripts\python.exe -m pytest tests\acceptance -q -rs
```

不要在 Pull Request 中提交模型权重、生成音频、Python 虚拟环境、缓存或个人参考音频。新增下载源必须来自上游官方项目，并补充许可证、磁盘空间、取消和失败恢复测试。
