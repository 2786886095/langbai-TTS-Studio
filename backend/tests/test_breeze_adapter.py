from __future__ import annotations

import hashlib
import io
import json
import queue
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import breeze_binding
from app.adapters.base import EngineError
from app.adapters.breeze import BreezeAdapter, PROTOCOL_PREFIX, parse_message
from app.breeze_binding import BreezeBindingRequest, BreezeBindingStore, windows_to_wsl
from app.service_runtime import DeviceCoordinator
from breeze_worker import FAST_FLAGS, identity_matches, safe_output, validate_parameters
import breeze_worker_ctl


def binding_fixture(tmp_path):
    source = tmp_path / "安装 with 空格 & ; $"
    model = source / "checkpoints" / "Breeze-TTS-2"
    (source / "models/stream_runtime/stream").mkdir(parents=True)
    (source / "breeze_infer").mkdir()
    (source / breeze_binding.KV_PATCH_PATH).write_bytes(b"verified patch")
    (source / "breeze_infer/runtime.py").write_text("# fixture\n", encoding="utf-8")
    (model / "audio_tokenizer").mkdir(parents=True)
    for name in ("config.json", "audio_tokenizer/config.json", "LICENSE", "tokenizer.json",
                 "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                 "audio_tokenizer/model.safetensors"):
        (model / name).write_text("fixture", encoding="utf-8")
    record = BreezeBindingRequest(sourcePath=str(source), modelPath=str(model), confirm=True,
                                 licenseAccepted=True).model_dump(mode="json", by_alias=True)
    record.update(schemaVersion=1, sourceCommit=breeze_binding.SOURCE_COMMIT,
                  modelRevision=breeze_binding.MODEL_REVISION, patchSha256=breeze_binding.KV_PATCH_SHA256,
                  licenseVersion="v1.1", updatedAt="2026-10-25T00:00:00Z")
    store = BreezeBindingStore(tmp_path / "data/v2/breeze-binding.json")
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(record), encoding="utf-8")
    return store, source, model


def test_windows_paths_are_arguments_not_shell_programs():
    assert windows_to_wsl(r"F:\中文 & $(touch nope)\part.wav") == "/mnt/f/中文 & $(touch nope)/part.wav"
    for value in (r"\\server\share\file.wav", "relative.wav", "F:relative.wav", r"F:\ok\..\escape"):
        with pytest.raises(ValueError):
            windows_to_wsl(value)


def test_binding_requires_explicit_license_and_leaves_source_unchanged(tmp_path, monkeypatch):
    store, source, model = binding_fixture(tmp_path)
    store.unbind()
    monkeypatch.setattr(breeze_binding, "KV_PATCH_SHA256", hashlib.sha256(b"verified patch").hexdigest())
    with pytest.raises(ValueError, match="许可"):
        store.bind(BreezeBindingRequest(sourcePath=str(source), modelPath=str(model), confirm=True))
    result = store.bind(BreezeBindingRequest(sourcePath=str(source), modelPath=str(model), confirm=True, licenseAccepted=True))
    assert result["performanceMode"] == "accelerated"
    assert (source / breeze_binding.KV_PATCH_PATH).read_bytes() == b"verified patch"
    assert store.get()["sourcePath"] == str(source.resolve())
    assert not (tmp_path / "data/engine-bindings.json").exists()


def test_binding_refuses_unknown_patch_without_repair(tmp_path):
    store, source, model = binding_fixture(tmp_path)
    with pytest.raises(ValueError, match="KV"):
        store.bind(BreezeBindingRequest(sourcePath=str(source), modelPath=str(model), confirm=True, licenseAccepted=True))
    assert (source / breeze_binding.KV_PATCH_PATH).read_bytes() == b"verified patch"


def test_output_root_and_attempt_are_enforced(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    name = "a" * 64 + "." + "b" * 32 + ".wav.part"
    assert safe_output(root, root / name) == root / name
    with pytest.raises(ValueError):
        safe_output(root, tmp_path / name)
    with pytest.raises(ValueError):
        safe_output(root, root / "fixed.wav")
    with pytest.raises(ValueError):
        safe_output(root, root / "nested" / name)


def test_worker_input_contract_rejects_unsupported_modes_and_over_budget(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"reference")
    params = {"ref_audio_path": str(ref), "ref_text": "参考文字"}
    assert validate_parameters("你好", params)["seed"] == 42
    for extra in ({"cfg_scale": 2}, {"instruction": "开心"}, {"performance_mode": "eager"},
                  {"temperature": float("nan")}, {"max_new_tokens": 0}):
        with pytest.raises(ValueError):
            validate_parameters("你好", {**params, **extra})
    with pytest.raises(ValueError, match="300"):
        validate_parameters("字" * 301, params)


def test_cache_identity_changes_when_reference_source_or_weight_changes(tmp_path):
    store, source, model = binding_fixture(tmp_path)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"first")
    adapter = BreezeAdapter(store, tmp_path / "data/v2/staging", tmp_path / "logs")
    p = {"ref_audio_path": str(ref), "ref_text": "原文"}
    identity = adapter.cache_identity(p)
    ref.write_bytes(b"second")
    assert adapter.cache_identity(p) != identity
    ref.write_bytes(b"first")
    (source / "breeze_infer/runtime.py").write_text("# changed\n")
    assert adapter.cache_identity(p) != identity
    (source / "breeze_infer/runtime.py").write_text("# fixture\n")
    (model / "model-00001-of-00002.safetensors").write_bytes(b"different weight bytes")
    assert adapter.cache_identity(p) != identity


def test_protocol_does_not_accept_upstream_progress_noise():
    assert parse_message('progress 23%') is None
    assert parse_message('{"event":"hello"}') is None
    assert parse_message(PROTOCOL_PREFIX + '{"event":"hello"}') == {"event": "hello"}


def registered_identity():
    return {"pid": 1234, "start_time": "100", "pgid": 1234, "sid": 1234,
            "boot_id": "boot", "cmdline_sha256": "cmdhash", "exe": "/venv/python",
            "uid": 1000, "token": "a" * 32, "state": "S"}


def test_controller_does_not_signal_reused_pid(monkeypatch):
    identity = registered_identity()
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "boot")
    monkeypatch.setattr(breeze_worker_ctl, "process_identity", lambda *a: {**identity, "start_time": "200"})
    monkeypatch.setattr(breeze_worker_ctl.os, "killpg", lambda *a: pytest.fail("must not signal a reused PID"), raising=False)
    assert breeze_worker_ctl.terminate_owned(identity) == {"stopped": False, "reason": "identity_mismatch"}
    assert not identity_matches(identity, {**identity, "token": "other"})


def test_controller_recognizes_wsl_reboot_without_touching_new_process(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "different-boot")
    monkeypatch.setattr(breeze_worker_ctl, "process_identity", lambda *a: pytest.fail("old PID must not be inspected on a new boot"))
    assert breeze_worker_ctl.terminate_owned(registered_identity())["reason"] == "wsl_rebooted"


def test_controller_never_claims_incomplete_identity_exited():
    assert breeze_worker_ctl.terminate_owned({"pid": 1234}) == {"stopped": False, "reason": "identity_incomplete"}


def test_controller_recognizes_original_zombie_without_signalling(monkeypatch):
    identity = registered_identity()
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "boot")
    monkeypatch.setattr(breeze_worker_ctl, "process_identity", lambda *a: {**identity, "state": "Z"})
    monkeypatch.setattr(breeze_worker_ctl.os, "killpg", lambda *a: pytest.fail("zombie has already exited"), raising=False)
    assert breeze_worker_ctl.terminate_owned(identity) == {"stopped": True, "reason": "exited_zombie"}


class QueueLines:
    def __init__(self):
        self.lines = queue.Queue()

    def emit(self, payload):
        self.lines.put(PROTOCOL_PREFIX + json.dumps(payload) + "\n")

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


class FakeInput:
    def __init__(self, process, staging):
        self.process, self.staging = process, staging

    def write(self, text):
        request = json.loads(text)
        self.process.requests.append(request)
        if request["op"] == "synthesize":
            target = self.staging / request["output"].rsplit("/", 1)[-1]
            target.write_bytes(b"test audio")
            self.process.stdout.emit({"id": request["id"], "ok": True, "metadata": {
                "audio_sha256": hashlib.sha256(b"test audio").hexdigest(), "sample_rate": 24000,
                "possibly_truncated": False, "worker_instance": self.process.token}})
        elif request["op"] == "shutdown":
            self.process.code = 0
            self.process.stdout.lines.put(None)

    def flush(self):
        pass


class FakeProcess:
    def __init__(self, command, staging):
        self.pid, self.code, self.requests = 42, None, []
        self.token = command[command.index("--instance-token") + 1]
        self.stdout, self.stderr = QueueLines(), io.StringIO("")
        self.stdin = FakeInput(self, staging)
        identity = {**registered_identity(), "token": self.token}
        self.stdout.emit({"event": "identity", "identity": identity})
        self.stdout.emit({"event": "hello", "protocolVersion": 1,
                          "source_commit": breeze_binding.SOURCE_COMMIT, "model_revision": breeze_binding.MODEL_REVISION,
                          "patch_sha256": breeze_binding.KV_PATCH_SHA256, "performance_mode": "accelerated",
                          "fast_flags": FAST_FLAGS, "depth_bucket_sizes": [1], "weights_verified": True})

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code

    def terminate(self):
        self.code = 0


def test_mock_transport_reuses_one_owned_worker_and_checks_result_hash(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    staging = tmp_path / "data/v2/staging"
    staging.mkdir()
    created, calls = [], []
    def popen(command, **kwargs):
        assert isinstance(command, list) and not kwargs.get("shell")
        result = FakeProcess(command, staging)
        created.append(result)
        return result
    def ctl(command, **kwargs):
        calls.append((command, json.loads(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout='{"stopped":true}', stderr="")
    adapter = BreezeAdapter(store, staging, tmp_path / "logs", popen_factory=popen, run_command=ctl)
    ref = tmp_path / "voice.wav"
    ref.write_bytes(b"ref")
    p = {"ref_audio_path": str(ref), "ref_text": "参考"}
    for number in (1, 2):
        target = staging / ("a" * 64 + "." + f"{number:032x}" + ".wav.part")
        assert adapter.synthesize("你好", target, p)["sample_rate"] == 24000
    assert len(created) == 1
    assert len(created[0].requests) == 2
    adapter.close()
    assert calls[0][1]["token"] == created[0].token
    assert adapter.runtime_snapshot()["exitConfirmed"] is True


def test_unconfirmed_stop_does_not_allow_a_replacement_worker(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    staging = tmp_path / "data/v2/staging"
    staging.mkdir()
    adapter = BreezeAdapter(store, staging, tmp_path / "logs",
                            popen_factory=lambda command, **kw: FakeProcess(command, staging),
                            run_command=lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"stopped":false,"reason":"identity_mismatch"}', stderr=""))
    adapter._start()
    assert adapter.runtime_snapshot()["warmed"] is False
    adapter.close()
    assert adapter.runtime_snapshot()["exitConfirmed"] is False
    with pytest.raises(EngineError, match="尚未确认退出"):
        adapter._start()


def test_changed_source_cannot_silently_use_the_loaded_old_worker(tmp_path):
    store, source, _ = binding_fixture(tmp_path)
    staging = tmp_path / "data/v2/staging"
    staging.mkdir()
    created = []
    def popen(command, **kwargs):
        process = FakeProcess(command, staging)
        created.append(process)
        return process
    adapter = BreezeAdapter(store, staging, tmp_path / "logs", popen_factory=popen,
                            run_command=lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"stopped":true}', stderr=""))
    adapter._start()
    assert len(created) == 1
    (source / "breeze_infer/runtime.py").write_text("# updated while idle\n")
    adapter._start()
    assert len(created) == 2 and created[0].poll() == 0
    adapter.close()


def test_output_outside_staging_is_rejected_before_any_process(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs",
                            popen_factory=lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(EngineError, match="staging"):
        adapter.synthesize("你好", tmp_path / "escape.wav", {})


def test_unknown_registered_worker_is_not_terminated(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs",
                            run_command=lambda *a, **k: pytest.fail("must not send a kill"))
    adapter._registry.write_text(json.dumps({"stagingRoot": "different-install", "identity": registered_identity()}))
    assert adapter.recover_owned_worker()["stopped"] is False
    assert adapter.runtime_snapshot()["exitConfirmed"] is False
    assert adapter.runtime_snapshot()["phase"] == "stop_unconfirmed"


def registered_orphan(adapter, store):
    identity = registered_identity()
    record = {"stagingRoot": str(adapter.staging_root), "identity": identity,
              "instanceToken": identity["token"], "binding": store.get(), "exitConfirmed": False}
    adapter._registry.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_startup_orphan_without_windows_popen_blocks_other_gpu_until_exit_proven(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    calls = []
    def ctl(command, **kwargs):
        calls.append((command, json.loads(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout='{"stopped":false,"reason":"identity_mismatch"}', stderr="")
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs", run_command=ctl)
    record = registered_orphan(adapter, store)
    assert adapter._process is None
    result = adapter.recover_owned_worker()
    assert result["stopped"] is False
    assert adapter.runtime_snapshot()["exitConfirmed"] is False
    coordinator = DeviceCoordinator({"breeze": adapter, "indextts2": SimpleNamespace()})
    with pytest.raises(RuntimeError, match="尚未确认退出"):
        coordinator.reserve("legacy:next-job", "indextts2")
    assert coordinator.snapshot() is None
    assert all(identity == record["identity"] for _, identity in calls)
    assert all(command[command.index("--distribution") + 1] == record["binding"]["distribution"] for command, _ in calls)
    assert json.loads(adapter._registry.read_text())["exitConfirmed"] is False


def test_close_recovers_original_registered_orphan_then_allows_other_gpu(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs",
                            run_command=lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"stopped":true,"reason":"already_exited"}', stderr=""))
    record = registered_orphan(adapter, store)
    # A replacement binding must not redirect cleanup into a different distro.
    current = store.get()
    current["distribution"] = "Different-New-Distro"
    store.path.write_text(json.dumps(current), encoding="utf-8")
    controls = []
    def ctl(command, **kwargs):
        controls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"stopped":true,"reason":"already_exited"}', stderr="")
    adapter._run = ctl
    adapter.close()
    assert adapter.runtime_snapshot()["exitConfirmed"] is True
    assert adapter.runtime_snapshot()["phase"] == "stopped"
    assert controls[0][controls[0].index("--distribution") + 1] == record["binding"]["distribution"]
    assert json.loads(adapter._registry.read_text())["exitConfirmed"] is True
    coordinator = DeviceCoordinator({"breeze": adapter, "indextts2": SimpleNamespace()})
    coordinator.reserve("legacy:next-job", "indextts2")
    assert coordinator.snapshot()["id"] == "legacy:next-job"
    coordinator.release("legacy:next-job")


def test_unreadable_or_mismatched_orphan_registry_never_claims_exit(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs",
                            run_command=lambda *a, **k: pytest.fail("unverifiable records must not signal processes"))
    adapter._registry.write_text("{truncated", encoding="utf-8")
    assert adapter.recover_owned_worker()["stopped"] is False
    assert adapter.runtime_snapshot()["exitConfirmed"] is False
    adapter._registry.write_text(json.dumps({"stagingRoot": "foreign-install", "exitConfirmed": True}))
    adapter.close()
    assert adapter.runtime_snapshot()["exitConfirmed"] is False
    adapter._registry.write_text(json.dumps({"stagingRoot": str(adapter.staging_root), "identity": {}, "binding": store.get(), "exitConfirmed": True}))
    assert adapter.recover_owned_worker()["stopped"] is False


def test_nonboolean_controller_claim_is_not_exit_evidence(tmp_path):
    store, _, _ = binding_fixture(tmp_path)
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs",
                            run_command=lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"stopped":"true"}', stderr=""))
    registered_orphan(adapter, store)
    assert adapter.recover_owned_worker() == {"stopped": False, "reason": "invalid_controller_evidence"}
    assert adapter.runtime_snapshot()["exitConfirmed"] is False


def test_status_reports_missing_wsl_without_loading_any_model(tmp_path, monkeypatch):
    store, source, _ = binding_fixture(tmp_path)
    monkeypatch.setattr("app.adapters.breeze.KV_PATCH_SHA256", hashlib.sha256(b"verified patch").hexdigest())
    adapter = BreezeAdapter(store, tmp_path / "staging", tmp_path / "logs", wsl_executable="not-a-real-wsl.exe",
                            popen_factory=lambda *a, **k: pytest.fail("status must not load"))
    assert adapter.status()["available"] is False
    assert "wsl.exe" in adapter.status()["detail"]
