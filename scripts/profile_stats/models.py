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
    identifier: str
    runtime_status: str
    total_commits: int
    total_additions: int
    total_deletions: int
    repo_count: int
    window_days: int
    heatmap_levels: list[int]
    language_segments: list[tuple[str, float]]


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass
