#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

from profile_stats.config import HTML_PREVIEW_PATH, README_PATH, SVG_PATH, env_int
from profile_stats.github_api import collect_activity, infer_username, now_utc, require_token_in_actions
from profile_stats.models import GitHubError
from profile_stats.render import render_activity_preview, render_activity_svg, render_stats, replace_stats_block
from profile_stats.stats import aggregate_stats, build_dashboard_card, build_temporal_metrics, build_weekly_summary, filter_temporal_commits, start_of_utc_day


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
    commit_metric_days = 14
    card_weeks = env_int("PROFILE_STATS_CARD_WEEKS", 22)
    code_window_days = max(window_days, commit_metric_days)
    temporal_window_days = max(card_weeks * 7, code_window_days)

    window_end = now_utc()
    window_start = start_of_utc_day(window_end.date() - timedelta(days=window_days - 1))
    commit_metric_start = start_of_utc_day(window_end.date() - timedelta(days=commit_metric_days - 1))
    code_window_start = start_of_utc_day(window_end.date() - timedelta(days=code_window_days - 1))
    card_window_start = start_of_utc_day(window_end.date() - timedelta(days=(card_weeks * 7) - 1))
    temporal_window_start = start_of_utc_day(window_end.date() - timedelta(days=temporal_window_days - 1))

    dataset = collect_activity(username, temporal_window_start, code_window_start, window_end)
    collected = aggregate_stats(dataset.code_commits, window_start, window_end, warnings=dataset.warnings)
    fortnight_collected = aggregate_stats(dataset.code_commits, commit_metric_start, window_end)
    temporal_commits = filter_temporal_commits(dataset.temporal_commits, card_window_start, window_end)

    if dataset.warnings:
        preview = "\n".join(dataset.warnings[:5])
        print(f"Skipped some repositories:\n{preview}", file=sys.stderr)

    block = render_stats(username, window_start, window_end, window_days, collected)
    fortnight_summary = build_weekly_summary(fortnight_collected, commit_metric_days)
    temporal_metrics = build_temporal_metrics(temporal_commits, window_end, card_weeks)
    card = build_dashboard_card(username, fortnight_summary, fortnight_collected, temporal_metrics)
    activity_svg = render_activity_svg(card)
    activity_preview = render_activity_preview(card)

    if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() == "1":
        print(block)
        return 0

    SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(activity_svg, encoding="utf-8")
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
