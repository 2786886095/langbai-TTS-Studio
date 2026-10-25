"""Explicit cooperation with the user's separate local Gradio installation."""
from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

BASE_URL = "http://127.0.0.1:7862"


def _request(path, payload=None, timeout=5):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE_URL + path, data=data, headers={"Content-Type": "application/json"})
    return build_opener(ProxyHandler({})).open(request, timeout=timeout)


def _capabilities():
    with _request("/config") as response:
        config = json.load(response)
    if not str(config.get("title", "")).startswith("Breeze TTS 2"):
        raise ValueError("7862 端口不是已识别的 Breeze 本地网页")
    return {item.get("api_name") for item in config.get("dependencies", [])}


def _call(name):
    if name not in {"runtime_status", "unload_model"}:
        raise ValueError("不允许的网页模型操作")
    if name not in _capabilities():
        raise ValueError("网页尚未启用 Studio 协作桥，请重启更新后的 Breeze 网页")
    with _request(f"/gradio_api/call/{name}", {"data": []}) as response:
        event_id = json.load(response).get("event_id", "")
    if not isinstance(event_id, str) or not event_id.isalnum():
        raise ValueError("网页返回的任务 ID 无效")
    with _request(f"/gradio_api/call/{name}/{event_id}", timeout=65) as response:
        event = ""
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "error":
                raise RuntimeError("网页模型操作失败：" + line[5:].strip())
            elif line.startswith("data:") and event == "complete":
                data = json.loads(line[5:])
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    return data[0]
                raise ValueError("网页模型返回了无效状态")
    raise RuntimeError("网页未返回操作完成记录")


def status():
    try:
        return {"available": True, "url": BASE_URL, **_call("runtime_status")}
    except (OSError, URLError, ValueError, RuntimeError) as exc:
        return {"available": False, "url": BASE_URL, "detail": str(exc)}


def unload():
    # Called only by a user-facing POST; never on adapter startup or GPU wait.
    return _call("unload_model")
