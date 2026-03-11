from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .models import GitHubError


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


load_dotenv()


API_ROOT = "https://api.github.com"
README_PATH = Path(os.getenv("PROFILE_STATS_README", "README.md"))
IMAGE_PATH = Path(os.getenv("PROFILE_STATS_IMAGE", "assets/activity-card.png"))
HTML_PREVIEW_PATH = Path(os.getenv("PROFILE_STATS_HTML_PREVIEW", "assets/activity-card-preview.html"))
REFERENCE_HTML_PATH = Path(os.getenv("PROFILE_STATS_REFERENCE_HTML", "assets/reference.html"))
REQUEST_TIMEOUT_SECONDS = 30
START_MARKER = "<!-- profile-stats:start -->"
END_MARKER = "<!-- profile-stats:end -->"

DEFAULT_CODE_EXTENSIONS = {
    ".asm",
    ".astro",
    ".bash",
    ".bat",
    ".c",
    ".cc",
    ".clj",
    ".cljs",
    ".cmake",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".dart",
    ".elm",
    ".erl",
    ".ex",
    ".exs",
    ".go",
    ".gql",
    ".graphql",
    ".groovy",
    ".h",
    ".hpp",
    ".hrl",
    ".hs",
    ".html",
    ".java",
    ".jl",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".less",
    ".lua",
    ".m",
    ".mm",
    ".nim",
    ".php",
    ".pl",
    ".proto",
    ".ps1",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".sass",
    ".scala",
    ".scss",
    ".sh",
    ".sol",
    ".sql",
    ".svelte",
    ".swift",
    ".tcl",
    ".tf",
    ".tsx",
    ".ts",
    ".vue",
    ".xml",
    ".yaml.tmpl",
    ".yml.tmpl",
    ".zig",
    ".zsh",
}

DEFAULT_CODE_FILENAMES = {
    "build",
    "build.bazel",
    "brewfile",
    "cmakelists.txt",
    "containerfile",
    "gemfile",
    "jenkinsfile",
    "justfile",
    "makefile",
    "meson.build",
    "podfile",
    "procfile",
    "rakefile",
    "tiltfile",
    "vagrantfile",
    "workspace",
}

LANGUAGE_BY_EXTENSION = {
    ".asm": "Assembly",
    ".astro": "Astro",
    ".bash": "Shell",
    ".bat": "Batchfile",
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cljs": "ClojureScript",
    ".cmake": "CMake",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".cxx": "C++",
    ".dart": "Dart",
    ".elm": "Elm",
    ".erl": "Erlang",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".go": "Go",
    ".gql": "GraphQL",
    ".graphql": "GraphQL",
    ".groovy": "Groovy",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".hrl": "Erlang",
    ".hs": "Haskell",
    ".html": "HTML",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".less": "Less",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".nim": "Nim",
    ".php": "PHP",
    ".pl": "Perl",
    ".proto": "Protocol Buffers",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sass": "Sass",
    ".scala": "Scala",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".tcl": "Tcl",
    ".tf": "Terraform",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".xml": "XML",
    ".yaml.tmpl": "YAML Template",
    ".yml.tmpl": "YAML Template",
    ".zig": "Zig",
    ".zsh": "Shell",
}

LANGUAGE_BY_FILENAME = {
    "build": "Starlark",
    "build.bazel": "Starlark",
    "workspace": "Starlark",
    "cmakelists.txt": "CMake",
    "makefile": "Makefile",
    "dockerfile": "Dockerfile",
    "containerfile": "Dockerfile",
    "gemfile": "Ruby",
    "rakefile": "Ruby",
    "podfile": "Ruby",
    "procfile": "Procfile",
    "justfile": "Just",
    "tiltfile": "Starlark",
    "jenkinsfile": "Groovy",
    "brewfile": "Ruby",
    "vagrantfile": "Ruby",
    "meson.build": "Meson",
}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise GitHubError(f"{name} must be an integer, got {raw!r}") from exc


def export_scale() -> int:
    value = env_int("PROFILE_STATS_EXPORT_SCALE", 3)
    if value < 1:
        raise GitHubError(f"PROFILE_STATS_EXPORT_SCALE must be at least 1, got {value!r}")
    return value


def env_csv_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def excluded_repos() -> set[str]:
    return env_csv_set("PROFILE_STATS_EXCLUDED_REPOS")


@lru_cache(maxsize=1)
def code_extensions() -> set[str]:
    configured = env_csv_set("PROFILE_STATS_CODE_EXTENSIONS")
    return configured or DEFAULT_CODE_EXTENSIONS


@lru_cache(maxsize=1)
def code_filenames() -> set[str]:
    configured = env_csv_set("PROFILE_STATS_CODE_FILENAMES")
    return configured or DEFAULT_CODE_FILENAMES


def is_code_file(path: str) -> bool:
    normalized = path.strip().lower()
    if not normalized:
        return False

    filename = normalized.rsplit("/", 1)[-1]
    if filename in code_filenames():
        return True
    if filename == "dockerfile" or filename.startswith("dockerfile."):
        return True

    suffixes = Path(filename).suffixes
    if not suffixes:
        return False

    joined_suffixes = "".join(suffixes)
    return joined_suffixes in code_extensions() or suffixes[-1] in code_extensions()


def detect_language(path: str) -> str | None:
    normalized = path.strip().lower()
    if not normalized:
        return None

    filename = normalized.rsplit("/", 1)[-1]
    if filename in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[filename]
    if filename.endswith(".cmake"):
        return "CMake"
    if filename.startswith("dockerfile."):
        return "Dockerfile"

    suffixes = Path(filename).suffixes
    joined_suffixes = "".join(suffixes)
    extension = joined_suffixes if joined_suffixes in code_extensions() else (suffixes[-1] if suffixes else "")
    return LANGUAGE_BY_EXTENSION.get(extension)
