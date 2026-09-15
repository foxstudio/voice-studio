#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {
    ".aac",
    ".bin",
    ".ckpt",
    ".db",
    ".flac",
    ".gguf",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".npy",
    ".npz",
    ".ogg",
    ".onnx",
    ".pcm",
    ".pt",
    ".pth",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".webm",
}
FORBIDDEN_PARTS = {
    "local-overrides",
    "outputs",
    "uploads",
    "weights",
}
RUNTIME_TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svelte",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}
POSIX_USER_HOME_PREFIX = "/" + "Users" + "/"
LOCAL_PATH_PATTERNS = (
    re.compile(
        re.escape(POSIX_USER_HOME_PREFIX)
        + r"(?!example(?:/|$))[A-Za-z0-9._-]+/"
    ),
    re.compile(r"[A-Za-z]:\\Users\\(?!example(?:\\|$))[A-Za-z0-9._-]+\\"),
)
SECRET_PATTERNS = (
    ("openai_style_secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt_bearer_token", re.compile(r"Bearer\s+eyJ[A-Za-z0-9_-]{16,}")),
)
REQUIRED_RELEASE_FILES = frozenset(
    {
        "CONTRIBUTING.md",
        "LICENSE",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/engines/mlx-indextts-source-provenance.md",
        "third_party_licenses/3D-Speaker-Apache-2.0.txt",
        "third_party_licenses/Amphion-MIT.txt",
        "third_party_licenses/BigVGAN-MIT.txt",
        "third_party_licenses/IndexTTS1-Apache-2.0.txt",
        "third_party_licenses/IndexTTS2-bilibili-model-use-license.txt",
        "third_party_licenses/MLX-IndexTTS-MIT.txt",
    }
)


@dataclass(frozen=True)
class ReleaseIssue:
    code: str
    path: str
    detail: str


def tracked_files(root: Path = PROJECT_ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [
        root / item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def audit_release_files(
    files: Iterable[Path],
    *,
    root: Path = PROJECT_ROOT,
    require_governance: bool = False,
) -> list[ReleaseIssue]:
    issues: list[ReleaseIssue] = []
    release_files = list(files)
    if require_governance:
        tracked_names = {
            PurePosixPath(path.relative_to(root).as_posix()).as_posix()
            for path in release_files
        }
        for required in sorted(REQUIRED_RELEASE_FILES - tracked_names):
            issues.append(
                ReleaseIssue(
                    "missing_release_governance",
                    required,
                    "开源发布缺少必要的许可、安全或贡献治理文件",
                )
            )
    for path in release_files:
        relative = PurePosixPath(path.relative_to(root).as_posix())
        relative_text = relative.as_posix()
        if _forbidden_repository_path(relative):
            issues.append(
                ReleaseIssue(
                    "forbidden_artifact",
                    relative_text,
                    "模型、媒体、数据库或运行产物不能进入开源仓库",
                )
            )
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _is_runtime_text(relative):
            if not _is_test_path(relative):
                for code, pattern in SECRET_PATTERNS:
                    if pattern.search(text):
                        issues.append(
                            ReleaseIssue(code, relative_text, "检测到疑似真实密钥或令牌")
                        )
            if not _is_release_gate_fixture(relative):
                for pattern in LOCAL_PATH_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        issues.append(
                            ReleaseIssue(
                                "personal_absolute_path",
                                relative_text,
                                f"检测到个人绝对路径：{match.group(0)}",
                            )
                        )
                        break
    return sorted(issues, key=lambda issue: (issue.path, issue.code))


def _forbidden_repository_path(path: PurePosixPath) -> bool:
    if path.name == ".env" or (
        path.name.startswith(".env.")
        and not path.name.endswith(".example")
        and path.name != ".env.test"
    ):
        return True
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return True
    parts = set(path.parts)
    if parts & FORBIDDEN_PARTS:
        return True
    return "models" in parts and path.suffix.lower() not in {".md", ".py", ".json"}


def _is_runtime_text(path: PurePosixPath) -> bool:
    return path.suffix.lower() in RUNTIME_TEXT_SUFFIXES


def _is_test_path(path: PurePosixPath) -> bool:
    return bool(path.parts and path.parts[0] == "tests")


def _is_release_gate_fixture(path: PurePosixPath) -> bool:
    return path.as_posix() == "tests/test_open_source_release_gate.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查开源发布中是否混入模型、用户数据、密钥或个人绝对路径",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    files = tracked_files()
    issues = audit_release_files(files, require_governance=True)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not issues,
                    "tracked_files": len(files),
                    "issues": [asdict(issue) for issue in issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif issues:
        print("开源发布检查失败：")
        for issue in issues:
            print(f"- [{issue.code}] {issue.path}: {issue.detail}")
    else:
        print(
            "开源发布检查通过：必要治理文件齐全，未发现受管模型、用户数据、"
            "密钥或个人绝对路径。人工许可兼容性仍以 THIRD_PARTY_NOTICES.md 为准。"
        )
    return 1 if issues else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
