from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from .models import CollectedStats, CommitRecord, DashboardCardData, RepoStats, WeeklySummary


def start_of_utc_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

def in_window(committed_at: datetime, window_start: datetime, window_end: datetime) -> bool:
    return window_start <= committed_at <= window_end


def aggregate_stats(
    commits: list[CommitRecord],
    window_start: datetime,
    window_end: datetime,
    warnings: list[str] | None = None,
) -> CollectedStats:
    per_repo: dict[str, RepoStats] = defaultdict(RepoStats)
    per_language: dict[str, RepoStats] = defaultdict(RepoStats)
    filtered_commits: list[CommitRecord] = []

    for commit in commits:
        if not in_window(commit.committed_at, window_start, window_end):
            continue

        stats = per_repo[commit.repo]
        stats.additions += commit.additions
        stats.deletions += commit.deletions
        stats.commits += 1

        for language, language_stats in commit.per_language.items():
            aggregate = per_language[language]
            aggregate.additions += language_stats.additions
            aggregate.deletions += language_stats.deletions
            aggregate.commits += 1

        filtered_commits.append(commit)

    return CollectedStats(
        per_repo=dict(per_repo),
        per_language=dict(per_language),
        commits=filtered_commits,
        warnings=list(warnings or []),
    )


def language_breakdown(per_language: dict[str, RepoStats], limit: int = 4) -> list[tuple[str, int]]:
    ranked = sorted(
        ((language, stats.changed) for language, stats in per_language.items() if stats.changed > 0),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    if len(ranked) <= limit:
        return ranked
    top = ranked[: limit - 1]
    other_total = sum(total for _, total in ranked[limit - 1 :])
    top.append(("Other", other_total))
    return top


def render_language_donut(per_language: dict[str, RepoStats]) -> list[tuple[str, float]]:
    segments = language_breakdown(per_language)
    if not segments:
        return []
    total = sum(amount for _, amount in segments)
    return [(language, amount / total if total else 0.0) for language, amount in segments]


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


def build_dashboard_card(
    summary: WeeklySummary,
    card_collected: CollectedStats,
) -> DashboardCardData:
    return DashboardCardData(
        window_days=summary.window_days,
        active_days=summary.active_days,
        total_commits=summary.total_commits,
        total_additions=summary.total_additions,
        total_deletions=summary.total_deletions,
        repo_count=summary.repo_count,
        language_segments=render_language_donut(card_collected.per_language),
    )
