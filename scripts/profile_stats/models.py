from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    per_language: dict[str, RepoStats] = field(default_factory=dict)

    @property
    def changed(self) -> int:
        return self.additions + self.deletions


@dataclass
class CommitSummary:
    additions: int = 0
    deletions: int = 0
    included_files: int = 0
    per_language: dict[str, RepoStats] = field(default_factory=dict)


@dataclass
class CollectedStats:
    per_repo: dict[str, RepoStats]
    per_language: dict[str, RepoStats]
    commits: list[CommitRecord]
    warnings: list[str]


@dataclass
class ActivityDataset:
    code_commits: list[CommitRecord]
    warnings: list[str]


@dataclass
class WeeklySummary:
    window_days: int
    window_period: str
    total_additions: int
    total_deletions: int
    net_delta: int
    total_changed: int
    total_commits: int
    repo_count: int
    active_days: int
    average_changed: int
    average_active_day: int


@dataclass
class DashboardCardData:
    window_days: int
    window_period: str
    active_days: int
    total_commits: int
    total_additions: int
    total_deletions: int
    repo_count: int
    language_segments: list[tuple[str, float]]

    @property
    def total_changed(self) -> int:
        return self.total_additions + self.total_deletions


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


def fake_dev_card(window_days: int = 100, window_period: str = "2026") -> DashboardCardData:
    """Sample data for local dev/testing without hitting the GitHub API."""
    return DashboardCardData(
        window_days=window_days,
        window_period=window_period,
        active_days=118,
        total_commits=182,
        total_additions=2_400_000,
        total_deletions=1_100_000,
        repo_count=12,
        language_segments=[
            ("TypeScript", 0.42),
            ("Python", 0.28),
            ("Go", 0.18),
            ("Other", 0.12),
        ],
    )


def fake_dev_collected(window_end=None) -> CollectedStats:
    """Sample CollectedStats for local dev/testing. Matches fake_dev_card totals."""
    from datetime import datetime, timedelta, timezone

    if window_end is None:
        window_end = datetime.now(timezone.utc)
    base = datetime(
        window_end.year, window_end.month, window_end.day, 12, 0, 0, tzinfo=timezone.utc
    )
    repos = {
        "owner/repo-a": RepoStats(additions=1_200_000, deletions=400_000, commits=80),
        "owner/repo-b": RepoStats(additions=800_000, deletions=500_000, commits=60),
        "owner/repo-c": RepoStats(additions=400_000, deletions=200_000, commits=42),
    }
    commits = [
        CommitRecord(
            repo="owner/repo-a" if offset % 3 == 0 else "owner/repo-b" if offset % 3 == 1 else "owner/repo-c",
            sha=f"fake{offset:04d}",
            committed_at=base - timedelta(days=offset),
            additions=15000 if offset % 2 == 0 else 8000,
            deletions=5000 if offset % 2 == 0 else 3000,
        )
        for offset in range(118)
    ]
    per_language = {
        "TypeScript": RepoStats(additions=1_240_000, deletions=230_000, commits=102),
        "Python": RepoStats(additions=620_000, deletions=360_000, commits=46),
        "Go": RepoStats(additions=390_000, deletions=240_000, commits=21),
        "Other": RepoStats(additions=150_000, deletions=270_000, commits=13),
    }
    return CollectedStats(per_repo=repos, per_language=per_language, commits=commits, warnings=[])
