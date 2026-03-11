from __future__ import annotations

import html
import math
from datetime import datetime, timezone

from .config import END_MARKER, START_MARKER
from .models import CollectedStats, CommitRecord, DashboardCardData, fake_dev_card
from .stats import busiest_day, daily_rollup, largest_commit
from .github_api import now_utc


def format_int(value: int) -> str:
    return f"{value:,}"


def format_compact_int(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        compact = f"{absolute / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{compact}M"
    if absolute >= 1_000:
        compact = f"{absolute / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{compact}K"
    return str(absolute)


def format_percent(value: float) -> str:
    return f"{value:.1f}%"


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


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


def render_activity_svg(card: DashboardCardData | None = None) -> str:
    if card is None:
        card = fake_dev_card()

    def arc_path(cx: float, cy: float, radius: float, start: float, end: float) -> str:
        start_x = cx + radius * math.cos(start)
        start_y = cy + radius * math.sin(start)
        end_x = cx + radius * math.cos(end)
        end_y = cy + radius * math.sin(end)
        large_arc = 1 if end - start > math.pi else 0
        return (
            f"M {start_x:.3f} {start_y:.3f} "
            f"A {radius:.3f} {radius:.3f} 0 {large_arc} 1 {end_x:.3f} {end_y:.3f}"
        )

    heatmap_colors = {
        0: "#1B1A22",
        1: "rgba(136, 62, 67, 0.32)",
        2: "rgba(136, 62, 67, 0.62)",
        3: "#883E43",
    }
    heatmap_glow = {3: ' filter="url(#cellGlow)"'}
    heatmap_cells: list[str] = []
    grid_x = 24
    grid_y = 253
    cell_size = 17
    gap = 3
    for index, level in enumerate(card.heatmap_levels[: 22 * 7]):
        if level < 0:
            continue
        column = index // 7
        row = index % 7
        x = grid_x + column * (cell_size + gap)
        y = grid_y + row * (cell_size + gap)
        heatmap_cells.append(
            f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
            f'fill="{heatmap_colors.get(level, heatmap_colors[0])}" rx="0.8"{heatmap_glow.get(level, "")}></rect>'
        )

    palette = ["#F0DDD8", "#C47A7F", "#883E43", "#5C262A"]
    donut_paths: list[str] = []
    legend_rows: list[str] = []
    cx = 271
    cy = 151
    radius = 38
    offset = -math.pi / 2
    for index, (language, share) in enumerate(card.language_segments):
        color = palette[index % len(palette)]
        start = offset
        end = offset + (share * math.tau)
        if share > 0:
            donut_paths.append(
                f'<path d="{arc_path(cx, cy, radius, start, end)}" fill="none" stroke="{color}" stroke-width="17" stroke-linecap="butt"></path>'
            )
        label_y = 124 + index * 18
        legend_rows.append(
            f'<circle cx="326" cy="{label_y}" r="4" fill="{color}"></circle>'
            f'<text x="337" y="{label_y + 2}" class="legend-label">{xml_escape(language[:11])}</text>'
            f'<text x="441" y="{label_y + 2}" class="legend-value">{format_percent(share * 100)}</text>'
        )
        offset = end

    return f"""<svg width="480" height="480" viewBox="0 0 480 480" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub activity dashboard">
  <defs>
    <radialGradient id="rubyGlowA" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(192 264) rotate(90) scale(150 190)">
      <stop stop-color="#883E43" stop-opacity="0.45"/>
      <stop offset="0.35" stop-color="#64272D" stop-opacity="0.25"/>
      <stop offset="0.7" stop-color="#0D1117" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="rubyGlowB" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(336 144) rotate(90) scale(110 135)">
      <stop stop-color="#501E23" stop-opacity="0.3"/>
      <stop offset="0.6" stop-color="#0D1117" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="vignette" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(240 240) rotate(90) scale(240)">
      <stop offset="0.15" stop-color="#0D1117" stop-opacity="0"/>
      <stop offset="0.5" stop-color="#0D1117" stop-opacity="0.6"/>
      <stop offset="0.8" stop-color="#0D1117"/>
    </radialGradient>
    <filter id="cellGlow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="2.5" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
    <style>
      .mono {{ font-family: "Space Mono", "SFMono-Regular", Consolas, monospace; fill: #F0DDD8; }}
      .serif {{ font-family: "Cormorant Garamond", Georgia, serif; fill: #F0DDD8; }}
      .tiny {{ font-size: 9px; letter-spacing: 1.6px; text-transform: uppercase; fill: rgba(240, 221, 216, 0.5); }}
      .small {{ font-size: 12px; letter-spacing: 0.2px; }}
      .legend-label {{ font-family: "Space Mono", "SFMono-Regular", Consolas, monospace; font-size: 8px; letter-spacing: 0.7px; fill: rgba(240, 221, 216, 0.82); text-transform: uppercase; }}
      .legend-value {{ font-family: "Space Mono", "SFMono-Regular", Consolas, monospace; font-size: 8px; letter-spacing: 0.5px; fill: rgba(240, 221, 216, 0.5); text-anchor: end; }}
    </style>
  </defs>
  <rect width="480" height="480" fill="#0D1117"/>
  <rect width="480" height="480" fill="url(#rubyGlowA)"/>
  <rect width="480" height="480" fill="url(#rubyGlowB)"/>
  <rect width="480" height="480" fill="url(#vignette)"/>
  <line x1="0" y1="240.5" x2="480" y2="240.5" stroke="rgba(240, 221, 216, 0.12)" stroke-opacity="0.4"/>

  <line x1="24" y1="81.5" x2="456" y2="81.5" stroke="rgba(240, 221, 216, 0.12)"/>
  <text x="24" y="54" class="mono small" font-weight="700">Past 14 days activity</text>

  <text x="117" y="170" class="serif" font-size="66" font-style="italic" text-anchor="middle">{format_int(card.total_commits)}</text>
  <text x="122" y="206" class="mono tiny" text-anchor="middle" letter-spacing="3.2px">COMMITS</text>

  <line x1="195.5" y1="102" x2="195.5" y2="211" stroke="rgba(240, 221, 216, 0.12)"/>
  <circle cx="{cx}" cy="{cy}" r="{radius}" stroke="rgba(240, 221, 216, 0.08)" stroke-width="17"/>
  {''.join(donut_paths)}
  <circle cx="{cx}" cy="{cy}" r="22" fill="#0D1117"/>
  <text x="{cx}" y="{cy - 4}" class="mono" font-size="10" text-anchor="middle">LANG</text>
  <text x="{cx}" y="{cy + 13}" class="mono tiny" text-anchor="middle">{len(card.language_segments)} MIX</text>
  <text x="326" y="110" class="mono tiny">LANGUAGE</text>
  {''.join(legend_rows)}

  {''.join(heatmap_cells)}

  <line x1="24" y1="410.5" x2="456" y2="410.5" stroke="rgba(240, 221, 216, 0.12)"/>
  <line x1="154.5" y1="423" x2="154.5" y2="457" stroke="rgba(240, 221, 216, 0.12)"/>
  <line x1="300.5" y1="423" x2="300.5" y2="457" stroke="rgba(240, 221, 216, 0.12)"/>

  <text x="24" y="428" class="mono tiny">ADDITIONS</text>
  <text x="24" y="449" class="mono small" font-weight="700">+ {format_compact_int(card.total_additions)}</text>

  <text x="179" y="428" class="mono tiny">DELETIONS</text>
  <text x="179" y="449" class="mono small" font-weight="700">- {format_compact_int(card.total_deletions)}</text>

  <text x="324" y="428" class="mono tiny">REPOSITORIES</text>
  <text x="324" y="449" class="mono small" font-weight="700">{format_int(card.repo_count)} REPOS</text>
</svg>
"""


def render_activity_preview(card: DashboardCardData | None = None) -> str:
    svg = render_activity_svg(card)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitHub Specimen // Ruby Dash</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ min-height: 100vh; display: grid; place-items: center; background: #0d1117; }}
    .frame {{ width: 480px; height: 480px; }}
  </style>
</head>
<body>
  <div class="frame">{svg}</div>
</body>
</html>"""


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
    image_reference = "./assets/activity-card.svg"

    lines = [
        "## Activity Dashboard",
        "",
        '<p align="center">',
        f'  <img src="{image_reference}" alt="Temporal activity dashboard card" width="100%" />',
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

    lines.extend(["", "</details>"])
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
