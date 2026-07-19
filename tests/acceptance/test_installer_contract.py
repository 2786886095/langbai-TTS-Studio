from __future__ import annotations

import json
from pathlib import Path

import pytest


INSTALLER_ENDPOINT = "/api/installer/catalog"


def _installer_available(client) -> bool:
    response = client.get(INSTALLER_ENDPOINT)
    return response.status_code != 404


@pytest.fixture()
def installer_client(api_client, require_implementation: bool):
    if not _installer_available(api_client):
        message = "Installer API is not implemented yet"
        if require_implementation:
            pytest.fail(message)
        pytest.skip(message)
    return api_client


def _catalog_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("engines", "items", "catalog"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise AssertionError(f"Unsupported installer catalog payload: {payload!r}")


def test_installer_catalog_has_three_official_engines_and_license_metadata(
    installer_client, project_root: Path
) -> None:
    baseline = json.loads(
        (project_root / "tests" / "acceptance" / "installer_catalog_baseline.json").read_text(encoding="utf-8")
    )
    response = installer_client.get(INSTALLER_ENDPOINT)
    assert response.status_code == 200, response.text
    items = {item.get("id") or item.get("engine_id"): item for item in _catalog_items(response.json())}
    assert set(items) == set(baseline["engines"])
    for engine_id, expected in baseline["engines"].items():
        item = items[engine_id]
        assert item.get("source_url") == expected["source_url"]
        assert item.get("source_commit") == expected["source_commit"]
        assert item.get("sha256") == expected["sha256"]
        assert len(item["sha256"]) == 64 and "latest" not in item["source_url"].lower()
        assert item.get("code_license")
        assert item.get("code_license_url")
        assert item["source_commit"] in item["code_license_url"]
        assert item.get("required_tools")
        assert item.get("required_runtime_licenses")
        models = item.get("models")
        assert isinstance(models, list) and models, f"{engine_id} has no selectable model metadata"
        for model in models:
            for key in baseline["model_required_metadata"]:
                assert model.get(key) not in (None, ""), f"{engine_id}.{model.get('id')}: missing {key}"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/installations/../../escape/setup", {}),
        ("post", "/api/installations/unknown/setup", {}),
        ("post", "/api/installations/indextts2/models", {"model_ids": ["../../escape"]}),
        ("post", "/api/installations/indextts2/setup", {"source_url": "https://evil.invalid/repo.git"}),
        ("post", "/api/installations/voxcpm/models", {"model_url": "file:///C:/Windows/win.ini"}),
        ("post", "/api/installations/gpt_sovits/setup", {"executable": "cmd.exe"}),
    ],
)
def test_install_request_rejects_unknown_ids_and_arbitrary_execution_inputs(
    installer_client, method: str, path: str, payload: dict, tmp_path: Path
) -> None:
    payload = {
        "installRoot": str(tmp_path / "managed"),
        "acceptLicense": True,
        "acceptPythonLicense": True,
        "acceptedToolLicenses": ["uv", "ffmpeg"],
        **payload,
    }
    response = installer_client.request(method, path, json=payload)
    assert response.status_code in {400, 403, 404, 422}, response.text


@pytest.mark.parametrize(
    "relative_path",
    ["../escape", "..\\escape"],
)
def test_install_request_rejects_path_escape(installer_client, relative_path: str) -> None:
    response = installer_client.post(
        "/api/installations/indextts2/setup",
        json={
            "install_root": relative_path,
            "accept_license": True,
        },
    )
    assert response.status_code in {400, 403, 422}, response.text


def test_request_models_forbid_untrusted_extra_fields(project_root: Path) -> None:
    import sys

    sys.path.insert(0, str(project_root / "backend"))
    from pydantic import ValidationError
    from app.installer.models import InstallRequest, ModelInstallRequest

    with pytest.raises(ValidationError):
        InstallRequest.model_validate({
            "installRoot": str(project_root),
            "acceptLicense": True,
            "source_url": "https://evil.invalid/repo.git",
        })
    with pytest.raises(ValidationError):
        ModelInstallRequest.model_validate({
            "installRoot": str(project_root),
            "acceptLicense": True,
            "modelId": "voxcpm2-official",
            "model_url": "file:///C:/Windows/win.ini",
        })


def test_installer_list_and_download_list_are_machine_readable(installer_client) -> None:
    installations = installer_client.get("/api/installations")
    assert installations.status_code == 200, installations.text
    assert isinstance(installations.json(), (list, dict))
    downloads = installer_client.get("/api/downloads")
    assert downloads.status_code == 200, downloads.text
    assert isinstance(downloads.json(), (list, dict))


@pytest.mark.parametrize("operation", ["pause", "resume", "cancel", "retry"])
def test_download_control_rejects_unknown_job(installer_client, operation: str) -> None:
    response = installer_client.post(f"/api/downloads/not-a-real-job/{operation}")
    assert response.status_code == 404, response.text
