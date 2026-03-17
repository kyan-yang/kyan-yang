from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .config import API_ROOT, CACHE_PATH, REQUEST_TIMEOUT_SECONDS, detect_language, env_int, excluded_repos, is_code_file
from .models import ActivityDataset, CommitRecord, CommitSummary, GitHubError, RateLimitError, RepoStats


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _commit_to_dict(record: CommitRecord) -> dict[str, object]:
    return {
        "repo": record.repo,
        "sha": record.sha,
        "committed_at": to_iso8601(record.committed_at),
        "additions": record.additions,
        "deletions": record.deletions,
        "per_language": {
            lang: {"additions": stats.additions, "deletions": stats.deletions}
            for lang, stats in record.per_language.items()
        },
    }


def _commit_from_dict(data: dict[str, object]) -> CommitRecord | None:
    try:
        committed_at = parse_iso8601(data.get("committed_at"))
        if not committed_at:
            return None
        per_language: dict[str, RepoStats] = {}
        for lang, stats in (data.get("per_language") or {}).items():
            if isinstance(stats, dict):
                per_language[lang] = RepoStats(
                    additions=int(stats.get("additions", 0)),
                    deletions=int(stats.get("deletions", 0)),
                )
        return CommitRecord(
            repo=str(data.get("repo", "")),
            sha=str(data.get("sha", "")),
            committed_at=committed_at,
            additions=int(data.get("additions", 0)),
            deletions=int(data.get("deletions", 0)),
            per_language=per_language,
        )
    except (TypeError, ValueError):
        return None


def load_cache() -> list[CommitRecord]:
    if not CACHE_PATH.exists():
        return []
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        records: list[CommitRecord] = []
        for entry in raw:
            if isinstance(entry, dict):
                record = _commit_from_dict(entry)
                if record:
                    records.append(record)
        return records
    except (json.JSONDecodeError, OSError):
        return []


def save_cache(commits: list[CommitRecord]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = [_commit_to_dict(c) for c in commits]
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def collect_activity(
    username: str,
    code_window_start: datetime,
    window_end: datetime,
) -> ActivityDataset:
    repos = candidate_repositories(username, code_window_start)
    code_commits: list[CommitRecord] = []
    seen_commits: set[str] = set()
    warnings: list[str] = []

    for owner, repo in repos.values():
        full_name = f"{owner}/{repo}"
        try:
            commits = list_recent_commits(owner, repo, username, code_window_start, window_end)
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

            committed_at = extract_commit_datetime(commit)

            try:
                summary = commit_stats(owner, repo, sha)
            except GitHubError as exc:
                warnings.append(f"Skipped {full_name}@{sha[:7]}: {exc}")
                continue

            if summary.included_files == 0:
                continue

            code_commits.append(
                CommitRecord(
                    repo=full_name,
                    sha=sha,
                    committed_at=committed_at,
                    additions=summary.additions,
                    deletions=summary.deletions,
                    per_language=summary.per_language,
                )
            )

    # Merge cached commits that fall within the window
    cached = load_cache()
    for record in cached:
        commit_key = f"{record.repo}:{record.sha}"
        if commit_key in seen_commits:
            continue
        if record.committed_at < code_window_start or record.committed_at > window_end:
            continue
        seen_commits.add(commit_key)
        code_commits.append(record)

    code_commits.sort(key=lambda item: item.committed_at)

    # Save all commits (fresh + cached) back to the cache
    save_cache(code_commits)

    return ActivityDataset(code_commits=code_commits, warnings=warnings)
