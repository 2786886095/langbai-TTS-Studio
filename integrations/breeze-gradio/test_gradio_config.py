"""CPU-only contract check in the existing Gradio environment; never launch."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import apply_bridge


def main():
    source_path = Path(os.environ.get("BREEZE_WEBUI_SOURCE", "/mnt/f/AI/Breeze-TTS-2/local_webui.py"))
    raw = source_path.read_bytes()
    patched = apply_bridge.patch_source(raw)
    sys.path.insert(0, str(source_path.parent))
    module = types.ModuleType("breeze_bridge_contract")
    module.__file__ = str(source_path)
    exec(compile(patched, str(source_path), "exec"), module.__dict__)

    def must_not_load(*args, **kwargs):
        raise AssertionError("Config construction must never load a model")

    module.BreezeLocalRuntime.ensure_loaded = must_not_load
    demo = module.build_ui(performance_mode="accelerated")
    api = {item["api_name"]: {"inputs": len(item["inputs"]), "outputs": len(item["outputs"]),
                              "js": bool(item.get("js")), "showApi": item.get("show_api")}
           for item in demo.config["dependencies"] if item.get("api_name")}
    assert api["generate_audio"]["inputs"] == 11 and api["generate_audio"]["outputs"] == 4
    assert api["load_model"]["inputs"] == 0 and api["load_model"]["outputs"] == 1
    assert api["generate_audio"]["js"] and api["load_model"]["js"]
    for name in ("runtime_status", "unload_model"):
        assert api[name]["inputs"] == 0 and api[name]["outputs"] == 1
        assert api[name]["showApi"] is False
        functions = [function for function in demo.fns.values() if function.api_name == name]
        assert len(functions) == 1 and functions[0].concurrency_id == "breeze-gpu"
    tabs = [component["props"].get("label") for component in demo.config["components"] if component["type"] == "tabitem"]
    assert tabs == ["短句试听", "长文本"], tabs
    assert module._runtime_instance.loaded is False
    torch = sys.modules.get("torch")
    assert torch is None or not torch.cuda.is_initialized()
    print(json.dumps({"status": "passed", "tabs": tabs, "api": api, "cudaInitialized": False,
                      "modelLoaded": False, "serverLaunched": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
