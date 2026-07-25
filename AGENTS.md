# AGENTS.md

## Purpose

This is a GitHub profile repository. The README is the profile content and is hand-maintained.

## Repo Layout

- `README.md`
  Profile README. Edit directly.
- `.gitignore`
  Ignores `__pycache__/`, `*.pyc`, and `.env`.
- `.env` (gitignored, optional)
  Local secrets file. Not consumed by anything in this repo anymore; safe to delete if unused elsewhere.

## Profile Stats Block

`README.md` retains an empty block delimited by:

```
<!-- profile-stats:start -->
<!-- profile-stats:end -->
```

The previous activity-card pipeline (scripts, workflow, generated PNG/HTML, commit cache) has been removed. The markers are kept as a placeholder. To re-enable, reintroduce a generator that writes content between the markers.

## Working Rules

- Edit `README.md` directly.
- Do not introduce generated artifacts unless a generator is added back.
- Keep the repo dependency-free.
