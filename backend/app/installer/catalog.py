"""Pinned, official-only installation catalog verified on 2026-07-19."""

INSTALLER_CATALOG = {
    "indextts2": {
        "id": "indextts2",
        "name": "IndexTTS2",
        "directory_name": "indextts2",
        "source_url": "https://codeload.github.com/index-tts/index-tts/zip/13495845e3028f0bb6ca1462ad22aa0e76349e40",
        "source_repo_url": "https://github.com/index-tts/index-tts",
        "revision": "13495845e3028f0bb6ca1462ad22aa0e76349e40",
        "source_commit": "13495845e3028f0bb6ca1462ad22aa0e76349e40",
        "branch": "main",
        "sha256": "7ed8bc742e2eeeb83f922247ef0e27f96327f418acacb6c63f182cafd66887ba",
        "verification": "pinned_commit_archive_sha256",
        "code_license": "Bilibili IndexTTS Model Use License",
        "code_license_url": "https://github.com/index-tts/index-tts/blob/13495845e3028f0bb6ca1462ad22aa0e76349e40/LICENSE",
        "license_requires_acceptance": True,
        "python": "3.11.13",
        "environment_method": "uv sync --extra webui --frozen --no-install-project",
        "estimated_source_bytes": 33_284_648,
        "estimated_environment_bytes": 15_000_000_000,
        "models": [
            {
                "id": "indextts2-official",
                "name": "IndexTTS-2 官方权重",
                "provider": "huggingface",
                "repo_id": "IndexTeam/IndexTTS-2",
                "revision": "740dcaff396282ffb241903d150ac011cd4b1ede",
                "sha256": None,
                "verification": "pinned_revision_and_content_manifest",
                "license": "Bilibili IndexTTS Model Use License",
                "license_url": "https://huggingface.co/IndexTeam/IndexTTS-2/blob/740dcaff396282ffb241903d150ac011cd4b1ede/LICENSE.txt",
                "estimated_download_bytes": 5_000_000_000,
                "estimated_installed_bytes": 5_500_000_000,
            }
        ],
    },
    "voxcpm": {
        "id": "voxcpm",
        "name": "VoxCPM2",
        "directory_name": "VoxCPM",
        "source_url": "https://codeload.github.com/OpenBMB/VoxCPM/zip/616d3d3e630a9c96c2853250eef91b0f39dcd5fa",
        "source_repo_url": "https://github.com/OpenBMB/VoxCPM",
        "revision": "616d3d3e630a9c96c2853250eef91b0f39dcd5fa",
        "source_commit": "616d3d3e630a9c96c2853250eef91b0f39dcd5fa",
        "branch": "main",
        "sha256": "131acb3c4741e63bcc33cfa5499f3ccaa3eb58bc00d352721a656a9ca12e448f",
        "verification": "pinned_commit_archive_sha256",
        "code_license": "Apache-2.0",
        "code_license_url": "https://github.com/OpenBMB/VoxCPM/blob/616d3d3e630a9c96c2853250eef91b0f39dcd5fa/LICENSE",
        "license_requires_acceptance": True,
        "python": "3.11.13",
        "environment_method": "uv venv + uv pip install from pinned source",
        "estimated_source_bytes": 4_137_130,
        "estimated_environment_bytes": 16_000_000_000,
        "models": [
            {
                "id": "voxcpm2-official",
                "name": "VoxCPM2 官方权重",
                "provider": "huggingface",
                "repo_id": "openbmb/VoxCPM2",
                "revision": "bffb3df5a29440629464e5e839f4d214c8714c3d",
                "sha256": None,
                "verification": "pinned_revision_and_content_manifest",
                "license": "Apache-2.0",
                "license_url": "https://huggingface.co/openbmb/VoxCPM2/tree/bffb3df5a29440629464e5e839f4d214c8714c3d",
                "estimated_download_bytes": 8_000_000_000,
                "estimated_installed_bytes": 8_500_000_000,
            }
        ],
    },
    "gpt_sovits": {
        "id": "gpt_sovits",
        "name": "GPT-SoVITS",
        "directory_name": "GPT-SoVITS",
        "source_url": "https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2",
        "source_repo_url": "https://github.com/RVC-Boss/GPT-SoVITS",
        "revision": "be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2",
        "source_commit": "be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2",
        "branch": "main",
        "sha256": "d16ddb222ef573d122a7ce16816bfe9c7536dc51acaae59422f8835617e47026",
        "verification": "pinned_commit_archive_sha256",
        "code_license": "MIT",
        "code_license_url": "https://github.com/RVC-Boss/GPT-SoVITS/blob/be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2/LICENSE",
        "license_requires_acceptance": True,
        "python": "3.10.18",
        "environment_method": "official manual install: extra-req.txt then requirements.txt",
        "estimated_source_bytes": 6_717_467,
        "estimated_environment_bytes": 18_000_000_000,
        "models": [
            {
                "id": "gpt-sovits-official",
                "name": "GPT-SoVITS 官方预训练权重",
                "provider": "huggingface",
                "repo_id": "lj1995/GPT-SoVITS",
                "revision": "336b2ec4e8d4ac74740798dd40af44e74659ecaf",
                "sha256": None,
                "verification": "pinned_revision_and_content_manifest",
                "license": "MIT",
                "license_url": "https://huggingface.co/lj1995/GPT-SoVITS/tree/336b2ec4e8d4ac74740798dd40af44e74659ecaf",
                "estimated_download_bytes": 8_000_000_000,
                "estimated_installed_bytes": 9_000_000_000,
            }
        ],
    },
}

MANAGED_TOOL_CATALOG = {
    "uv": {
        "id": "uv",
        "name": "Astral uv",
        "version": "0.11.29",
        "platform": "x86_64-pc-windows-msvc",
        "archive_url": "https://releases.astral.sh/github/uv/releases/download/0.11.29/uv-x86_64-pc-windows-msvc.zip",
        "checksum_url": "https://releases.astral.sh/github/uv/releases/download/0.11.29/uv-x86_64-pc-windows-msvc.zip.sha256",
        "sha256": "a047d55651bc3e0ca24595b25ec4cfcb10f9dca9fb56514e661269b37d4fae68",
        "archive_bytes": 25_534_683,
        "executables": ["uv.exe", "uvx.exe"],
        "license": "Apache-2.0 OR MIT",
        "license_url": "https://github.com/astral-sh/uv/tree/0.11.29#license",
        "source_page": "https://github.com/astral-sh/uv/releases/tag/0.11.29",
    },
    "ffmpeg": {
        "id": "ffmpeg",
        "name": "FFmpeg 8.1.2 essentials (gyan.dev Windows build)",
        "version": "8.1.2",
        "platform": "windows-x64",
        "archive_url": "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip",
        "checksum_url": "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip.sha256",
        "sha256": "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
        "archive_bytes": 109_728_040,
        "executables": ["bin/ffmpeg.exe", "bin/ffprobe.exe"],
        "license": "GPLv3 (gyan.dev build)",
        "license_url": "https://www.gyan.dev/ffmpeg/builds/#about-these-builds",
        "source_page": "https://ffmpeg.org/download.html#build-windows",
    },
}

# The model downloader is executed by the managed uv binary.  Its exact
# version is pinned so an installation never resolves a floating CLI release.
MODEL_DOWNLOAD_TOOL = {
    "package": "huggingface-hub[cli,hf_xet]==1.24.0",
    "source_page": "https://pypi.org/project/huggingface-hub/1.24.0/",
}

MANAGED_PYTHON_LICENSE = {
    "id": "cpython",
    "name": "CPython（由 uv 管理安装）",
    "license": "Python Software Foundation License Version 2",
    "license_url": "https://docs.python.org/3/license.html",
    "source_page": "https://docs.astral.sh/uv/guides/install-python/",
}

ENGINE_TOOL_REQUIREMENTS = {
    "indextts2": ["uv"],
    "voxcpm": ["uv"],
    "gpt_sovits": ["uv", "ffmpeg"],
}

OFFICIAL_SOURCE_URLS = frozenset(item["source_url"] for item in INSTALLER_CATALOG.values())
OFFICIAL_MODEL_REPOS = frozenset(
    model["repo_id"] for item in INSTALLER_CATALOG.values() for model in item["models"]
)
