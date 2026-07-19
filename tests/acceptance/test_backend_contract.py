from __future__ import annotations


def _unwrap_engines(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("engines", "items", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise AssertionError(f"Unsupported /api/engines response: {payload!r}")


def _engine_id(item: dict) -> str:
    for key in ("id", "engine", "name"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    raise AssertionError(f"Engine item has no id: {item!r}")


def _parameter_names(item: dict) -> set[str]:
    value = item.get("parameters") or item.get("params") or item.get("parameter_schema")
    if isinstance(value, list):
        return {entry["name"] for entry in value if isinstance(entry, dict) and "name" in entry}
    if isinstance(value, dict):
        if isinstance(value.get("properties"), dict):
            return set(value["properties"])
        return set(value)
    raise AssertionError(f"Engine has no machine-readable parameters: {item!r}")


def test_health_endpoint(api_client) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("status") in {"ok", "healthy", "ready"}


def test_engine_catalog_exposes_exactly_three_engines(api_client) -> None:
    response = api_client.get("/api/engines")
    assert response.status_code == 200, response.text
    engines = _unwrap_engines(response.json())
    assert {_engine_id(item) for item in engines} == {"indextts2", "voxcpm", "gpt_sovits"}


def test_engine_catalog_covers_native_parameter_baseline(api_client, parameter_baseline: dict) -> None:
    response = api_client.get("/api/engines")
    assert response.status_code == 200, response.text
    items = {_engine_id(item): item for item in _unwrap_engines(response.json())}
    for engine, baseline in parameter_baseline["engines"].items():
        actual = _parameter_names(items[engine])
        metadata = {item["name"]: item for item in baseline["parameters"]}
        # `text` is a job-level field shared by all engines. Other Studio aliases
        # may legitimately retain the upstream adapter field name instead.
        missing = []
        for name in baseline["required_surface"]:
            if name == "text":
                continue
            aliases = {name, metadata[name].get("native_name")}
            aliases.update(metadata[name].get("aliases", []))
            aliases.discard(None)
            if actual.isdisjoint(aliases):
                missing.append(name)
        missing.sort()
        assert not missing, f"{engine} API parameter catalog missing: {missing}"


def test_unknown_engine_is_rejected(api_client) -> None:
    response = api_client.post(
        "/api/jobs",
        json={"engine": "not-a-real-engine", "text": "验收文本", "parameters": {}},
    )
    assert response.status_code in {400, 404, 422}, response.text


def test_empty_text_is_rejected(api_client) -> None:
    response = api_client.post(
        "/api/jobs",
        json={"engine": "indextts2", "text": "   ", "parameters": {}},
    )
    assert response.status_code in {400, 422}, response.text


def test_unknown_parameter_is_not_silently_ignored(api_client) -> None:
    response = api_client.post(
        "/api/jobs",
        json={
            "engine": "voxcpm",
            "text": "未知参数必须被拒绝。",
            "parameters": {"definitely_unknown_parameter": 1},
        },
    )
    assert response.status_code in {400, 422}, response.text
