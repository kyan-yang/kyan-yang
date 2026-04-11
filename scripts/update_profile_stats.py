#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from profile_stats.config import HTML_PREVIEW_PATH, IMAGE_PATH, PACIFIC_TZ, README_PATH
from profile_stats.github_api import collect_activity, infer_username, now_utc, require_token_in_actions
from profile_stats.models import GitHubError, fake_dev_collected
from profile_stats.render import render_activity_png, render_activity_preview, render_stats, replace_stats_block
from profile_stats.stats import aggregate_stats, build_dashboard_card, build_weekly_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GitHub profile activity assets.")
    parser.add_argument("--github-username", help="GitHub username to analyze.")
    parser.add_argument("--github-token", help="GitHub token to use for authenticated API requests.")
    parser.add_argument("--update-readme", action="store_true", help="Rewrite the README stats block.")
    parser.add_argument("--dev", action="store_true", help="Use fake data, skip GitHub API (for local testing).")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    if args.github_username:
        os.environ["GH_USERNAME"] = args.github_username
    if args.github_token:
        os.environ["GH_TOKEN"] = args.github_token

    if not args.dev:
        require_token_in_actions()

    username = infer_username() if not args.dev else "dev"
    now_pacific = now_utc().astimezone(PACIFIC_TZ)
    window_period = str(now_pacific.year)
    window_start = datetime(now_pacific.year, 1, 1, tzinfo=PACIFIC_TZ).astimezone(timezone.utc)
    window_end = now_utc()
    jan1 = now_pacific.replace(month=1, day=1).date()
    window_days = (now_pacific.date() - jan1).days + 1

    if args.dev:
        collected = fake_dev_collected(window_end)
        card_summary = build_weekly_summary(collected, window_days, window_period)
        card = build_dashboard_card(card_summary, collected)
        if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() != "1":
            print("Dev mode: using fake data, skipping GitHub API", file=sys.stderr)
    else:
        dataset = collect_activity(username, window_start, window_end)
        collected = aggregate_stats(dataset.code_commits, window_start, window_end, warnings=dataset.warnings)

        if dataset.warnings:
            preview = "\n".join(dataset.warnings[:5])
            print(f"Skipped some repositories:\n{preview}", file=sys.stderr)

        card_summary = build_weekly_summary(collected, window_days, window_period)
        card = build_dashboard_card(card_summary, collected)

    block = render_stats(username, window_start, window_end, window_period, collected)
    activity_png = render_activity_png(card)
    activity_preview = render_activity_preview(card)

    if os.getenv("PROFILE_STATS_DRY_RUN", "").strip() == "1":
        print(block)
        return 0

    IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_PATH.write_bytes(activity_png)
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
