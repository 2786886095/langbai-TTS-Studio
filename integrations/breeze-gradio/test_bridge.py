from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import apply_bridge
import studio_gradio_bridge as bridge


class FakeEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.runtime = self.model = self.tokenizer = self.audio_tokenizer = object()
        self.load_seconds = 42
        self.device_name = "fake"
        self.last_prefill_length = self.last_cfg_scale = self.last_model_loaded_for_request = 1
        self.performance_mode = "accelerated"

    @property
    def loaded(self):
        return self.runtime is not None

    def model_info(self):
        return {"loaded": self.loaded, "mode": self.performance_mode}

    def ensure_loaded(self, *args, **kwargs):
        raise AssertionError("status/unload must not load a model")


class BridgeTests(unittest.TestCase):
    def test_origin_is_loopback_only(self):
        with patch.dict("os.environ", {"LANGBAI_STUDIO_ORIGIN": "http://127.0.0.1:18765"}):
            self.assertEqual(bridge.studio_origin(), "http://127.0.0.1:18765")
        for value in ("https://remote.example", "http://127.0.0.1@remote.example", "http://127.0.0.1:18765/path"):
            with patch.dict("os.environ", {"LANGBAI_STUDIO_ORIGIN": value}):
                with self.assertRaises(ValueError):
                    bridge.studio_origin()

    def test_status_without_torch_does_not_import_it(self):
        engine = FakeEngine()
        with patch.dict(sys.modules):
            sys.modules.pop("torch", None)
            self.assertTrue(bridge.runtime_status(engine)["loaded"])
            self.assertNotIn("torch", sys.modules)

    def test_busy_status_does_not_wait_for_model_lock(self):
        engine = FakeEngine()
        acquired, release = threading.Event(), threading.Event()
        def hold():
            with engine.lock:
                acquired.set()
                release.wait(3)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(acquired.wait(1))
        try:
            self.assertTrue(bridge.runtime_status(engine)["busy"])
        finally:
            release.set()
            thread.join(2)

    def test_explicit_unload_clears_own_refs_without_changing_mode(self):
        calls = []
        cuda = types.SimpleNamespace(is_initialized=lambda: True, synchronize=lambda: calls.append("sync"),
                                     empty_cache=lambda: calls.append("empty"), memory_allocated=lambda _device: 0,
                                     memory_reserved=lambda _device: 0)
        engine = FakeEngine()
        with patch.dict(sys.modules, {"torch": types.SimpleNamespace(cuda=cuda)}):
            result = bridge.unload_runtime(engine)
        self.assertFalse(result["loaded"])
        self.assertTrue(result["modelReferencesCleared"])
        self.assertEqual(calls, ["sync", "empty"])
        self.assertEqual(engine.performance_mode, "accelerated")
        self.assertTrue(all(getattr(engine, field) is None for field in ("runtime", "model", "tokenizer", "audio_tokenizer")))

    def test_vram_guard_only_rejects_insufficient_budget(self):
        def torch(free):
            return types.SimpleNamespace(cuda=types.SimpleNamespace(mem_get_info=lambda _device: (free, 12 * 1024 ** 3)))
        bridge.guard_free_vram(torch(10 * 1024 ** 3), "accelerated")
        with self.assertRaisesRegex(RuntimeError, "不会自动停止"):
            bridge.guard_free_vram(torch(3 * 1024 ** 3), "accelerated")

    def test_patch_source_and_backup_contract(self):
        source = Path(r"F:\AI\Breeze-TTS-2\local_webui.py")
        if not source.is_file():
            self.skipTest("source baseline is unavailable")
        raw = source.read_bytes()
        if apply_bridge.sha(raw) != apply_bridge.EXPECTED_SHA256:
            self.skipTest("source has already been patched; use the stored baseline backup for this check")
        changed = apply_bridge.patch_source(raw)
        original_tree, patched_tree = ast.parse(raw), ast.parse(changed)
        original_functions = {n.name: n for n in ast.walk(original_tree) if isinstance(n, ast.FunctionDef)}
        patched_functions = {n.name: n for n in ast.walk(patched_tree) if isinstance(n, ast.FunctionDef)}
        for name in ("performance_profile", "validate_performance_request", "synthesize", "load_model", "generate_audio"):
            self.assertEqual(ast.dump(original_functions[name]), ast.dump(patched_functions[name]), name)
        clicks = [n for n in ast.walk(patched_tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "click" and isinstance(n.func.value, ast.Name) and n.func.value.id == "generate"]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(len(clicks[0].args[1].elts), 11)
        self.assertEqual(len(clicks[0].args[2].elts), 4)
        with self.assertRaises(ValueError):
            apply_bridge.patch_source(raw + b"\n# unexpected source modification\n")
        with tempfile.TemporaryDirectory(prefix="langbai-bridge-test-") as directory:
            target = Path(directory) / "local_webui.py"
            target.write_bytes(raw)
            preview = apply_bridge.apply(target)
            self.assertTrue(preview["dryRun"])
            self.assertEqual(target.read_bytes(), raw)
            result = apply_bridge.apply(target, dry_run=False)
            self.assertEqual(Path(result["backup"]).read_bytes(), raw)
            self.assertEqual(target.read_bytes(), changed)
            self.assertEqual((target.parent / apply_bridge.MODULE_NAME).read_bytes(), (HERE / apply_bridge.MODULE_NAME).read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
