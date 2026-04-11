from __future__ import annotations

import html
import re
from datetime import datetime

from .config import END_MARKER, REFERENCE_HTML_PATH, START_MARKER, export_scale
from .github_api import now_utc
from .models import CollectedStats, DashboardCardData, GitHubError, fake_dev_card

REFERENCE_TITLE = '<title vid="4">GitHub Specimen // Ruby Lips</title>'
REFERENCE_ACTIVE_LABEL = '<div class="mono-tiny" vid="16">ACTIVE CYCLE</div>'
REFERENCE_CROSSHAIR = '<div class="crosshair" vid="24"></div>'
REFERENCE_ACTIVE_CYCLE = (
    '<div class="serif-display" style="font-size: 1.8rem;" vid="17">342 '
    '<span class="mono-tiny" style="font-style: normal; vertical-align: middle;" vid="18">DAYS</span></div>'
)
REFERENCE_REPOSITORIES = '<div class="serif-display" style="font-size: 1.4rem;" vid="22">87</div>'
REFERENCE_COMMITS = '<div class="serif-display commits-value" vid="25">14,209</div>'
REFERENCE_ADDITIONS = (
    '<span class="mono-value" vid="32">2,410,892 '
    '<span class="mono-tiny" style="color:var(--text-muted)" vid="33">ADD</span></span>'
)
REFERENCE_DELETIONS = (
    '<span class="mono-value" vid="36">1,104,330 '
    '<span class="mono-tiny" style="color:var(--text-muted)" vid="37">DEL</span></span>'
)
REFERENCE_TOTAL_CHANGED = (
    '<div class="serif-display" style="font-size: 1.3rem; margin-top: 4px;" vid="40">3,515,222</div>'
)
REFERENCE_RENDER_LOOP = """        function render(t) {
            canvas.width = canvas.clientWidth; canvas.height = canvas.clientHeight;
            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.useProgram(prog);
            gl.enableVertexAttribArray(posLoc);
            gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
            gl.uniform2f(resLoc, canvas.width, canvas.height);
            gl.uniform1f(timeLoc, t * 0.0005);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
            requestAnimationFrame(render);
        }
        requestAnimationFrame(render);
"""
EXPORT_RENDER_LOOP = """        function render(t) {
            canvas.width = canvas.clientWidth; canvas.height = canvas.clientHeight;
            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.useProgram(prog);
            gl.enableVertexAttribArray(posLoc);
            gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
            gl.uniform2f(resLoc, canvas.width, canvas.height);
            gl.uniform1f(timeLoc, t * 0.0005);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
            if (!window.__PROFILE_STATS_STATIC__) requestAnimationFrame(render);
        }
        if (window.__PROFILE_STATS_STATIC__) {
            render(2400.0);
        } else {
            requestAnimationFrame(render);
        }
"""


def format_int(value: int) -> str:
    return f"{value:,}"


def format_percent(share: float) -> str:
    percentage = max(0.0, share) * 100
    if 0 < percentage < 1:
        return "<1%"
    return f"{round(percentage):.0f}%"


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def card_title(window_period: str) -> str:
    return f"{window_period} GitHub activity specimen"


def window_label(card: DashboardCardData) -> str:
    return f"{card.window_period.upper()} SO FAR"


def active_label() -> str:
    return "ACTIVE DAYS"


def active_days_display(card: DashboardCardData) -> str:
    return format_int(card.active_days)


def compact_language_name(name: str, limit: int = 14) -> str:
    cleaned = " ".join(name.split()).upper()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 3)].rstrip()}..."


def language_rows(card: DashboardCardData) -> list[tuple[str, str]]:
    if not card.language_segments:
        return [("NO DATA", "0%")]
    return [
        (compact_language_name(language), format_percent(share))
        for language, share in card.language_segments
    ]


def replace_exact(text: str, needle: str, replacement: str, label: str) -> str:
    if needle not in text:
        raise GitHubError(f"Failed to find {label} in {REFERENCE_HTML_PATH}")
    return text.replace(needle, replacement, 1)


def replace_regex(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise GitHubError(f"Failed to replace {label} in {REFERENCE_HTML_PATH}")
    return updated


def build_language_list_html(card: DashboardCardData) -> str:
    rows = []
    for language, percentage in language_rows(card):
        rows.append(
            "                    <div class=\"lang-item mono-tiny\">\n"
            f"                        <span style=\"color: var(--text-cream);\">{xml_escape(language)}</span>\n"
            f"                        <span>{xml_escape(percentage)}</span>\n"
            "                    </div>"
        )
    return "\n".join(rows)


def reference_template() -> str:
    if not REFERENCE_HTML_PATH.exists():
        raise GitHubError(f"Reference template not found at {REFERENCE_HTML_PATH}")
    return REFERENCE_HTML_PATH.read_text(encoding="utf-8")


def build_reference_html(card: DashboardCardData | None = None) -> str:
    if card is None:
        card = fake_dev_card()

    html_text = reference_template()
    html_text = replace_exact(
        html_text,
        REFERENCE_TITLE,
        f'<title vid="4">{xml_escape(card_title(card.window_period))}</title>',
        "reference title",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_ACTIVE_LABEL,
        f'<div class="mono-tiny" vid="16">{active_label()}</div>',
        "active label",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_CROSSHAIR,
        (
            '<div class="crosshair" vid="24"></div>\n'
            f'                <div class="mono-tiny" style="position: absolute; top: calc(50% - 64px); left: 50%; transform: translateX(-50%); letter-spacing: 0.2em; white-space: nowrap; font-size: 0.72rem; color: var(--text-cream); opacity: 1;">{window_label(card)}</div>'
        ),
        "center label",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_ACTIVE_CYCLE,
        f'<div class="serif-display" style="font-size: 1.8rem;" vid="17">{active_days_display(card)}</div>',
        "active cycle",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_REPOSITORIES,
        f'<div class="serif-display" style="font-size: 1.4rem;" vid="22">{format_int(card.repo_count)}</div>',
        "repository count",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_COMMITS,
        f'<div class="serif-display commits-value" vid="25">{format_int(card.total_commits)}</div>',
        "commit count",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_ADDITIONS,
        (
            f'<span class="mono-value" vid="32">{format_int(card.total_additions)} '
            '<span class="mono-tiny" style="color:var(--text-muted)" vid="33">ADD</span></span>'
        ),
        "additions",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_DELETIONS,
        (
            f'<span class="mono-value" vid="36">{format_int(card.total_deletions)} '
            '<span class="mono-tiny" style="color:var(--text-muted)" vid="37">DEL</span></span>'
        ),
        "deletions",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_TOTAL_CHANGED,
        (
            '<div class="serif-display" style="font-size: 1.3rem; margin-top: 4px;" vid="40">'
            f"{format_int(card.total_changed)}</div>"
        ),
        "total changed",
    )
    html_text = replace_regex(
        html_text,
        r'(?<=<div class="language-list" vid="42">\n)(?:.*?\n)+?(?=\s*</div>\n\s*</div>\n\s*</div>)',
        build_language_list_html(card) + "\n",
        "language list",
    )
    html_text = replace_exact(
        html_text,
        REFERENCE_RENDER_LOOP,
        EXPORT_RENDER_LOOP,
        "deterministic render loop",
    )
    return html_text


def render_activity_preview(card: DashboardCardData | None = None) -> str:
    return build_reference_html(card)


def render_activity_png(card: DashboardCardData | None = None) -> bytes:
    if card is None:
        card = fake_dev_card()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise GitHubError(
            "Playwright is required to export the activity card. Install it with `python3 -m pip install playwright` and `playwright install chromium`."
        ) from exc

    preview_html = build_reference_html(card)
    scale = export_scale()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 480, "height": 480},
                device_scale_factor=scale,
            )
            page.add_init_script("window.__PROFILE_STATS_STATIC__ = true;")
            page.set_content(preview_html, wait_until="load")
            page.wait_for_function(
                "() => document.fonts ? document.fonts.status === 'loaded' : true",
                timeout=10000,
            )
            page.wait_for_timeout(150)
            image = page.screenshot(type="png")
            browser.close()
            return image
    except Exception as exc:
        raise GitHubError(f"Failed to render activity card via Playwright: {exc}") from exc


def render_stats(
    username: str,
    window_start: datetime,
    window_end: datetime,
    window_period: str,
    collected: CollectedStats,
) -> str:
    del window_start, window_end

    image_reference = "./assets/activity-card.png"
    updated_at = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    period_lower = window_period.lower()

    if collected.commits:
        caption = (
            f"Generated from GitHub commit data for `{username}` for "
            f"{period_lower} so far. Updated {updated_at}."
        )
    else:
        caption = (
            f"No code-file commits detected for `{username}` for "
            f"{period_lower} so far. Updated {updated_at}."
        )

    return "\n".join(
        [
            '<p align="center">',
            f'  <img src="{image_reference}" alt="{card_title(window_period)}" width="100%" />',
            "</p>",
            "",
            f'<p align="center"><sub>{caption}</sub></p>',
        ]
    )


def replace_stats_block(readme: str, block: str) -> str:
    replacement = f"{START_MARKER}\n{block}\n{END_MARKER}"
    if START_MARKER in readme and END_MARKER in readme:
        before, remainder = readme.split(START_MARKER, 1)
        _, after = remainder.split(END_MARKER, 1)
        return f"{before}{replacement}{after}"
    if not readme.endswith("\n"):
        readme += "\n"
    return f"{readme}\n{replacement}\n"
