#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


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
GIF_PATH = Path(os.getenv("PROFILE_STATS_GIF", "assets/activity-card.gif"))
HTML_PREVIEW_PATH = Path(os.getenv("PROFILE_STATS_HTML_PREVIEW", "assets/activity-card-preview.html"))
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
class TemporalCommit:
    repo: str
    sha: str
    committed_at: datetime


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


@dataclass
class TemporalCell:
    day: date
    count: int
    level: int


@dataclass
class TemporalMetrics:
    start_day: date
    end_day: date
    weeks: int
    cells: list[TemporalCell]
    peak_velocity: int
    consistency_score: float
    consistency_label: str
    streak_weeks: int
    temporal_bias: str
    observed_clock: str


@dataclass
class WeeklySummary:
    window_days: int
    total_additions: int
    total_deletions: int
    net_delta: int
    total_changed: int
    total_commits: int
    repo_count: int
    active_days: int
    average_changed: int
    average_active_day: int


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


def collect_temporal_commits(username: str, window_start: datetime, window_end: datetime) -> tuple[list[TemporalCommit], list[str]]:
    repos = candidate_repositories(username, window_start)
    commit_records: list[TemporalCommit] = []
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

            commit_records.append(
                TemporalCommit(
                    repo=full_name,
                    sha=sha,
                    committed_at=extract_commit_datetime(commit),
                )
            )

    commit_records.sort(key=lambda item: item.committed_at)
    return commit_records, warnings


def classify_temporal_bias(hours: list[int]) -> tuple[str, str]:
    if not any(hours):
        return "Undetermined", "UTC BASELINE"

    peak_hour = max(range(24), key=lambda hour: (hours[hour], -hour))
    if peak_hour >= 21 or peak_hour < 5:
        bias = "Nocturnal"
    elif 5 <= peak_hour < 10:
        bias = "Matinal"
    elif 10 <= peak_hour < 17:
        bias = "Diurnal"
    else:
        bias = "Crepuscular"

    return bias, f"PEAK {peak_hour:02d}:00 UTC"


def consistency_label(score: float) -> str:
    if score >= 0.92:
        return "Alpha"
    if score >= 0.8:
        return "Beta"
    if score >= 0.65:
        return "Gamma"
    if score >= 0.45:
        return "Delta"
    return "Epsilon"


def intensity_levels(counts: list[int]) -> list[int]:
    non_zero = sorted(count for count in counts if count > 0)
    if not non_zero:
        return [0 for _ in counts]

    if len(non_zero) == 1:
        return [4 if count else 0 for count in counts]

    def percentile(fraction: float) -> int:
        index = min(len(non_zero) - 1, max(0, round((len(non_zero) - 1) * fraction)))
        return non_zero[index]

    low = percentile(0.25)
    mid = percentile(0.5)
    high = percentile(0.8)
    levels: list[int] = []

    for count in counts:
        if count <= 0:
            levels.append(0)
        elif count >= high:
            levels.append(4)
        elif count >= mid:
            levels.append(3)
        elif count >= low:
            levels.append(2)
        else:
            levels.append(1)

    return levels


def build_temporal_metrics(commits: list[TemporalCommit], window_end: datetime, weeks: int) -> TemporalMetrics:
    end_day = window_end.astimezone(timezone.utc).date()

    # Align to GitHub-style Sun-Sat weeks.
    # isoweekday(): Mon=1 .. Sun=7; convert so Sun=0 .. Sat=6.
    end_weekday = end_day.isoweekday() % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    week_end_saturday = end_day + timedelta(days=6 - end_weekday)
    start_day = week_end_saturday - timedelta(days=weeks * 7 - 1)  # always a Sunday
    total_days = weeks * 7

    daily_counts: dict[date, int] = {
        start_day + timedelta(days=offset): 0 for offset in range(total_days)
    }
    hours = [0 for _ in range(24)]

    for commit in commits:
        committed_at = commit.committed_at.astimezone(timezone.utc)
        committed_day = committed_at.date()
        if committed_day not in daily_counts:
            continue
        daily_counts[committed_day] += 1
        hours[committed_at.hour] += 1

    days = [start_day + timedelta(days=offset) for offset in range(total_days)]
    counts = [daily_counts[day] for day in days]

    # Only compute intensity levels for past/present days; future days get level -1.
    past_counts = [c for d, c in zip(days, counts) if d <= end_day]
    past_levels = intensity_levels(past_counts)
    level_iter = iter(past_levels)
    all_levels = [next(level_iter) if d <= end_day else -1 for d in days]

    # Weekly aggregates (only count past days for each week).
    weekly_counts = [
        sum(c for d, c in zip(days[i : i + 7], counts[i : i + 7]) if d <= end_day)
        for i in range(0, len(counts), 7)
    ]
    active_weeks = sum(1 for count in weekly_counts if count > 0)
    streak = 0
    for count in reversed(weekly_counts):
        if count <= 0:
            break
        streak += 1

    bias, observed_clock = classify_temporal_bias(hours)
    score = active_weeks / weeks if weeks else 0.0

    return TemporalMetrics(
        start_day=start_day,
        end_day=end_day,
        weeks=weeks,
        cells=[
            TemporalCell(day=day, count=count, level=level)
            for day, count, level in zip(days, counts, all_levels)
        ],
        peak_velocity=max(weekly_counts, default=0),
        consistency_score=score,
        consistency_label=consistency_label(score),
        streak_weeks=streak,
        temporal_bias=bias,
        observed_clock=observed_clock,
    )


def build_weekly_summary(collected: CollectedStats, window_days: int) -> WeeklySummary:
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
    net_delta = total_additions - total_deletions
    return WeeklySummary(
        window_days=window_days,
        total_additions=total_additions,
        total_deletions=total_deletions,
        net_delta=net_delta,
        total_changed=total_changed,
        total_commits=total_commits,
        repo_count=repo_count,
        active_days=active_days,
        average_changed=average_changed,
        average_active_day=average_active_day,
    )

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


def format_signed_int(value: int) -> str:
    return f"{'+' if value >= 0 else '-'}{format_int(abs(value))}"


def load_font_candidates() -> dict[str, list[str]]:
    return {
        "mono": [
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        ],
        "serif": [
            "/System/Library/Fonts/Supplemental/Georgia.ttf",
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        ],
    }


def load_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in load_font_candidates()[kind]:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, tracking: int = 0) -> int:
    if not text:
        return 0
    if tracking <= 0:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
    width = 0
    for index, char in enumerate(text):
        bbox = draw.textbbox((0, 0), char, font=font)
        width += int(bbox[2] - bbox[0])
        if index < len(text) - 1:
            width += tracking
    return width


def draw_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    tracking: int = 0,
    align: str = "left",
) -> None:
    start_x = x
    if tracking > 0:
        total_width = text_width(draw, text, font, tracking)
        if align == "center":
            start_x -= total_width / 2
        elif align == "right":
            start_x -= total_width
        for index, char in enumerate(text):
            draw.text((start_x, y), char, font=font, fill=fill)
            char_bbox = draw.textbbox((0, 0), char, font=font)
            start_x += (char_bbox[2] - char_bbox[0]) + tracking
        return

    anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


def render_activity_frame(username: str, metrics: TemporalMetrics, summary: WeeklySummary, frame_index: int, frame_count: int, size: int = 600) -> Image.Image:
    scale = size / 480.0
    background = Image.new("RGBA", (size, size), (13, 17, 23, 255))
    draw = ImageDraw.Draw(background)
    mono_bracket = load_font("mono", int(8 * scale))
    mono_small = load_font("mono", int(9 * scale))
    mono_body = load_font("mono", int(11 * scale))
    serif_big = load_font("serif", int(32 * scale))
    serif_mid = load_font("serif", int(19 * scale))
    phase = (frame_index / frame_count) * math.tau
    pad = 24 * scale

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    blobs = [
        (size * 0.48 + math.sin(phase * 0.9) * size * 0.03, size * 0.46 + math.cos(phase * 1.2) * size * 0.02, size * 0.54, size * 0.48, (125, 51, 57, 108)),
        (size * 0.60 + math.cos(phase * 1.1) * size * 0.025, size * 0.55 + math.sin(phase * 0.8) * size * 0.03, size * 0.36, size * 0.34, (240, 221, 216, 28)),
        (size * 0.40 + math.sin(phase * 0.6) * size * 0.03, size * 0.38 + math.sin(phase * 1.4) * size * 0.02, size * 0.46, size * 0.42, (91, 38, 43, 52)),
    ]
    for cx, cy, w, h, fill in blobs:
        glow_draw.ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fill=fill)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(12, int(20 * scale))))
    background.alpha_composite(glow)

    # Vignette overlay (radial gradient: transparent center, opaque edges)
    vignette_mask = Image.new("L", (size, size), 0)
    v_draw = ImageDraw.Draw(vignette_mask)
    vcx, vcy = size // 2, size // 2
    max_r = int(math.sqrt(vcx ** 2 + vcy ** 2))
    v_steps = 60
    for vi in range(v_steps, 0, -1):
        vr = int(max_r * vi / v_steps)
        progress = vi / v_steps
        valpha = 0 if progress <= 0.2 else int(255 * ((progress - 0.2) / 0.8))
        v_draw.ellipse((vcx - vr, vcy - vr, vcx + vr, vcy + vr), fill=valpha)
    vignette_layer = Image.new("RGBA", (size, size), (13, 17, 23, 0))
    vignette_layer.putalpha(vignette_mask)
    background.alpha_composite(vignette_layer)

    # Helper: draw a bordered bracket label
    def draw_bracket(bx, by, blabel, balign="left"):
        btracking = max(0, int(1.5 * scale))
        btw = text_width(draw, blabel, mono_bracket, btracking)
        bpx, bpy = 6 * scale, 2 * scale
        bbw = btw + bpx * 2
        bbh = int(8 * scale + bpy * 2)
        box_x = bx - bbw if balign == "right" else bx
        draw.rectangle((box_x, by, box_x + bbw, by + bbh), outline=(240, 221, 216, 30), width=1)
        draw_text(draw, box_x + bpx, by + bpy, blabel, font=mono_bracket, fill=(240, 221, 216, 128), tracking=btracking)
        return bbh

    # Header Left
    hx = pad
    hy = pad
    bh = draw_bracket(hx, hy, "IDENTIFIER")
    hy += bh + 6 * scale
    draw_text(draw, hx, hy, username.upper().replace("-", "_"), font=mono_body, fill=(240, 221, 216, 255), tracking=max(0, int(scale)))
    hy += 14 * scale
    draw_text(draw, hx, hy, "TEMPORAL DENSITY ANALYSIS", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))
    hy += 12 * scale
    period = f"PERIOD: {metrics.start_day.strftime('%Y.%m')} - {metrics.end_day.strftime('%Y.%m')}"
    draw_text(draw, hx, hy, period, font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))

    # Header Right
    rx = size - pad
    ry = pad
    bh = draw_bracket(rx, ry, "VOLUMETRICS", balign="right")
    ry += bh + 6 * scale
    draw_text(draw, rx, ry, "PEAK VELOCITY", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)), align="right")
    ry += 10 * scale
    draw_text(draw, rx, ry, f"{metrics.peak_velocity}/wk", font=serif_big, fill=(240, 221, 216, 255), align="right")

    # Heatmap panel — sized to contain 26×7 grid with 10px padding
    cell_size = 12 * scale
    gap = 3 * scale
    grid_cols = min(len(metrics.cells) // 7, 26) if metrics.cells else 26
    grid_w = grid_cols * cell_size + (grid_cols - 1) * gap
    grid_h = 7 * cell_size + 6 * gap
    panel_pad = 10 * scale
    panel_w = grid_w + panel_pad * 2
    panel_h = grid_h + panel_pad * 2
    panel_x = (size - panel_w) / 2
    panel_y = 186 * scale
    panel_rect = (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h)

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rectangle((panel_rect[0], panel_rect[1] + 8 * scale, panel_rect[2], panel_rect[3] + 8 * scale), fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(4, int(10 * scale))))
    background.alpha_composite(shadow)
    draw.rectangle(panel_rect, fill=(13, 17, 23, 110), outline=(240, 221, 216, 30), width=1)

    # "// ANNUAL ACTIVITY MATRIX" label
    draw_text(draw, size / 2, panel_y - 14 * scale, "// ANNUAL ACTIVITY MATRIX", font=mono_small, fill=(240, 221, 216, 128), tracking=max(1, int(2 * scale)), align="center")

    # Crosshair behind heatmap
    cx, cy = size / 2, panel_y + panel_h / 2
    draw.line((cx, panel_y - 24 * scale, cx, panel_y + panel_h + 12 * scale), fill=(240, 221, 216, 14), width=1)
    draw.line((panel_x - 20 * scale, cy, panel_x + panel_w + 20 * scale, cy), fill=(240, 221, 216, 14), width=1)

    # Heatmap grid (Sun=row0 .. Sat=row6, columns = weeks)
    grid_x = panel_x + panel_pad
    grid_y = panel_y + panel_pad
    colors = {0: (58, 26, 28, 225), 1: (92, 38, 42, 235), 2: (136, 62, 67, 240), 3: (196, 122, 127, 245), 4: (240, 221, 216, 250)}
    for index, cell in enumerate(metrics.cells):
        if cell.level < 0:
            continue  # future day — no square
        column = index // 7
        row = index % 7
        x = grid_x + column * (cell_size + gap)
        y = grid_y + row * (cell_size + gap)
        if cell.level >= 3:
            pulse = 0.82 + 0.18 * math.sin(phase + index * 0.18)
            glow_fill = (196, 122, 127, int(70 * pulse)) if cell.level == 3 else (240, 221, 216, int(90 * pulse))
            draw.rounded_rectangle((x - 2 * scale, y - 2 * scale, x + cell_size + 2 * scale, y + cell_size + 2 * scale), radius=max(1, int(2 * scale)), fill=glow_fill)
        draw.rounded_rectangle((x, y, x + cell_size, y + cell_size), radius=max(1, int(scale)), fill=colors[cell.level])

    # Legend centered below heatmap
    legend_y = panel_y + panel_h + 14 * scale
    legend_cell = 8 * scale
    legend_gap = 6 * scale
    legend_cells_w = 5 * legend_cell + 4 * legend_gap
    less_w = text_width(draw, "LESS", mono_small, max(0, int(scale)))
    more_w = text_width(draw, "MORE", mono_small, max(0, int(scale)))
    total_legend_w = less_w + 8 * scale + legend_cells_w + 8 * scale + more_w
    legend_start = (size - total_legend_w) / 2
    draw_text(draw, legend_start, legend_y, "LESS", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))
    lx = legend_start + less_w + 8 * scale
    for fill in ((58, 26, 28, 220), (92, 38, 42, 235), (136, 62, 67, 240), (196, 122, 127, 245), (240, 221, 216, 255)):
        draw.rounded_rectangle((lx, legend_y - 5 * scale, lx + legend_cell, legend_y + 3 * scale), radius=max(1, int(scale / 2)), fill=fill)
        lx += legend_cell + legend_gap
    draw_text(draw, lx + 2 * scale, legend_y, "MORE", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))

    # Bottom Left - Consistency Rating
    bl_x = pad
    bl_base = size - pad - 56 * scale
    draw.line((bl_x, bl_base, bl_x, bl_base + 54 * scale), fill=(240, 221, 216, 30), width=1)
    bx = bl_x + 10 * scale
    draw_text(draw, bx, bl_base, "CONSISTENCY RATING", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))
    label_text = f"{metrics.consistency_label} "
    draw_text(draw, bx, bl_base + 16 * scale, label_text, font=serif_mid, fill=(240, 221, 216, 255))
    lw = text_width(draw, label_text, serif_mid)
    score_text = f"[ {metrics.consistency_score:.2f} ]"
    draw_text(draw, bx + lw, bl_base + 20 * scale, score_text, font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))
    draw.line((bx, bl_base + 36 * scale, bx + 130 * scale, bl_base + 36 * scale), fill=(240, 221, 216, 30), width=1)
    draw_text(draw, bx, bl_base + 42 * scale, f"STREAK: {metrics.streak_weeks} WEEKS", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)))

    # Bottom Right - Temporal Bias
    br_x = size - pad
    draw.line((br_x, bl_base, br_x, bl_base + 54 * scale), fill=(240, 221, 216, 30), width=1)
    bx_r = br_x - 10 * scale
    draw_text(draw, bx_r, bl_base, "TEMPORAL BIAS", font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)), align="right")
    draw_text(draw, bx_r, bl_base + 16 * scale, metrics.temporal_bias, font=serif_mid, fill=(240, 221, 216, 255), align="right")
    draw.line((bx_r - 130 * scale, bl_base + 36 * scale, bx_r, bl_base + 36 * scale), fill=(240, 221, 216, 30), width=1)
    draw_text(draw, bx_r, bl_base + 42 * scale, metrics.observed_clock, font=mono_small, fill=(240, 221, 216, 128), tracking=max(0, int(scale)), align="right")

    noise = Image.effect_noise((size, size), 10.0).convert("L")
    noise_rgba = Image.new("RGBA", (size, size), (240, 221, 216, 0))
    noise_rgba.putalpha(noise.point(lambda value: int(value * 0.06)))
    background.alpha_composite(noise_rgba)
    return background.convert("RGB")


def render_activity_gif(username: str, metrics: TemporalMetrics, summary: WeeklySummary) -> bytes:
    frame_count = 16
    frames = [render_activity_frame(username, metrics, summary, index, frame_count) for index in range(frame_count)]
    palette_frames = [frame.convert("P", palette=Image.ADAPTIVE, colors=128) for frame in frames]
    from io import BytesIO
    output = BytesIO()
    palette_frames[0].save(output, format="GIF", save_all=True, append_images=palette_frames[1:], duration=90, loop=0, optimize=True, disposal=2)
    return output.getvalue()


def render_activity_preview(username: str = "", metrics: "TemporalMetrics | None" = None) -> str:
    if metrics:
        cell_levels = [c.level for c in metrics.cells]
        grid_cols = min(len(cell_levels) // 7, 26) if cell_levels else 26
        cell_data_js = json.dumps(cell_levels)
        username_display = username.upper().replace("-", "_") or "PROFILE_IDX_04"
        period_str = f"{metrics.start_day.strftime('%Y.%m')} - {metrics.end_day.strftime('%Y.%m')}"
        peak_vel = f"{metrics.peak_velocity}/wk"
        cons_label = metrics.consistency_label
        cons_score = f"{metrics.consistency_score:.2f}"
        streak_str = f"STREAK: {metrics.streak_weeks} WEEKS"
        bias_str = metrics.temporal_bias
        clock_str = metrics.observed_clock
    else:
        cell_data_js = "null"
        grid_cols = 26
        username_display = "PROFILE_IDX_04"
        period_str = "2023.10 - 2024.10"
        peak_vel = "142/wk"
        cons_label = "Alpha"
        cons_score = "0.94"
        streak_str = "STREAK: 42 WEEKS"
        bias_str = "Nocturnal"
        clock_str = "UTC+2 OBSERVED"

    html = """<!DOCTYPE html>
<html lang="en"><head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Specimen // Temporal Activity</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="">
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-edge: #0d1117;
            --text-cream: #F0DDD8;
            --text-muted: rgba(240, 221, 216, 0.5);
            --line-subtle: rgba(240, 221, 216, 0.12);
            --ruby-glow: rgba(136, 62, 67, 0.6);
            --ruby-dark: #3a1a1c;
            --ruby-base: #883E43;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-edge);
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh;
            font-family: 'Space Mono', monospace;
            color: var(--text-cream);
            -webkit-font-smoothing: antialiased;
            overflow: hidden;
        }
        .specimen-artwork { position: relative; width: 480px; height: 480px; overflow: hidden; }
        #gl-canvas { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; background: var(--bg-edge); }
        .vignette-overlay {
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 2;
            background: radial-gradient(circle at center, transparent 20%, var(--bg-edge) 100%);
            pointer-events: none;
        }
        .specimen-data {
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 3;
            padding: 24px;
            display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: auto 1fr auto;
            pointer-events: none;
        }
        .mono-tiny {
            font-family: 'Space Mono', monospace; font-size: 9px;
            text-transform: uppercase; letter-spacing: 0.12em;
            color: var(--text-muted); line-height: 1.4;
        }
        .mono-value {
            font-family: 'Space Mono', monospace; font-size: 11px; font-weight: 700;
            color: var(--text-cream); letter-spacing: 0.05em;
        }
        .serif-display { font-family: 'Cormorant Garamond', serif; color: var(--text-cream); line-height: 1; }
        .header-left { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; gap: 2px; }
        .header-right { grid-column: 2; grid-row: 1; text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
        .label-bracket {
            display: inline-block; border: 1px solid var(--line-subtle);
            padding: 2px 6px; font-size: 8px; letter-spacing: 0.15em; margin-bottom: 6px;
        }
        .center-display {
            grid-column: 1 / 3; grid-row: 2;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            position: relative;
        }
        .heatmap-container {
            margin-top: 10px;
            display: grid; grid-template-columns: repeat(""" + str(grid_cols) + """, 12px); grid-template-rows: repeat(7, 12px); gap: 3px;
            grid-auto-flow: column;
            padding: 10px; background: rgba(13, 17, 23, 0.4);
            border: 1px solid var(--line-subtle);
            backdrop-filter: blur(4px); box-shadow: 0 0 30px rgba(0,0,0,0.5);
        }
        .cell { width: 12px; height: 12px; background: var(--ruby-dark); border-radius: 1px; opacity: 0.8; }
        .cell.level-1 { background: #5c262a; }
        .cell.level-2 { background: #883e43; }
        .cell.level-3 { background: #c47a7f; box-shadow: 0 0 4px var(--ruby-glow); }
        .cell.level-4 { background: #f0ddd8; box-shadow: 0 0 8px rgba(240, 221, 216, 0.4); }
        .heatmap-legend { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
        .crosshair {
            position: absolute; width: 100%; height: 100%;
            display: flex; justify-content: center; align-items: center;
            z-index: -1; opacity: 0.2;
        }
        .crosshair::before { content: ''; position: absolute; width: 1px; height: 320px; background: var(--line-subtle); }
        .crosshair::after { content: ''; position: absolute; width: 400px; height: 1px; background: var(--line-subtle); }
        .bottom-left { grid-column: 1; grid-row: 3; align-self: end; border-left: 1px solid var(--line-subtle); padding-left: 10px; }
        .bottom-right { grid-column: 2; grid-row: 3; align-self: end; text-align: right; border-right: 1px solid var(--line-subtle); padding-right: 10px; }
        .data-group { margin-top: 12px; }
        .fraction-line { width: 100%; height: 1px; background: var(--line-subtle); margin: 6px 0; }
    </style>
</head>
<body>
    <div class="specimen-artwork">
        <canvas id="gl-canvas"></canvas>
        <div class="vignette-overlay"></div>
        <div class="specimen-data">
            <div class="header-left">
                <div class="label-bracket">IDENTIFIER</div>
                <div class="mono-value">__USERNAME__</div>
                <div class="mono-tiny">TEMPORAL DENSITY ANALYSIS</div>
                <div class="mono-tiny">PERIOD: __PERIOD__</div>
            </div>
            <div class="header-right">
                <div class="label-bracket">VOLUMETRICS</div>
                <div class="mono-tiny">PEAK VELOCITY</div>
                <div class="serif-display" style="font-size: 2rem; margin-top: -2px;">__PEAK_VEL__</div>
            </div>
            <div class="center-display">
                <div class="crosshair"></div>
                <div class="mono-tiny" style="margin-bottom: 12px; letter-spacing: 0.3em;">// ANNUAL ACTIVITY MATRIX</div>
                <div class="heatmap-container" id="heatmap-grid"></div>
                <div class="heatmap-legend">
                    <span class="mono-tiny">LESS</span>
                    <div class="cell" style="width:8px; height:8px;"></div>
                    <div class="cell level-1" style="width:8px; height:8px;"></div>
                    <div class="cell level-2" style="width:8px; height:8px;"></div>
                    <div class="cell level-3" style="width:8px; height:8px;"></div>
                    <div class="cell level-4" style="width:8px; height:8px;"></div>
                    <span class="mono-tiny">MORE</span>
                </div>
            </div>
            <div class="bottom-left">
                <div class="mono-tiny">CONSISTENCY RATING</div>
                <div class="data-group">
                    <div class="serif-display" style="font-size: 1.2rem;">__CONS_LABEL__ <span class="mono-tiny" style="vertical-align: middle;">[ __CONS_SCORE__ ]</span></div>
                </div>
                <div class="fraction-line"></div>
                <div class="mono-tiny">__STREAK__</div>
            </div>
            <div class="bottom-right">
                <div class="mono-tiny">TEMPORAL BIAS</div>
                <div class="data-group">
                    <div class="serif-display" style="font-size: 1.2rem;">__BIAS__</div>
                </div>
                <div class="fraction-line"></div>
                <div class="mono-tiny">__CLOCK__</div>
            </div>
        </div>
    </div>
    <script>
        const cellData = __CELL_DATA__;
        const grid = document.getElementById('heatmap-grid');
        if (cellData) {
            for (let i = 0; i < cellData.length; i++) {
                const level = cellData[i];
                const cell = document.createElement('div');
                cell.className = 'cell';
                if (level < 0) { cell.style.visibility = 'hidden'; }
                else if (level >= 1) { cell.classList.add('level-' + level); }
                grid.appendChild(cell);
            }
        } else {
            for (let i = 0; i < 26 * 7; i++) {
                const cell = document.createElement('div');
                cell.className = 'cell';
                const rand = Math.random();
                if (rand > 0.92) cell.classList.add('level-4');
                else if (rand > 0.75) cell.classList.add('level-3');
                else if (rand > 0.5) cell.classList.add('level-2');
                else if (rand > 0.25) cell.classList.add('level-1');
                grid.appendChild(cell);
            }
        }
        const canvas = document.getElementById('gl-canvas');
        const gl = canvas.getContext('webgl');
        const vsSource = `attribute vec4 aVertexPosition; void main() { gl_Position = aVertexPosition; }`;
        const fsSource = `
            precision highp float;
            uniform vec2 u_resolution;
            uniform float u_time;
            float random (in vec2 st) { return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123); }
            float noise (in vec2 st) {
                vec2 i = floor(st); vec2 f = fract(st);
                float a = random(i); float b = random(i + vec2(1.0, 0.0));
                float c = random(i + vec2(0.0, 1.0)); float d = random(i + vec2(1.0, 1.0));
                vec2 u = f*f*(3.0-2.0*f);
                return mix(a, b, u.x) + (c - a)* u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
            }
            #define OCTAVES 5
            float fbm (in vec2 st) {
                float value = 0.0; float amplitude = .5;
                mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.50));
                for (int i = 0; i < OCTAVES; i++) {
                    value += amplitude * noise(st);
                    st = rot * st * 2.0 + vec2(100.0);
                    amplitude *= .5;
                }
                return value;
            }
            void main() {
                vec2 st = gl_FragCoord.xy/u_resolution.xy;
                vec2 p = st * 2.0 - 1.0;
                vec2 q = vec2(fbm(p + 0.05 * u_time), fbm(p + vec2(1.0)));
                vec2 r = vec2(fbm(p + 1.5 * q + 0.15 * u_time), fbm(p + 1.5 * q + 0.126 * u_time));
                float f = fbm(p + r);
                vec3 col0 = vec3(0.051, 0.067, 0.090);
                vec3 col2 = vec3(0.533, 0.243, 0.263);
                vec3 col3 = vec3(0.769, 0.478, 0.498);
                vec3 color = mix(col0, col2, clamp(length(q)*1.2, 0.0, 1.0));
                color = mix(color, col3, clamp(length(r.x)*0.8, 0.0, 1.0));
                color += (random(st * (u_time * 0.1 + 1.0)) - 0.5) * 0.08;
                gl_FragColor = vec4(mix(col0, color, smoothstep(1.2, 0.4, max(abs(p.x), abs(p.y)))), 1.0);
            }
        `;
        function initShader(gl, vs, fs) {
            const s = (t, src) => { const sh = gl.createShader(t); gl.shaderSource(sh, src); gl.compileShader(sh); return sh; };
            const p = gl.createProgram(); gl.attachShader(p, s(gl.VERTEX_SHADER, vs)); gl.attachShader(p, s(gl.FRAGMENT_SHADER, fs));
            gl.linkProgram(p); return p;
        }
        const program = initShader(gl, vsSource, fsSource);
        const posLoc = gl.getAttribLocation(program, 'aVertexPosition');
        const resLoc = gl.getUniformLocation(program, 'u_resolution');
        const timeLoc = gl.getUniformLocation(program, 'u_time');
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([1,1,-1,1,1,-1,-1,-1]), gl.STATIC_DRAW);
        function render(now) {
            canvas.width = canvas.clientWidth; canvas.height = canvas.clientHeight;
            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.useProgram(program);
            gl.enableVertexAttribArray(posLoc);
            gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
            gl.uniform2f(resLoc, canvas.width, canvas.height);
            gl.uniform1f(timeLoc, now * 0.0004);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
            requestAnimationFrame(render);
        }
        requestAnimationFrame(render);
    </script>
</body></html>
"""
    html = html.replace("__USERNAME__", username_display)
    html = html.replace("__PERIOD__", period_str)
    html = html.replace("__PEAK_VEL__", peak_vel)
    html = html.replace("__CONS_LABEL__", cons_label)
    html = html.replace("__CONS_SCORE__", cons_score)
    html = html.replace("__STREAK__", streak_str)
    html = html.replace("__BIAS__", bias_str)
    html = html.replace("__CLOCK__", clock_str)
    html = html.replace("__CELL_DATA__", cell_data_js)
    return html


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
    gif_reference = "./assets/activity-card.gif"

    lines = [
        "## Activity Dashboard",
        "",
        '<p align="center">',
        f'  <img src="{gif_reference}" alt="Temporal activity dashboard card" width="100%" />',
        "</p>",
        "",
    ]

    if total_commits == 0:
        lines.extend(
            [
                f"<sub>Updated {updated_at}</sub>",
                "",
                f"No code-file commits found for `{username}` between {window_start.date()} and {window_end.date()}.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "<details>",
            "<summary>Open raw 7-day breakdown</summary>",
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GitHub profile activity assets.")
    parser.add_argument("--github-username", help="GitHub username to analyze.")
    parser.add_argument("--github-token", help="GitHub token to use for authenticated API requests.")
    parser.add_argument("--update-readme", action="store_true", help="Rewrite the README stats block.")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    if args.github_username:
        os.environ["GH_USERNAME"] = args.github_username
    if args.github_token:
        os.environ["GH_TOKEN"] = args.github_token

    require_token_in_actions()
    username = infer_username()
    window_days = env_int("PROFILE_STATS_WINDOW_DAYS", 7)
    temporal_weeks = env_int("PROFILE_STATS_TEMPORAL_WEEKS", 26)
    window_end = now_utc()
    window_start = start_of_utc_day(window_end.date() - timedelta(days=window_days - 1))
    temporal_window_start = start_of_utc_day(window_end.date() - timedelta(days=(temporal_weeks * 7) - 1))

    collected = collect_stats(username, window_start, window_end)
    temporal_commits, temporal_warnings = collect_temporal_commits(username, temporal_window_start, window_end)
    all_warnings = [*collected.warnings, *temporal_warnings]
    if all_warnings:
        preview = "\n".join(all_warnings[:5])
        print(f"Skipped some repositories:\n{preview}", file=sys.stderr)

    block = render_stats(username, window_start, window_end, window_days, collected)
    weekly_summary = build_weekly_summary(collected, window_days)
    temporal_metrics = build_temporal_metrics(temporal_commits, window_end, temporal_weeks)
    activity_gif = render_activity_gif(username, temporal_metrics, weekly_summary)
    activity_preview = render_activity_preview(username, temporal_metrics)

    if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() == "1":
        print(block)
        return 0

    GIF_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    GIF_PATH.write_bytes(activity_gif)
    HTML_PREVIEW_PATH.write_text(activity_preview, encoding="utf-8")
    if args.update_readme:
        if not README_PATH.exists():
            raise GitHubError(f"README file not found at {README_PATH}")
        current = README_PATH.read_text(encoding="utf-8")
        updated = replace_stats_block(current, block)
        README_PATH.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
