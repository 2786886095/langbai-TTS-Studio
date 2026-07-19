from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest


def require_commercial_gate(commercial_release: bool) -> None:
    if not commercial_release:
        pytest.skip("strict UI checks run with --prepackage")


def test_frontend_uses_durable_project_api(project_root: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    source = (project_root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    project_upsert = re.compile(
        r"fetch\s*\(\s*apiUrl\s*\(\s*projectId\s*\?\s*"
        r"`/api/projects/\$\{projectId\}`\s*:\s*[\"']/api/projects[\"']\s*\)",
        re.DOTALL,
    )
    method_upsert = re.compile(
        r"method\s*:\s*projectId\s*\?\s*[\"']PUT[\"']\s*:\s*[\"']POST[\"']",
        re.DOTALL,
    )
    assert project_upsert.search(source), (
        "保存方案必须通过项目 ID 分流调用 POST /api/projects 或 PUT /api/projects/{id}；"
        "仅出现端点字符串不足以通过门禁"
    )
    assert method_upsert.search(source), "项目持久化调用必须区分新建 POST 与更新 PUT"
    assert 'localStorage.setItem("langbai-project"' not in source, (
        "项目正文和参数不能只保存在单一 localStorage 槽位"
    )


def test_audio_library_has_real_playback(project_root: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    source = (project_root / "frontend" / "src" / "WorkspacePages.tsx").read_text(encoding="utf-8")
    assert re.search(r"<audio\b", source), "音频库不能只有打开位置/复制路径，必须有内置试听控件"
    assert "getAudioUrl" in source, "播放器必须通过受控的 Electron 本地音频 URL 读取输出"


def test_settings_and_diagnostics_are_reachable_from_ui(project_root: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    frontend = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (project_root / "frontend" / "src").glob("*.tsx")
    )
    assert "/api/settings" in frontend, "设置页面尚未读写全局设置 API"
    assert "/api/diagnostics/exports" in frontend, "UI 尚无诊断包创建/导出入口"


def test_cancelled_is_not_presented_as_failed(project_root: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    source = (project_root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'statusValue === "cancelled" ? "failed"' not in source, "取消状态被错误归类为失败"
    assert '"cancelled"' in source, "创作台任务模型必须保留 cancelled 独立状态"


@pytest.fixture(scope="session")
def ui_metrics(project_root: Path, commercial_release: bool) -> dict:
    require_commercial_gate(commercial_release)
    if os.environ.get("CI") and os.environ.get("LANGBAI_CI_UI_CAPTURE") != "1":
        pytest.skip(
            "GitHub-hosted Windows runners do not provide a reliable interactive desktop; "
            "real Electron window evidence remains a required local prepackage gate"
        )
    built = subprocess.run(
        ["npm.cmd", "run", "build:frontend"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0, (
        f"商业 UI 采集前必须成功构建当前源码\n{built.stdout}\n{built.stderr}"
    )
    dist = project_root / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        pytest.fail("缺少 frontend/dist；先执行 npm run build:frontend")
    electron = project_root / "node_modules" / "electron" / "dist" / "electron.exe"
    if not electron.is_file():
        pytest.fail("缺少本地 Electron 运行时，无法生成真实窗口证据")
    output = project_root / "docs" / "audit" / "commercial-current"
    script = project_root / "tests" / "acceptance" / "capture_ui_metrics.cjs"
    completed = subprocess.run(
        [str(electron), str(script), str(output)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"Electron UI 采集失败:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads((output / "ui-metrics.json").read_text(encoding="utf-8"))


def iter_ui_states(ui_metrics: dict):
    for label, view in ui_metrics["views"].items():
        yield f"1920×1080 / {label}", view
    yield "1920×1080 / 150% 缩放 / 创作台", ui_metrics["zoom150"]
    for label, view in ui_metrics["minimumViews"].items():
        yield f"1180×720 / {label}", view


def test_navigation_pages_are_distinct_and_nonempty(ui_metrics: dict) -> None:
    expected = {
        "创作台": ("studio", "把长文本变成可控的声音"),
        "任务队列": ("data-page", "任务队列"),
        "音频库": ("data-page", "音频库"),
        "历史记录": ("data-page", "历史记录"),
        "设置与路径": ("settings", "引擎管理"),
    }
    failures = []
    for label, (kind, title) in expected.items():
        view = ui_metrics["views"][label]
        if view["pageKind"] != kind or view["title"] != title:
            failures.append(f"{label}: kind={view['pageKind']!r}, title={view['title']!r}")
    assert not failures, "导航页仍为空壳或复用错误页面:\n" + "\n".join(failures)


def test_capture_uses_required_default_zoom_and_minimum_viewports(ui_metrics: dict) -> None:
    expected_default = {"width": 1920, "height": 1080}
    expected_minimum = {"width": 1180, "height": 720}
    failures = []
    for label, view in ui_metrics["views"].items():
        actual = {key: view["viewport"][key] for key in ("width", "height")}
        if actual != expected_default:
            failures.append(f"默认 100% / {label}: {actual!r}")
    zoom_viewport = {
        key: ui_metrics["zoom150"]["viewport"][key]
        for key in ("width", "height")
    }
    expected_zoom = {
        "width": round(expected_default["width"] / 1.5),
        "height": round(expected_default["height"] / 1.5),
    }
    if zoom_viewport != expected_zoom:
        failures.append(f"150% 缩放的 CSS viewport: {zoom_viewport!r}")
    for label, view in ui_metrics["minimumViews"].items():
        actual = {key: view["viewport"][key] for key in ("width", "height")}
        if actual != expected_minimum:
            failures.append(f"最小窗口 / {label}: {actual!r}")
    assert not failures, "UI 证据未在规定的相同 viewport 采集:\n" + "\n".join(failures)


def test_all_visible_text_is_at_least_12px(ui_metrics: dict) -> None:
    failures = []
    for label, view in iter_ui_states(ui_metrics):
        for item in view["undersizedText"][:20]:
            failures.append(f"{label}: {item['fontSize']}px {item['selector']} {item['text']!r}")
    assert not failures, "发现小于 12px 的可见文字（最多列出每页 20 项）:\n" + "\n".join(failures)


def test_default_body_and_action_typography_meets_product_baseline(ui_metrics: dict) -> None:
    failures = []
    for label, view in iter_ui_states(ui_metrics):
        if view["appFontSize"] < 14:
            failures.append(f"{label}: 应用正文基准仅 {view['appFontSize']}px，要求至少 14px")
        for item in view["undersizedActionText"][:15]:
            failures.append(
                f"{label}: 操作文字 {item['fontSize']}px {item['selector']} {item['text']!r}"
            )
        for item in view["undersizedPrimaryActionText"][:15]:
            failures.append(
                f"{label}: 主操作文字 {item['fontSize']}px {item['selector']} {item['text']!r}"
            )
    assert not failures, (
        "舒适模式要求正文基准至少 14px、可见操作文字至少 14px、主操作至少 15px:\n"
        + "\n".join(failures)
    )


def test_interactive_targets_are_at_least_40px(ui_metrics: dict) -> None:
    failures = []
    for label, view in iter_ui_states(ui_metrics):
        for item in view["undersizedTargets"][:25]:
            failures.append(
                f"{label}: {item['width']}x{item['height']} {item['selector']} {item['label']!r}"
            )
    assert not failures, "发现小于内部商业基线 40x40 CSS px 的可见交互目标:\n" + "\n".join(failures)


def test_no_horizontal_overflow_at_default_zoom_and_minimum(ui_metrics: dict) -> None:
    failures = [
        f"{label}: scroll={view['bodyScrollWidth']} client={view['bodyClientWidth']}"
        for label, view in iter_ui_states(ui_metrics)
        if view["bodyScrollWidth"] > view["bodyClientWidth"] + 1
    ]
    assert not failures, "窗口存在水平溢出:\n" + "\n".join(failures)


def test_runtime_errors_are_localized_and_actionable(ui_metrics: dict) -> None:
    failures = []
    raw_error_fragments = ("Failed to fetch", "NetworkError", "ECONNREFUSED")
    for label, view in iter_ui_states(ui_metrics):
        text = view["bodyText"]
        for fragment in raw_error_fragments:
            if fragment in text:
                failures.append(f"{label}: 直接暴露底层英文异常 {fragment!r}")
    assert not failures, "错误状态必须给出中文原因与下一步操作:\n" + "\n".join(failures)


def test_keyboard_focus_leaves_body_and_reaches_multiple_visible_controls(ui_metrics: dict) -> None:
    trace = ui_metrics["focusTrace"]
    meaningful = [item for item in trace if item["tag"] not in {"", "body"}]
    unique = {(item["tag"], item["label"]) for item in meaningful}
    assert len(unique) >= 8, (
        f"Tab 键焦点轨迹不足，实际仅到达 {len(unique)} 个不同目标: {trace}"
    )
    invisible = [item for item in meaningful if not item.get("focusVisible")]
    assert not invisible, f"键盘焦点到达控件但 :focus-visible 未生效: {invisible}"
