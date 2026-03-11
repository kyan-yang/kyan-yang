from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


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
class TemporalCommit:
    repo: str
    sha: str
    committed_at: datetime


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
    temporal_commits: list[TemporalCommit]
    warnings: list[str]


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
    streak_days: int
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


@dataclass
class DashboardCardData:
    total_commits: int
    total_additions: int
    total_deletions: int
    repo_count: int
    heatmap_levels: list[int]
    language_segments: list[tuple[str, float]]


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


def fake_dev_card() -> DashboardCardData:
    """Sample data for local dev/testing without hitting the GitHub API."""
    return DashboardCardData(
        total_commits=182,
        total_additions=2_400_000,
        total_deletions=1_100_000,
        repo_count=12,
        heatmap_levels=[(index % 4) for index in range(154)],
        language_segments=[
            ("TypeScript", 0.42),
            ("Python", 0.28),
            ("Go", 0.18),
            ("Other", 0.12),
        ],
    )


def fake_dev_collected(window_end=None) -> CollectedStats:
    """Sample CollectedStats for local dev/testing. Matches fake_dev_card totals."""
    from datetime import datetime, timezone

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
        CommitRecord("owner/repo-a", "abc1234", base, 15000, 5000),
        CommitRecord("owner/repo-b", "def5678", base, 8000, 3000),
    ]
    return CollectedStats(per_repo=repos, per_language={}, commits=commits, warnings=[])
