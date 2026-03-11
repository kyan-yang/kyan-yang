#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


API_ROOT = "https://api.github.com"
README_PATH = Path(os.getenv("PROFILE_STATS_README", "README.md"))
REQUEST_TIMEOUT_SECONDS = 30
START_MARKER = "<!-- profile-stats:start -->"
END_MARKER = "<!-- profile-stats:end -->"


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
    commits: list[CommitRecord]
    warnings: list[str]


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


def commit_stats(owner: str, repo: str, sha: str) -> tuple[int, int]:
    details, _ = api_get(f"/repos/{owner}/{repo}/commits/{sha}")
    if not isinstance(details, dict):
        raise GitHubError(f"Expected commit details for {owner}/{repo}@{sha}")
    stats = details.get("stats") or {}
    additions = int(stats.get("additions", 0))
    deletions = int(stats.get("deletions", 0))
    return additions, deletions


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

            additions, deletions = commit_stats(owner, repo, sha)
            committed_at = extract_commit_datetime(commit)

            stats = per_repo[full_name]
            stats.additions += additions
            stats.deletions += deletions
            stats.commits += 1

            commit_records.append(
                CommitRecord(
                    repo=full_name,
                    sha=sha,
                    committed_at=committed_at,
                    additions=additions,
                    deletions=deletions,
                )
            )

    return CollectedStats(per_repo=dict(per_repo), commits=commit_records, warnings=warnings)


def render_coverage_note() -> str:
    if os.getenv("GH_TOKEN", "").strip():
        return "Coverage includes public repositories plus any additional repositories the workflow token can read."
    return "Coverage is limited to public activity. Add PROFILE_STATS_TOKEN to include private and collaborator repositories."


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
            f"{day.strftime('%m-%d')} | {bar:<18} {format_int(stats.changed):>7} lines | {commit_label}"
        )

    return "\n".join(lines)


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

    lines = [
        "## Activity Dashboard",
        "",
        f"> Rolling window: the last {window_days} calendar day{'s' if window_days != 1 else ''}, including today.",
        "",
        f"<sub>Updated {updated_at}</sub>",
        "",
    ]

    if total_commits == 0:
        lines.extend(
            [
                f"No commits found for `{username}` between {window_start.date()} and {window_end.date()}.",
                "",
                f"> {coverage_note}",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Lines changed | {format_int(total_changed)} |",
            f"| Added | +{format_int(total_additions)} |",
            f"| Deleted | -{format_int(total_deletions)} |",
            f"| Net delta | {'+' if net_delta >= 0 else '-'}{format_int(abs(net_delta))} |",
            f"| Commits | {format_int(total_commits)} |",
            f"| Repositories touched | {format_int(repo_count)} |",
            f"| Active days | {format_int(active_days)} / {format_int(window_days)} |",
            f"| Average lines per commit | {format_int(average_changed)} |",
            f"| Average lines per active day | {format_int(average_active_day)} |",
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
            f"- Busiest day: `{day.isoformat()}` with {format_int(stats.changed)} lines changed across {format_int(stats.commits)} commit{'s' if stats.commits != 1 else ''}"
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
            "| Repository | Lines | Added | Deleted | Commits |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )

    for full_name, stats in top_repos:
        lines.append(
            f"| `{full_name}` | {format_int(stats.changed)} | +{format_int(stats.additions)} | -{format_int(stats.deletions)} | {format_int(stats.commits)} |"
        )

    lines.extend(["", f"> {coverage_note}"])
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

    if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() == "1":
        print(block)
        return 0

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
