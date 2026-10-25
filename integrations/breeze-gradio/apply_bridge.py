"""SHA-bound, backup-first optional patch for the user's existing Gradio UI."""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SHA256 = "48b665760cdf3332e0eca6be9cc87298321973e82cf9b77305fa04af3dded64e"
MODULE_NAME = "studio_gradio_bridge.py"
HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"预期内容不唯一或已经改变：{old[:80]!r}")
    return source.replace(old, new, 1)


def patch_source(raw: bytes) -> bytes:
    if sha(raw) != EXPECTED_SHA256:
        raise ValueError("local_webui.py SHA256 与审核基线不一致，拒绝自动套用；请先重新审查差异")
    source = raw.decode("utf-8").replace("\r\n", "\n")
    source = once(source, "from sample_presets import PRESETS, SAMPLE_TEXT, preset_by_name\n",
                  "from sample_presets import PRESETS, SAMPLE_TEXT, preset_by_name\n"
                  "from studio_gradio_bridge import attach_studio_tab, browser_preflight_js, guard_free_vram\n")
    source = once(source, '            self.device = "cuda:0"\n',
                  '            guard_free_vram(torch, self.performance_mode)\n            self.device = "cuda:0"\n')
    source = once(source, '            concurrency_limit=1, concurrency_id="breeze-gpu",\n        )\n        preset.change',
                  '            concurrency_limit=1, concurrency_id="breeze-gpu", js=browser_preflight_js(),\n        )\n        preset.change')
    source = once(source, '            [output, download, metrics, model_status],\n            concurrency_limit=1, concurrency_id="breeze-gpu",',
                  '            [output, download, metrics, model_status],\n            concurrency_limit=1, concurrency_id="breeze-gpu", js=browser_preflight_js(),')
    source = once(source, '        model_status = gr.Markdown("模型尚未加载。点击生成时会在本机加载，首次需要稍等。")\n',
                  '        model_status = gr.Markdown("模型尚未加载。点击生成时会在本机加载，首次需要稍等。")\n'
                  '        gr.HTML(\'<div id="langbai-gpu-guard" role="alert" style="display:none;padding:12px;border:1px solid #fbbf24;background:#fffbeb;color:#92400e;border-radius:8px"></div>\')\n')
    start = source.index('        gr.Markdown(f"# {page_title}')
    end = source.index('    demo.queue(default_concurrency_limit=1, max_size=8)', start)
    body = source[start:end]
    source = source[:start] + '        with gr.Tab("短句试听", id="breeze-short"):\n' + ''.join(
        '    ' + line if line.strip() else line for line in body.splitlines(keepends=True)
    ) + '        attach_studio_tab(gr, engine, model_status)\n' + source[end:]
    # Existing handler names and arity remain stable for older benchmark clients.
    tree = ast.parse(source)
    generation = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "generate_audio"]
    if len(generation) != 1 or len(generation[0].args.args) != 12:
        raise ValueError("生成回调签名发生非预期变化")
    return source.encode("utf-8")


def atomic_write(path: Path, data: bytes):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(target: Path, *, dry_run=True):
    target = target.resolve()
    if target.name != "local_webui.py" or not target.is_file():
        raise ValueError("目标必须是已有 local_webui.py 文件")
    original = target.read_bytes()
    patched = patch_source(original)
    bridge = (HERE / MODULE_NAME).read_bytes()
    destination = target.parent / MODULE_NAME
    if destination.exists() and destination.read_bytes() != bridge:
        raise ValueError("目标已存在另一份桥接模块，拒绝覆盖")
    result = {"target": str(target), "originalSha256": sha(original), "patchedSha256": sha(patched),
              "bridgeSha256": sha(bridge), "dryRun": dry_run}
    if dry_run:
        return result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = target.with_name(f"{target.name}.studio-backup-{stamp}")
    with backup.open("xb") as output:
        output.write(original)
        output.flush()
        os.fsync(output.fileno())
    if sha(backup.read_bytes()) != EXPECTED_SHA256:
        raise RuntimeError("备份 hash 验证失败，原文件未修改")
    atomic_write(destination, bridge)  # Module first: an interrupted UI write remains importable.
    if target.read_bytes() != original:
        raise RuntimeError("备份期间目标被修改，拒绝覆盖")
    atomic_write(target, patched)
    result.update(backup=str(backup), applied=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="默认只检查；此开关才备份并写入")
    parser.add_argument("--write-diff", type=Path, help="只写审阅用 diff，不写目标")
    args = parser.parse_args()
    if args.write_diff:
        raw = args.target.read_bytes()
        patched = patch_source(raw)
        diff = difflib.unified_diff(raw.decode("utf-8").replace("\r\n", "\n").splitlines(True), patched.decode("utf-8").splitlines(True),
                                    fromfile="a/local_webui.py", tofile="b/local_webui.py")
        args.write_diff.write_text("".join(diff), encoding="utf-8")
    print(json.dumps(apply(args.target, dry_run=not args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
