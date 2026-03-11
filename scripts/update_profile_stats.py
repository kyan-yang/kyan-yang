#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


API_ROOT = "https://api.github.com"
README_PATH = Path(os.getenv("PROFILE_STATS_README", "README.md"))
SVG_PATH = Path(os.getenv("PROFILE_STATS_SVG", "assets/activity-card.svg"))
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


@dataclass
class RepoStats:
    additions: int = 0
    deletions: int = 0
    commits: int = 0

    @property
    def changed(self) -> int:
        return self.additions + self.deletions


@dataclass
class CommitRecord:
    repo: str
    sha: str
    committed_at: datetime
    additions: int
    deletions: int

    @property
    def changed(self) -> int:
        return self.additions + self.deletions


@dataclass
class CollectedStats:
    per_repo: dict[str, RepoStats]
    per_language: dict[str, RepoStats]
    commits: list[CommitRecord]
    warnings: list[str]


@dataclass
class CommitSummary:
    additions: int = 0
    deletions: int = 0
    included_files: int = 0
    per_language: dict[str, RepoStats] = field(default_factory=dict)


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def start_of_utc_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def to_iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_int(value: int) -> str:
    return f"{value:,}"


def format_percent(value: float) -> str:
    return f"{value:.1f}%"


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise GitHubError(f"{name} must be an integer, got {raw!r}") from exc


def excluded_repos() -> set[str]:
    raw = os.getenv("PROFILE_STATS_EXCLUDED_REPOS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def env_csv_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def code_extensions() -> set[str]:
    configured = env_csv_set("PROFILE_STATS_CODE_EXTENSIONS")
    return configured or DEFAULT_CODE_EXTENSIONS


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
    if joined_suffixes in code_extensions():
        return True
    return suffixes[-1] in code_extensions()


def detect_language(path: str) -> str | None:
    normalized = path.strip().lower()
    if not normalized:
        return None

    filename = normalized.rsplit("/", 1)[-1]
    if filename in {"build", "build.bazel", "workspace"}:
        return "Starlark"
    if filename == "cmakelists.txt" or filename.endswith(".cmake"):
        return "CMake"
    if filename in {"makefile"}:
        return "Makefile"
    if filename in {"dockerfile", "containerfile"} or filename.startswith("dockerfile."):
        return "Dockerfile"
    if filename in {"gemfile", "rakefile", "podfile"}:
        return "Ruby"
    if filename == "procfile":
        return "Procfile"
    if filename == "justfile":
        return "Just"
    if filename == "tiltfile":
        return "Starlark"
    if filename == "jenkinsfile":
        return "Groovy"
    if filename == "brewfile":
        return "Ruby"
    if filename == "vagrantfile":
        return "Ruby"
    if filename == "meson.build":
        return "Meson"

    suffixes = Path(filename).suffixes
    joined_suffixes = "".join(suffixes)
    extension = joined_suffixes if joined_suffixes in code_extensions() else (suffixes[-1] if suffixes else "")

    extension_map = {
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
    return extension_map.get(extension)


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-stats-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def rate_limit_reset_message(headers: urllib.error.HTTPError.headers) -> str:
    reset_value = headers.get("x-ratelimit-reset")
    if not reset_value:
        return ""
    try:
        reset_at = datetime.fromtimestamp(int(reset_value), tz=timezone.utc)
    except (TypeError, ValueError):
        return ""
    return f" Rate limit resets at {reset_at.strftime('%Y-%m-%d %H:%M UTC')}."


def api_get(path: str, params: dict[str, object] | None = None) -> tuple[object, dict[str, str]]:
    query = ""
    if params:
        normalized = {key: value for key, value in params.items() if value not in (None, "")}
        query = urllib.parse.urlencode(normalized)
    url = f"{API_ROOT}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(url, headers=build_headers())

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload) if payload else None
            headers = {key.lower(): value for key, value in response.headers.items()}
            return data, headers
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        message = payload
        try:
            parsed = json.loads(payload)
            message = parsed.get("message", payload)
        except json.JSONDecodeError:
            pass

        if exc.code == 403 and exc.headers.get("x-ratelimit-remaining") == "0":
            hint = rate_limit_reset_message(exc.headers)
            if not os.getenv("GH_TOKEN", "").strip():
                hint += " Add PROFILE_STATS_TOKEN for authenticated requests."
            raise RateLimitError(f"GitHub API rate limit exceeded for {url}.{hint}") from exc

        raise GitHubError(f"GitHub API error {exc.code} for {url}: {message}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"Network error while calling {url}: {exc.reason}") from exc


def paginate(path: str, params: dict[str, object] | None = None, max_pages: int = 10) -> list[object]:
    items: list[object] = []
    for page in range(1, max_pages + 1):
        page_params = dict(params or {})
        page_params["page"] = page
        page_params["per_page"] = 100
        data, _ = api_get(path, page_params)
        if not isinstance(data, list):
            raise GitHubError(f"Expected list response from {path}, got {type(data).__name__}")
        if not data:
            break
        items.extend(data)
        if len(data) < 100:
            break
    return items


def infer_username() -> str:
    for key in ("GH_USERNAME", "GITHUB_REPOSITORY_OWNER"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    raise GitHubError("Set GH_USERNAME or run this workflow in your profile repository so GITHUB_REPOSITORY_OWNER is available.")


def require_token_in_actions() -> None:
    if os.getenv("GITHUB_ACTIONS") == "true" and not os.getenv("GH_TOKEN", "").strip():
        raise GitHubError("Missing GH_TOKEN. Add a repository secret named PROFILE_STATS_TOKEN before running this workflow.")


def recent_public_event_repos(username: str, window_start: datetime) -> dict[str, tuple[str, str]]:
    repos: dict[str, tuple[str, str]] = {}
    events = paginate(f"/users/{username}/events/public", max_pages=3)
    for event in events:
        if not isinstance(event, dict):
            continue
        event_time = parse_iso8601(event.get("created_at"))
        if event_time and event_time < window_start:
            continue
        repo_info = event.get("repo") or {}
        full_name = repo_info.get("name", "")
        if "/" not in full_name:
            continue
        owner, repo = full_name.split("/", 1)
        repos[full_name.lower()] = (owner, repo)
    return repos


def candidate_repositories(username: str, window_start: datetime) -> dict[str, tuple[str, str]]:
    candidates: dict[str, tuple[str, str]] = {}
    excluded = excluded_repos()
    has_token = bool(os.getenv("GH_TOKEN", "").strip())
    max_pages = env_int("PROFILE_STATS_MAX_REPO_PAGES", 10)

    if has_token:
        repos = paginate(
            "/user/repos",
            params={
                "affiliation": "owner,collaborator,organization_member",
                "sort": "pushed",
                "direction": "desc",
            },
            max_pages=max_pages,
        )
    else:
        repos = paginate(
            f"/users/{username}/repos",
            params={
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
            },
            max_pages=max_pages,
        )

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        full_name = str(repo.get("full_name", "")).strip()
        if not full_name or "/" not in full_name:
            continue
        if full_name.lower() in excluded:
            continue
        if repo.get("archived") or repo.get("disabled"):
            continue
        pushed_at = parse_iso8601(repo.get("pushed_at"))
        if pushed_at and pushed_at < window_start:
            continue
        owner, name = full_name.split("/", 1)
        candidates[full_name.lower()] = (owner, name)

    candidates.update(recent_public_event_repos(username, window_start))
    return candidates


def list_recent_commits(owner: str, repo: str, username: str, window_start: datetime, window_end: datetime) -> list[dict[str, object]]:
    commits: list[dict[str, object]] = []
    pages = paginate(
        f"/repos/{owner}/{repo}/commits",
        params={
            "author": username,
            "since": to_iso8601(window_start),
            "until": to_iso8601(window_end),
        },
        max_pages=env_int("PROFILE_STATS_MAX_COMMIT_PAGES", 10),
    )

    for item in pages:
        if isinstance(item, dict):
            commits.append(item)
    return commits


def commit_stats(owner: str, repo: str, sha: str) -> CommitSummary:
    details, _ = api_get(f"/repos/{owner}/{repo}/commits/{sha}")
    if not isinstance(details, dict):
        raise GitHubError(f"Expected commit details for {owner}/{repo}@{sha}")

    files = details.get("files") or []
    if not isinstance(files, list):
        raise GitHubError(f"Expected changed files for {owner}/{repo}@{sha}")

    summary = CommitSummary()
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        filename = str(file_info.get("filename", "")).strip()
        if not is_code_file(filename):
            continue
        additions = int(file_info.get("additions", 0))
        deletions = int(file_info.get("deletions", 0))
        summary.additions += additions
        summary.deletions += deletions
        summary.included_files += 1

        language = detect_language(filename) or "Other"
        language_stats = summary.per_language.setdefault(language, RepoStats())
        language_stats.additions += additions
        language_stats.deletions += deletions

    return summary


def extract_commit_datetime(commit: dict[str, object]) -> datetime:
    commit_info = commit.get("commit") or {}
    if isinstance(commit_info, dict):
        author = commit_info.get("author") or {}
        if isinstance(author, dict):
            authored_at = parse_iso8601(author.get("date"))
            if authored_at:
                return authored_at
        committer = commit_info.get("committer") or {}
        if isinstance(committer, dict):
            committed_at = parse_iso8601(committer.get("date"))
            if committed_at:
                return committed_at
    return now_utc()


def collect_stats(username: str, window_start: datetime, window_end: datetime) -> CollectedStats:
    repos = candidate_repositories(username, window_start)
    per_repo: dict[str, RepoStats] = defaultdict(RepoStats)
    per_language: dict[str, RepoStats] = defaultdict(RepoStats)
    commit_records: list[CommitRecord] = []
    seen_commits: set[str] = set()
    warnings: list[str] = []

    for owner, repo in repos.values():
        full_name = f"{owner}/{repo}"
        try:
            commits = list_recent_commits(owner, repo, username, window_start, window_end)
        except GitHubError as exc:
            warnings.append(f"Skipped {full_name}: {exc}")
            continue

        for commit in commits:
            sha = str(commit.get("sha", "")).strip()
            if not sha:
                continue

            commit_key = f"{full_name}:{sha}"
            if commit_key in seen_commits:
                continue
            seen_commits.add(commit_key)

            summary = commit_stats(owner, repo, sha)
            if summary.included_files == 0:
                continue
            committed_at = extract_commit_datetime(commit)

            stats = per_repo[full_name]
            stats.additions += summary.additions
            stats.deletions += summary.deletions
            stats.commits += 1
            for language, language_stats in summary.per_language.items():
                aggregate = per_language[language]
                aggregate.additions += language_stats.additions
                aggregate.deletions += language_stats.deletions
                aggregate.commits += 1

            commit_records.append(
                CommitRecord(
                    repo=full_name,
                    sha=sha,
                    committed_at=committed_at,
                    additions=summary.additions,
                    deletions=summary.deletions,
                )
            )

    return CollectedStats(
        per_repo=dict(per_repo),
        per_language=dict(per_language),
        commits=commit_records,
        warnings=warnings,
    )


def render_coverage_note() -> str:
    if os.getenv("GH_TOKEN", "").strip():
        return "Coverage includes code-file changes in public repositories plus any additional repositories the workflow token can read."
    return "Coverage is limited to code-file changes in public activity. Add PROFILE_STATS_TOKEN to include private and collaborator repositories."


def last_n_dates(window_end: datetime, window_days: int) -> list[date]:
    final_day = window_end.astimezone(timezone.utc).date()
    return [final_day - timedelta(days=offset) for offset in range(window_days - 1, -1, -1)]


def daily_rollup(commits: list[CommitRecord], window_end: datetime, window_days: int) -> list[tuple[date, RepoStats]]:
    days = last_n_dates(window_end, window_days)
    rollup: dict[date, RepoStats] = {day: RepoStats() for day in days}

    for commit in commits:
        day = commit.committed_at.astimezone(timezone.utc).date()
        if day not in rollup:
            continue
        stats = rollup[day]
        stats.additions += commit.additions
        stats.deletions += commit.deletions
        stats.commits += 1

    return [(day, rollup[day]) for day in days]


def render_daily_chart(commits: list[CommitRecord], window_end: datetime, window_days: int) -> str:
    rows = daily_rollup(commits, window_end, window_days)
    max_changed = max((stats.changed for _, stats in rows), default=0)
    width = 18
    lines: list[str] = []

    for day, stats in rows:
        if stats.changed == 0 or max_changed == 0:
            bar = "."
        else:
            filled = max(1, round((stats.changed / max_changed) * width))
            bar = "#" * filled
        commit_label = f"{stats.commits} commit{'s' if stats.commits != 1 else ''}"
        lines.append(
            f"{day.strftime('%m-%d')} | {bar:<18} {format_int(stats.changed):>7} code lines | {commit_label}"
        )

    return "\n".join(lines)


def top_languages(per_language: dict[str, RepoStats], limit: int = 8) -> list[tuple[str, RepoStats]]:
    return sorted(
        per_language.items(),
        key=lambda item: (item[1].changed, item[1].additions, item[0]),
        reverse=True,
    )[:limit]


def render_activity_card(window_days: int, collected: CollectedStats, window_end: datetime) -> str:
    per_language = collected.per_language
    commits = collected.commits
    language_rows = top_languages(per_language)
    total_changed = sum(stats.changed for stats in per_language.values())
    total_commits = len(commits)
    repo_count = len(collected.per_repo)
    active_days = len({commit.committed_at.astimezone(timezone.utc).date() for commit in commits})
    updated_label = window_end.strftime("%b %d, %Y")

    width = 1120
    row_height = 54
    bar_width = 300
    summary_y = 178
    summary_height = 86
    languages_start_y = 324
    footer_height = 70
    rows = max(len(language_rows), 1)
    height = languages_start_y + (rows * row_height) + footer_height

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" role="img" aria-labelledby="title desc">',
        "<title id=\"title\">Weekly code activity</title>",
        "<desc id=\"desc\">Generated language breakdown and weekly code activity summary.</desc>",
        "<defs>",
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0%" stop-color="#09111f"/>',
        '<stop offset="100%" stop-color="#0f1f36"/>',
        "</linearGradient>",
        '<linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">',
        '<stop offset="0%" stop-color="#71e5ff"/>',
        '<stop offset="100%" stop-color="#5b8cff"/>',
        "</linearGradient>",
        "</defs>",
        '<rect x="1" y="1" width="1118" height="{height_minus}" rx="28" fill="url(#bg)" stroke="#273449"/>'.replace(
            "{height_minus}", str(height - 2)
        ),
        '<text x="52" y="60" fill="#8fa7c6" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="18">Generated from GitHub commits</text>',
        '<text x="52" y="108" fill="#f5f7fb" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="42" font-weight="700">Weekly Code Activity</text>',
        f'<text x="52" y="144" fill="#8fa7c6" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="20">Last {window_days} calendar days, including today • updated {xml_escape(updated_label)}</text>',
    ]

    summary = [
        ("Code lines", format_int(total_changed)),
        ("Commits", format_int(total_commits)),
        ("Repos", format_int(repo_count)),
        ("Active days", f"{active_days}/{window_days}"),
    ]
    metric_x = 52
    for label, value in summary:
        svg.extend(
            [
                f'<rect x="{metric_x}" y="{summary_y}" width="220" height="{summary_height}" rx="18" fill="#111c2f" stroke="#243349"/>',
                f'<text x="{metric_x + 20}" y="{summary_y + 34}" fill="#8fa7c6" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="16">{xml_escape(label)}</text>',
                f'<text x="{metric_x + 20}" y="{summary_y + 69}" fill="#f5f7fb" font-family="SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace" font-size="28" font-weight="700">{xml_escape(value)}</text>',
            ]
        )
        metric_x += 240

    if not language_rows:
        empty_y = languages_start_y + 8
        svg.extend(
            [
                f'<text x="52" y="{empty_y}" fill="#f5f7fb" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="28" font-weight="600">No code-file changes in this window.</text>',
                f'<text x="52" y="{empty_y + 38}" fill="#8fa7c6" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="18">The card will populate after your next code commit.</text>',
            ]
        )
    else:
        start_y = languages_start_y
        for index, (language, stats) in enumerate(language_rows):
            y = start_y + (index * row_height)
            share = (stats.changed / total_changed) if total_changed else 0
            filled = max(8, round(bar_width * share))
            if stats.changed == 0:
                filled = 0

            svg.extend(
                [
                    f'<text x="52" y="{y}" fill="#f5f7fb" font-family="SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace" font-size="24">{xml_escape(language)}</text>',
                    f'<text x="330" y="{y}" fill="#d6dfeb" font-family="SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace" font-size="24">{xml_escape(format_int(stats.changed))} lines</text>',
                    f'<rect x="610" y="{y - 22}" width="{bar_width}" height="18" rx="9" fill="#1d2b41"/>',
                    f'<rect x="610" y="{y - 22}" width="{filled}" height="18" rx="9" fill="url(#accent)"/>',
                    f'<text x="940" y="{y}" fill="#f5f7fb" font-family="SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace" font-size="24">{xml_escape(format_percent(share * 100))}</text>',
                ]
            )

    footer_y = height - 28
    svg.extend(
        [
            f'<text x="52" y="{footer_y}" fill="#6f87a7" font-family="Inter, Segoe UI, Arial, sans-serif" font-size="16">Only code files are counted. Language detection is inferred from changed filenames and extensions.</text>',
            "</svg>",
        ]
    )
    return "\n".join(svg)


def busiest_day(commits: list[CommitRecord], window_end: datetime, window_days: int) -> tuple[date, RepoStats] | None:
    rows = daily_rollup(commits, window_end, window_days)
    active_rows = [(day, stats) for day, stats in rows if stats.commits]
    if not active_rows:
        return None
    return max(active_rows, key=lambda item: (item[1].changed, item[1].commits, item[0]))


def largest_commit(commits: list[CommitRecord]) -> CommitRecord | None:
    if not commits:
        return None
    return max(commits, key=lambda item: (item.changed, item.additions, item.repo, item.sha))


def render_stats(
    username: str,
    window_start: datetime,
    window_end: datetime,
    window_days: int,
    collected: CollectedStats,
) -> str:
    per_repo = collected.per_repo
    commits = collected.commits
    total_additions = sum(repo.additions for repo in per_repo.values())
    total_deletions = sum(repo.deletions for repo in per_repo.values())
    total_commits = sum(repo.commits for repo in per_repo.values())
    total_changed = total_additions + total_deletions
    repo_count = len(per_repo)
    active_days = len({commit.committed_at.astimezone(timezone.utc).date() for commit in commits})
    average_changed = round(total_changed / total_commits) if total_commits else 0
    average_active_day = round(total_changed / active_days) if active_days else 0
    updated_at = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    net_delta = total_additions - total_deletions
    coverage_note = render_coverage_note()
    svg_reference = "./assets/activity-card.svg"

    lines = [
        "## Activity Dashboard",
        "",
        '<p align="center">',
        f'  <img src="{svg_reference}" alt="Weekly code activity card" width="100%" />',
        "</p>",
        "",
    ]

    if total_commits == 0:
        lines.extend(
            [
                f"<sub>Updated {updated_at}</sub>",
                "",
                f"No code-file commits found for `{username}` between {window_start.date()} and {window_end.date()}.",
                "",
                f"> {coverage_note}",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "<details>",
            "<summary>Open raw weekly breakdown</summary>",
            "",
            f"<sub>Updated {updated_at}</sub>",
            "",
            f"- Added: +{format_int(total_additions)}",
            f"- Deleted: -{format_int(total_deletions)}",
            f"- Net delta: {'+' if net_delta >= 0 else '-'}{format_int(abs(net_delta))}",
            f"- Code-touching commits: {format_int(total_commits)}",
            f"- Repositories touched: {format_int(repo_count)}",
            f"- Active days: {format_int(active_days)} / {format_int(window_days)}",
            f"- Average code lines per commit: {format_int(average_changed)}",
            f"- Average code lines per active day: {format_int(average_active_day)}",
            "",
            "### Daily Throughput",
            "",
            "```text",
            render_daily_chart(commits, window_end, window_days),
            "```",
            "",
            "### Highlights",
            "",
        ]
    )

    busiest = busiest_day(commits, window_end, window_days)
    if busiest:
        day, stats = busiest
        lines.append(
            f"- Busiest day: `{day.isoformat()}` with {format_int(stats.changed)} code lines changed across {format_int(stats.commits)} commit{'s' if stats.commits != 1 else ''}"
        )

    biggest = largest_commit(commits)
    if biggest:
        lines.append(
            f"- Largest commit: `{biggest.repo}@{biggest.sha[:7]}` with +{format_int(biggest.additions)} / -{format_int(biggest.deletions)}"
        )

    top_repos = sorted(
        per_repo.items(),
        key=lambda item: (item[1].changed, item[1].commits, item[0]),
        reverse=True,
    )[:5]
    lines.extend(
        [
            "",
            "### Top Repositories",
            "",
            "| Repository | Code lines | Added | Deleted | Commits |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )

    for full_name, stats in top_repos:
        lines.append(
            f"| `{full_name}` | {format_int(stats.changed)} | +{format_int(stats.additions)} | -{format_int(stats.deletions)} | {format_int(stats.commits)} |"
        )

    lines.extend(
        [
            "",
            f"> {coverage_note}",
            "",
            "</details>",
        ]
    )
    return "\n".join(lines)


def replace_stats_block(readme: str, block: str) -> str:
    replacement = f"{START_MARKER}\n{block}\n{END_MARKER}"
    if START_MARKER in readme and END_MARKER in readme:
        before, remainder = readme.split(START_MARKER, 1)
        _, after = remainder.split(END_MARKER, 1)
        return f"{before}{replacement}{after}"
    if not readme.endswith("\n"):
        readme += "\n"
    return f"{readme}\n{replacement}\n"


def main() -> int:
    require_token_in_actions()
    username = infer_username()
    window_days = env_int("PROFILE_STATS_WINDOW_DAYS", 7)
    window_end = now_utc()
    window_start = start_of_utc_day(window_end.date() - timedelta(days=window_days - 1))

    collected = collect_stats(username, window_start, window_end)
    if collected.warnings:
        preview = "\n".join(collected.warnings[:5])
        print(f"Skipped some repositories:\n{preview}", file=sys.stderr)

    block = render_stats(username, window_start, window_end, window_days, collected)
    activity_card = render_activity_card(window_days, collected, window_end)

    if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() == "1":
        print(block)
        return 0

    if not README_PATH.exists():
        raise GitHubError(f"README file not found at {README_PATH}")

    current = README_PATH.read_text(encoding="utf-8")
    updated = replace_stats_block(current, block)
    SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(activity_card, encoding="utf-8")
    README_PATH.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
