# AGENTS.md

## Purpose

This repository generates a GitHub profile activity dashboard and writes the results back into the profile README.

Primary outputs:

- `README.md`
- `assets/activity-card.svg`
- `assets/activity-card-preview.html`

Primary entrypoint:

- `scripts/update_profile_stats.py`


## Repo Layout

- `scripts/update_profile_stats.py`
  Thin CLI/orchestration entrypoint. Keep this small.
- `scripts/profile_stats/config.py`
  Environment variables, paths, code-file/language classification.
- `scripts/profile_stats/github_api.py`
  GitHub API access, pagination, repository discovery, commit collection.
- `scripts/profile_stats/stats.py`
  Aggregation and derived metrics.
- `scripts/profile_stats/render.py`
  README block generation and SVG/HTML rendering.
- `scripts/profile_stats/models.py`
  Shared dataclasses and domain errors.


## Working Rules

- Keep `scripts/update_profile_stats.py` as a thin wrapper. Put new business logic in `scripts/profile_stats/`.
- Treat `README.md`, `assets/activity-card.svg`, and `assets/activity-card-preview.html` as generated artifacts.
- Do not hand-edit the profile stats block between `<!-- profile-stats:start -->` and `<!-- profile-stats:end -->` unless the task explicitly requires changing renderer output.
- Prefer changing the renderer or stats pipeline, then regenerating outputs.
- Keep the code dependency-light. The current implementation is standard library only.
- Maintain UTC-based time handling unless there is an explicit reason to change it.
- Be careful with GitHub API usage. Avoid adding extra passes over repos/commits when existing data can be reused.

## Common Commands

Validate Python syntax:

```bash
python3 -m py_compile scripts/update_profile_stats.py scripts/profile_stats/*.py
```

Dry run without writing files:

```bash
PROFILE_STATS_DRY_RUN=1 python3 scripts/update_profile_stats.py
```

Regenerate assets and README locally:

```bash
python3 scripts/update_profile_stats.py --update-readme
```

Local dev (fake data, no API):

```bash
python3 scripts/update_profile_stats.py --dev --update-readme
```

Use lower API limits when testing network behavior:

```bash
PROFILE_STATS_MAX_REPO_PAGES=1 PROFILE_STATS_MAX_COMMIT_PAGES=1 PROFILE_STATS_DRY_RUN=1 python3 scripts/update_profile_stats.py
```


## Change Guidance

- If you change stat semantics, check both the README block and the SVG card output.
- If you change rendering, verify both `assets/activity-card.svg` and `assets/activity-card-preview.html`.
- If you add new environment variables, define them in `scripts/profile_stats/config.py`.
- If you add new derived metrics, keep aggregation in `scripts/profile_stats/stats.py` and presentation formatting in `scripts/profile_stats/render.py`.
- If you change GitHub collection behavior, prefer one discovery pass and reuse collected data across windows.


## Workflow Notes

- The scheduled workflow is `.github/workflows/update-profile-stats.yml`.
- It runs `python3 scripts/update_profile_stats.py --update-readme`.
- The workflow commits changes only when `README.md`, `assets/activity-card.svg`, or `assets/activity-card-preview.html` differ.


## Before Finishing

- Run `py_compile`.
- If the change affects output, run a dry run or full regeneration.
- Do not remove or rewrite unrelated user changes in generated assets or README unless the task requires it.
