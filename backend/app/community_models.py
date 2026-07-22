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
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from pydantic import BaseModel, ConfigDict, Field

from .models import now_iso
from .workspace import atomic_write_json


CATALOG_ROOT = "https://rs.acgnai.top/api/model_libry"
COMMUNITY_SOURCE_PAGE = "https://www.ai-hobbyist.com/forum.php?mod=forumdisplay&fid=138"
HUGGING_FACE_API = "https://huggingface.co/api/models"
ALLOWED_DOWNLOAD_HOSTS = {"pan.acgnai.top", "rs.acgnai.top"}
ALLOWED_DOWNLOAD_HOST_PATTERNS = (
    re.compile(r"^cdn-lfs-cn-\d+\.modelscope\.cn$", re.IGNORECASE),
)
ALLOWED_ARCHIVE_SUFFIXES = {
    ".ckpt", ".pth", ".wav", ".mp3", ".flac", ".ogg", ".m4a",
    ".txt", ".json", ".md", ".yaml", ".yml",
}
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_ARCHIVE_MEMBERS = 20_000

CLOUD_MODEL_SOURCES = [
    {
        "id": "bilibili-model-search",
        "name": "Bilibili GPT-SoVITS 模型分享检索",
        "platform": "Bilibili / 多种网盘",
        "sourceType": "cloud",
        "sourcePage": "https://search.bilibili.com/all?keyword=GPT-SoVITS%E6%A8%A1%E5%9E%8B%E5%88%86%E4%BA%AB",
        "summary": "持续更新的模型分享视频和动态入口；下载路径通常位于简介、置顶评论或作者动态。",
        "licenseNotice": "Bilibili 搜索结果并非软件审核目录，链接有效性与许可由发布者决定。",
    },
    {
        "id": "hf-search",
        "name": "Hugging Face GPT-SoVITS 模型索引",
        "platform": "Hugging Face",
        "sourceType": "repository",
        "sourcePage": "https://huggingface.co/models?search=gpt-sovits",
        "summary": "实时索引约 260 个相关仓库；仓库可能包含多个角色的 GPT 与 SoVITS 权重。",
        "licenseNotice": "每个仓库的许可证不同，下载前应查看模型卡和文件页。",
    },
    {
        "id": "unlimitedburst-collection",
        "name": "原神・崩坏3・星穹铁道・绝区零・蔚蓝档案合集",
        "platform": "Hugging Face",
        "sourceType": "repository",
        "sourcePage": "https://huggingface.co/UnlimitedBurst/GPT-SoVITS/tree/main",
        "summary": "大型 V2 角色模型合集，含多个作品分类和参考音频。",
        "licenseNotice": "仓库标注 MIT，但角色声音素材及二次使用权仍需使用者自行确认。",
    },
    {
        "id": "xiaomipo-downloads",
        "name": "GPT-SoVITS 整合包与网盘下载中心",
        "platform": "百度网盘 / 夸克网盘",
        "sourceType": "cloud",
        "sourcePage": "https://gpt-sovits.xiaomipo.com/download.html",
        "summary": "提供 V2、V2Pro、V3、V4 等整合包的百度和夸克网盘入口。",
        "licenseNotice": "第三方转载页面；文件来源、完整性和许可需自行核对。",
    },
    {
        "id": "official-baidu-index",
        "name": "花儿不哭 GPT-SoVITS 整合包与模型入口",
        "platform": "百度网盘",
        "sourceType": "cloud",
        "sourcePage": "https://www.kbiao.net/138.html",
        "summary": "整理官方项目、教程及百度网盘模型入口。",
        "licenseNotice": "页面和网盘内容可能变化；应以原作者说明为准。",
    },
    {
        "id": "laoba-bilibili",
        "name": "岛市老八 GPT-SoVITS 模型",
        "platform": "百度网盘 / 阿里云盘",
        "sourceType": "cloud",
        "sourcePage": "https://www.bilibili.com/video/BV1rz421y7C9/",
        "summary": "模型分享原帖提供阿里云盘与百度网盘路径。",
        "licenseNotice": "许可未明确；不得据此推定可商用或可二次分发。",
    },
    {
        "id": "garen-bilibili",
        "name": "LOL AI 盖伦 GPT-SoVITS V3 模型",
        "platform": "百度网盘",
        "sourceType": "cloud",
        "sourcePage": "https://www.bilibili.com/opus/1038870701109411847",
        "summary": "Bilibili 模型分享动态，含网盘下载入口。",
        "licenseNotice": "许可未明确；角色与游戏素材权利需自行确认。",
    },
    {
        "id": "zeyin-bilibili",
        "name": "泽音 AI 语音 GPT-SoVITS 模型分享",
        "platform": "百度网盘",
        "sourceType": "cloud",
        "sourcePage": "https://www.bilibili.com/video/BV1fM9NYnEuc/",
        "summary": "社区模型分享视频及百度网盘路径。",
        "licenseNotice": "许可未明确；下载后请保留原帖信息并自行承担使用风险。",
    },
]


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


class HuggingFaceCatalogClient:
    def __init__(self, endpoint: str = HUGGING_FACE_API, timeout: int = 30):
        self.endpoint = endpoint
        self.timeout = timeout

    def models(self, query: str = "gpt-sovits", limit: int = 80) -> list[dict[str, Any]]:
        search = query.strip() or "gpt-sovits"
        params = urlencode({"search": search, "limit": max(1, min(limit, 100)), "full": "true"})
        request = Request(
            f"{self.endpoint}?{params}",
            headers={"Accept": "application/json", "User-Agent": "langbai-TTS-Studio/1.2"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                rows = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise CommunityCatalogError(f"Hugging Face 模型索引暂时不可用：{exc}") from exc
        result: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("private") or row.get("gated"):
                continue
            model_id = str(row.get("id") or row.get("modelId") or "").strip()
            if not model_id:
                continue
            siblings = row.get("siblings") if isinstance(row.get("siblings"), list) else []
            files = [str(item.get("rfilename") or "") for item in siblings if isinstance(item, dict)]
            gpt_count = sum(name.casefold().endswith(".ckpt") for name in files)
            sovits_count = sum(name.casefold().endswith(".pth") for name in files)
            audio_count = sum(Path(name).suffix.casefold() in {".wav", ".mp3", ".flac", ".ogg", ".m4a"} for name in files)
            tags = [str(tag) for tag in row.get("tags", []) if isinstance(tag, str)]
            license_tag = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), None)
            result.append({
                "id": hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:20],
                "name": model_id,
                "platform": "Hugging Face",
                "sourceType": "repository",
                "sourcePage": f"https://huggingface.co/{model_id}/tree/main",
                "gptWeights": gpt_count,
                "sovitsWeights": sovits_count,
                "audioFiles": audio_count,
                "likes": int(row.get("likes") or 0),
                "downloads": int(row.get("downloads") or 0),
                "lastModified": row.get("lastModified"),
                "license": license_tag,
                "readyPair": gpt_count > 0 and sovits_count > 0,
                "licenseNotice": f"仓库许可证：{license_tag}" if license_tag else "仓库未声明可识别的许可证，请查看模型卡。",
            })
        return sorted(result, key=lambda item: (not item["readyPair"], -item["likes"], item["name"].casefold()))


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    if not cleaned:
        cleaned = "community-model"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:80]}-{suffix}"


def _validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    trusted_host = hostname in ALLOWED_DOWNLOAD_HOSTS or any(pattern.fullmatch(hostname) for pattern in ALLOWED_DOWNLOAD_HOST_PATTERNS)
    if parsed.scheme != "https" or not trusted_host:
        raise CommunityCatalogError("社区目录返回了不受信任的下载地址，已拒绝下载")


class _TrustedDownloadRedirectHandler(HTTPRedirectHandler):
    """Reject an untrusted redirect before urllib connects to the target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    def __init__(self, root: str | Path, catalog: CommunityCatalogClient | None = None, hugging_face: HuggingFaceCatalogClient | None = None):
        self.root = Path(root).resolve()
        self.installed_root = self.root / "installed"
        self.cache_root = self.root / "cache"
        self.installed_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog or CommunityCatalogClient()
        self.hugging_face = hugging_face or HuggingFaceCatalogClient()
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

    def hugging_face_models(self, query: str = "gpt-sovits", limit: int = 80) -> list[dict[str, Any]]:
        return self.hugging_face.models(query, limit)

    def external_sources(self) -> list[dict[str, Any]]:
        return [dict(item) for item in CLOUD_MODEL_SOURCES]

    def default_scan_paths(self) -> list[str]:
        home = Path.home()
        candidates = [home / "Downloads", home / "Desktop", self.installed_root]
        return [str(path.resolve(strict=False)) for path in candidates if path.is_dir()]

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
            request = Request(download_url, headers={"User-Agent": "langbai-TTS-Studio/1.2"})
            opener = build_opener(_TrustedDownloadRedirectHandler())
            with opener.open(request, timeout=60) as response, archive_path.open("wb") as output:
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
            destination.parent.mkdir(parents=True, exist_ok=True)
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
