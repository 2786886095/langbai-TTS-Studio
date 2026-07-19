from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from .models import now_iso
from .workspace import atomic_write_json


CATALOG_ROOT = "https://rs.acgnai.top/api/model_libry"
COMMUNITY_SOURCE_PAGE = "https://www.ai-hobbyist.com/forum.php?mod=forumdisplay&fid=138"
ALLOWED_DOWNLOAD_HOSTS = {"pan.acgnai.top", "rs.acgnai.top"}
ALLOWED_ARCHIVE_SUFFIXES = {
    ".ckpt", ".pth", ".wav", ".mp3", ".flac", ".ogg", ".m4a",
    ".txt", ".json", ".md", ".yaml", ".yml",
}
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_ARCHIVE_MEMBERS = 20_000


class CommunityCatalogError(RuntimeError):
    pass


class CommunityInstallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    category: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=1, max_length=50)
    model_name: str = Field(alias="modelName", min_length=1, max_length=200)
    version: Literal["auto", "v2", "v3", "v4", "v2Pro", "v2ProPlus"] = "auto"
    license_accepted: bool = Field(alias="licenseAccepted")


class CommunityDownloadJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    category: str
    language: str
    model_name: str = Field(alias="modelName")
    version: str
    status: Literal["queued", "downloading", "extracting", "completed", "failed"] = "queued"
    progress: float = 0.0
    message: str = "等待下载"
    error: str | None = None
    installed_model: dict[str, Any] | None = Field(default=None, alias="installedModel")
    created_at: str = Field(default_factory=now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=now_iso, alias="updatedAt")


class CommunityCatalogClient:
    def __init__(self, root: str = CATALOG_ROOT, timeout: int = 30):
        self.root = root.rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.root}/{endpoint}", data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except Exception as exc:
            raise CommunityCatalogError(f"社区模型目录暂时不可用: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommunityCatalogError("社区模型目录返回了无法识别的数据") from exc

    def categories(self) -> list[str]:
        rows = self._post("categories", {"link": "line1"})
        return [str(row) for row in rows if str(row).strip()]

    def languages(self, category: str) -> list[str]:
        rows = self._post("langs", {"link": "line1", "category": category})
        return [str(row) for row in rows if str(row).strip()]

    def models(self, category: str, language: str) -> list[dict[str, str]]:
        rows = self._post("models", {"link": "line1", "category": category, "lang": language})
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("modelname") or "").strip()
            url = str(row.get("dl_link") or "").strip()
            if name and url:
                result.append({"name": name, "downloadUrl": url})
        return result


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    if not cleaned:
        cleaned = "community-model"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:80]}-{suffix}"


def _validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise CommunityCatalogError("社区目录返回了不受信任的下载地址，已拒绝下载")


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise CommunityCatalogError("模型压缩包文件数量异常，已拒绝解压")
    total = 0
    accepted = []
    for member in members:
        normalized = PurePosixPath(member.filename.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise CommunityCatalogError("模型压缩包包含越界路径，已拒绝解压")
        if member.is_dir():
            continue
        total += member.file_size
        if total > MAX_EXPANDED_BYTES:
            raise CommunityCatalogError("模型解压体积超过安全上限")
        if Path(normalized.name).suffix.lower() in ALLOWED_ARCHIVE_SUFFIXES:
            accepted.append(member)
    return accepted


class CommunityModelManager:
    def __init__(self, root: str | Path, catalog: CommunityCatalogClient | None = None):
        self.root = Path(root).resolve()
        self.installed_root = self.root / "installed"
        self.cache_root = self.root / "cache"
        self.installed_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog or CommunityCatalogClient()
        self._jobs: dict[str, CommunityDownloadJob] = {}
        self._lock = threading.RLock()

    def categories(self) -> list[str]:
        return self.catalog.categories()

    def languages(self, category: str) -> list[str]:
        return self.catalog.languages(category)

    def models(self, category: str, language: str) -> list[dict[str, Any]]:
        installed = {(item["category"], item["language"], item["name"]): item for item in self.list_installed()}
        return [{
            **row,
            "category": category,
            "language": language,
            "version": "auto",
            "sourcePage": COMMUNITY_SOURCE_PAGE,
            "licenseNotice": "目录接口只提供模型名与压缩包地址，不提供逐模型许可证；下载前请在社区模型区自行核对作者、用途限制与署名要求。",
            "installed": installed.get((category, language, row["name"])),
        } for row in self.catalog.models(category, language)]

    def list_installed(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.installed_root.glob("*/*/langbai-model.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if Path(payload["installPath"]).resolve(strict=False) != path.parent.resolve(strict=False):
                    continue
                rows.append(payload)
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
        return sorted(rows, key=lambda item: item.get("installedAt", ""), reverse=True)

    def list_jobs(self) -> list[CommunityDownloadJob]:
        with self._lock:
            return sorted((job.model_copy(deep=True) for job in self._jobs.values()), key=lambda item: item.created_at, reverse=True)

    def get_job(self, job_id: str) -> CommunityDownloadJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def install(self, request: CommunityInstallRequest) -> CommunityDownloadJob:
        if not request.license_accepted:
            raise ValueError("必须先确认已阅读社区原帖中的作者许可与使用限制")
        job = CommunityDownloadJob(
            id=uuid.uuid4().hex,
            category=request.category,
            language=request.language,
            modelName=request.model_name,
            version=request.version,
        )
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(target=self._run_install, args=(job.id,), daemon=True, name=f"community-model-{job.id[:8]}").start()
        return job.model_copy(deep=True)

    def _set(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = current.model_copy(update={**changes, "updated_at": now_iso()})

    def _run_install(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None:
            return
        cache = (self.cache_root / job.id).resolve()
        archive_path = cache / "model.zip"
        extract_root = cache / "extracted"
        try:
            matches = [row for row in self.catalog.models(job.category, job.language) if row["name"] == job.model_name]
            if not matches:
                raise CommunityCatalogError("社区目录中已找不到该模型，请刷新后重试")
            download_url = matches[0]["downloadUrl"]
            _validate_download_url(download_url)
            destination = (self.installed_root / job.version / _safe_name(f"{job.category}-{job.language}-{job.model_name}")).resolve()
            if destination.parent.parent != self.installed_root:
                raise CommunityCatalogError("模型目标路径无效")
            if destination.exists():
                raise CommunityCatalogError("该社区模型已经安装")
            cache.mkdir(parents=True, exist_ok=False)
            self._set(job.id, status="downloading", progress=0.01, message="正在下载社区模型")
            request = Request(download_url, headers={"User-Agent": "langbai-TTS-Studio/1.1"})
            with urlopen(request, timeout=60) as response, archive_path.open("wb") as output:
                final_url = response.geturl()
                _validate_download_url(final_url)
                total = int(response.headers.get("Content-Length") or 0)
                if total > MAX_ARCHIVE_BYTES:
                    raise CommunityCatalogError("模型压缩包超过 2GB 安全上限")
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_BYTES:
                        raise CommunityCatalogError("模型压缩包超过 2GB 安全上限")
                    output.write(chunk)
                    progress = min(0.82, 0.02 + (downloaded / total * 0.8 if total else 0.0))
                    self._set(job.id, progress=progress, message=f"已下载 {downloaded / 1024**2:.1f} MB")
                output.flush()
                os.fsync(output.fileno())

            self._set(job.id, status="extracting", progress=0.84, message="正在安全检查并解压权重")
            extract_root.mkdir(parents=True, exist_ok=False)
            with zipfile.ZipFile(archive_path) as archive:
                members = _safe_archive_members(archive)
                for index, member in enumerate(members):
                    relative = PurePosixPath(member.filename.replace("\\", "/"))
                    target = (extract_root / Path(*relative.parts)).resolve()
                    if not target.is_relative_to(extract_root):
                        raise CommunityCatalogError("模型压缩包包含越界路径")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    self._set(job.id, progress=0.84 + 0.12 * ((index + 1) / max(1, len(members))))

            gpt_files = sorted(extract_root.rglob("*.ckpt"), key=lambda path: path.stat().st_size, reverse=True)
            sovits_files = sorted(extract_root.rglob("*.pth"), key=lambda path: path.stat().st_size, reverse=True)
            if not gpt_files or not sovits_files:
                raise CommunityCatalogError("压缩包没有同时包含 GPT(.ckpt) 与 SoVITS(.pth) 权重")
            audio_files = sorted(
                [path for suffix in ("*.wav", "*.mp3", "*.flac", "*.ogg", "*.m4a") for path in extract_root.rglob(suffix)],
                key=lambda path: ("reference_audios" not in str(path).lower(), str(path)),
            )
            os.replace(extract_root, destination)
            gpt_path = destination / gpt_files[0].relative_to(extract_root)
            sovits_path = destination / sovits_files[0].relative_to(extract_root)
            reference = destination / audio_files[0].relative_to(extract_root) if audio_files else None
            prompt_text = ""
            if reference:
                prompt_text = re.sub(r"^【[^】]+】", "", reference.stem)
            manifest = {
                "id": hashlib.sha256(f"{job.version}\n{job.category}\n{job.language}\n{job.model_name}".encode("utf-8")).hexdigest()[:24],
                "name": job.model_name,
                "category": job.category,
                "language": job.language,
                "version": job.version,
                "installPath": str(destination),
                "gptWeightsPath": str(gpt_path),
                "sovitsWeightsPath": str(sovits_path),
                "referenceAudio": str(reference) if reference else None,
                "promptText": prompt_text,
                "sourcePage": COMMUNITY_SOURCE_PAGE,
                "downloadHost": urlparse(download_url).hostname,
                "installedAt": now_iso(),
            }
            atomic_write_json(destination / "langbai-model.json", manifest)
            self._set(job.id, status="completed", progress=1.0, message="社区模型已安装，可创建角色声音", installed_model=manifest)
        except Exception as exc:
            self._set(job.id, status="failed", message="社区模型安装失败", error=str(exc))
        finally:
            if cache.is_relative_to(self.cache_root):
                shutil.rmtree(cache, ignore_errors=True)
