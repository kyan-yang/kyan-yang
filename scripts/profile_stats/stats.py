from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .models import CollectedStats, CommitRecord, DashboardCardData, RepoStats, TemporalCommit, TemporalCell, TemporalMetrics, WeeklySummary


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


def filter_temporal_commits(
    commits: list[TemporalCommit],
    window_start: datetime,
    window_end: datetime,
) -> list[TemporalCommit]:
    return [commit for commit in commits if in_window(commit.committed_at, window_start, window_end)]


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


def runtime_status(score: float) -> str:
    if score >= 0.92:
        return "OPTIMIZED"
    if score >= 0.8:
        return "STABLE"
    if score >= 0.65:
        return "CALIBRATED"
    if score >= 0.45:
        return "VARIABLE"
    return "SPARSE"


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
    current_week_start = end_day - timedelta(days=(end_day.weekday() + 1) % 7)
    start_day = current_week_start - timedelta(days=(weeks - 1) * 7)
    grid_end_day = start_day + timedelta(days=weeks * 7 - 1)
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
    visible_days = [day for day in days if day <= end_day]
    counts = [daily_counts[day] for day in days]
    visible_counts = [daily_counts[day] for day in visible_days]
    visible_levels = intensity_levels(visible_counts)
    levels_by_day = {
        day: level
        for day, level in zip(visible_days, visible_levels)
    }
    levels = [levels_by_day.get(day, -1) for day in days]
    weekly_counts = [sum(counts[index : index + 7]) for index in range(0, len(counts), 7)]

    streak_weeks = 0
    for count in reversed(weekly_counts):
        if count <= 0:
            break
        streak_weeks += 1

    streak_days = 0
    for count in reversed(visible_counts):
        if count <= 0:
            break
        streak_days += 1

    bias, observed_clock = classify_temporal_bias(hours)
    score = (sum(1 for count in weekly_counts if count > 0) / weeks) if weeks else 0.0

    return TemporalMetrics(
        start_day=start_day,
        end_day=end_day,
        weeks=weeks,
        cells=[
            TemporalCell(day=day, count=count, level=level)
            for day, count, level in zip(days, counts, levels)
        ],
        peak_velocity=max(weekly_counts, default=0),
        consistency_score=score,
        consistency_label=consistency_label(score),
        streak_weeks=streak_weeks,
        streak_days=streak_days,
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


def build_dashboard_card(
    username: str,
    summary: WeeklySummary,
    fortnight_collected: CollectedStats,
    temporal_metrics: TemporalMetrics,
) -> DashboardCardData:
    heatmap_levels: list[int] = []
    for cell in temporal_metrics.cells:
        if cell.level < 0:
            heatmap_levels.append(-1)
        elif cell.level == 0:
            heatmap_levels.append(0)
        elif cell.level == 1:
            heatmap_levels.append(1)
        elif cell.level in (2, 3):
            heatmap_levels.append(2)
        else:
            heatmap_levels.append(3)

    return DashboardCardData(
        identifier=f"{username.upper().replace('-', '_')} // {temporal_metrics.consistency_label.upper()}",
        runtime_status=runtime_status(temporal_metrics.consistency_score),
        total_commits=len(fortnight_collected.commits),
        total_additions=summary.total_additions,
        total_deletions=summary.total_deletions,
        repo_count=len(fortnight_collected.per_repo),
        window_days=temporal_metrics.weeks * 7,
        heatmap_levels=heatmap_levels,
        language_segments=render_language_donut(fortnight_collected.per_language),
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
