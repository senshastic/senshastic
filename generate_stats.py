#!/usr/bin/env python3
"""
Weekly coding stats card generator for GitHub profile README.
Queries the GitHub API, then writes stats.svg.
"""
import os, sys, time, json
import requests
from datetime import datetime, timedelta, timezone

# ── config ────────────────────────────────────────────────────────────────────
TOKEN    = os.environ.get('STATS_TOKEN') or os.environ.get('GITHUB_TOKEN', '')
USERNAME = os.environ.get('GITHUB_USERNAME', 'senshastic')
DAYS     = int(os.environ.get('STATS_DAYS', '30'))
OUTPUT   = os.environ.get('OUTPUT_FILE', 'stats.svg')

if not TOKEN:
    print('WARNING: no token found, API calls may be rate-limited', file=sys.stderr)

HEADERS = {
    'Authorization': f'token {TOKEN}',
    'Accept': 'application/vnd.github.v3+json',
    'User-Agent': 'senshastic-profile-stats/1.0',
}

def api(url, params=None, retries=3):
    for attempt in range(retries):
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code == 202:        # stats still computing
            time.sleep(3)
            continue
        if r.status_code == 409:        # empty repo
            return {}
        if r.ok:
            return r.json()
        if r.status_code in (404, 403):
            return {}
        time.sleep(1)
    return {}

# ── date range ────────────────────────────────────────────────────────────────
now      = datetime.now(timezone.utc)
since_dt = now - timedelta(days=DAYS)
since    = since_dt.isoformat()

def fmt_date(dt):
    return dt.strftime('%b ') + str(dt.day)   # "Mar 24" no zero-pad

period = f"{fmt_date(since_dt)} – {fmt_date(now)}, {now.year}"

# ── events ────────────────────────────────────────────────────────────────────
print(f'Fetching events for @{USERNAME}, last {DAYS} days...')
raw = api(f'https://api.github.com/users/{USERNAME}/events', {'per_page': 100})
if not isinstance(raw, list):
    raw = []

push_events = [
    e for e in raw
    if e.get('type') == 'PushEvent' and e.get('created_at', '') >= since
]
print(f'  {len(push_events)} push events found')

# ── collect commit SHAs per repo ──────────────────────────────────────────────
repos_commits = {}   # full_name -> [sha, ...]
for e in push_events:
    repo = e['repo']['name']
    for c in e['payload'].get('commits', []):
        repos_commits.setdefault(repo, []).append(c['sha'])

# ── fetch per-commit stats (cap per repo to respect rate limits) ──────────────
total_add = total_del = total_commits = 0
repo_stats = {}

for repo, shas in repos_commits.items():
    radd = rdel = 0
    seen = list(dict.fromkeys(shas))[:10]   # dedupe, max 10 per repo
    for sha in seen:
        data = api(f'https://api.github.com/repos/{repo}/commits/{sha}')
        s = data.get('stats', {})
        radd += s.get('additions', 0)
        rdel += s.get('deletions', 0)
    repo_stats[repo] = {'add': radd, 'del': rdel, 'commits': len(shas)}
    total_add     += radd
    total_del     += rdel
    total_commits += len(shas)

# ── language breakdown ────────────────────────────────────────────────────────
lang_bytes = {}
for repo in repos_commits:
    for lang, b in api(f'https://api.github.com/repos/{repo}/languages').items():
        lang_bytes[lang] = lang_bytes.get(lang, 0) + b

total_lb  = sum(lang_bytes.values()) or 1
top_langs = sorted(lang_bytes.items(), key=lambda x: -x[1])[:5]

# ── top repos ─────────────────────────────────────────────────────────────────
top_repos = sorted(
    repo_stats.items(),
    key=lambda x: -(x[1]['add'] + x[1]['del'])
)[:5]

print(f'  +{total_add} / -{total_del} lines, {len(repos_commits)} repos, {total_commits} commits')
print(f'  langs: {[l for l, _ in top_langs]}')

# ── SVG ───────────────────────────────────────────────────────────────────────
def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def fmt(n):
    return f'{n:,}'

# palette
BG     = '#0d0d0d'
BORDER = '#242424'
GOLD   = '#f5c518'
WHITE  = '#e0e0e0'
TEXT2  = '#888888'
TEXT3  = '#3a3a3a'
GREEN  = '#3ddc84'
RED    = '#e8433a'
BLUE   = '#4a9eff'

LANG_COLORS = {
    'JavaScript': '#f1e05a', 'TypeScript': '#3178c6', 'Python':  '#3572A5',
    'HTML':       '#e34c26', 'CSS':        '#563d7c', 'SCSS':    '#c6538c',
    'Shell':      '#89e051', 'Rust':       '#dea584', 'Go':      '#00ADD8',
    'Java':       '#b07219', 'C++':        '#f34b7d', 'C':       '#555555',
    'Ruby':       '#701516', 'PHP':        '#4F5D95', 'Kotlin':  '#A97BFF',
    'Swift':      '#F05138', 'Dart':       '#00B4AB', 'Vue':     '#41b883',
    'Svelte':     '#ff3e00', 'Lua':        '#000080',
}

W   = 600
PAD = 24

# section heights
H_HEADER  = 56
H_STATS   = 78
H_LANGS   = 34 + max(len(top_langs), 1) * 26 + 10
H_REPOS   = (32 + len(top_repos) * 24 + 10) if top_repos else 0
H_FOOT    = 18
H_TOTAL   = H_HEADER + H_STATS + H_LANGS + H_REPOS + H_FOOT

out = []

def line(s):
    out.append(s)

line(f'<svg width="{W}" height="{H_TOTAL}" viewBox="0 0 {W} {H_TOTAL}" xmlns="http://www.w3.org/2000/svg">')
line(f'<rect width="{W}" height="{H_TOTAL}" rx="6" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>')

# top accent bar
line(f'<rect x="0" y="0" width="72" height="3" rx="1" fill="{GOLD}"/>')
line(f'<rect x="74" y="0" width="18" height="3" rx="1" fill="{TEXT3}"/>')
line(f'<rect x="94" y="0" width="36" height="3" rx="1" fill="{BORDER}"/>')

# ── header
y = 35
line(f'<text x="{PAD}" y="{y}" font-family="\'Segoe UI\',system-ui,sans-serif" '
     f'font-size="11" font-weight="700" fill="{GOLD}" letter-spacing="2.5">'
     f'CODING STATS · LAST {DAYS} DAYS</text>')
line(f'<text x="{W - PAD}" y="{y}" font-family="\'Courier New\',Consolas,monospace" '
     f'font-size="10" fill="{TEXT2}" text-anchor="end">{esc(period)}</text>')

# divider
y = H_HEADER
line(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{BORDER}"/>')

# ── stats row
cells = [
    (f'+{fmt(total_add)}',       'lines added',   GREEN),
    (f'-{fmt(total_del)}',       'lines removed', RED),
    (str(len(repos_commits)),    'repos',          BLUE),
    (str(total_commits),         'commits',        GOLD),
]
cell_w = (W - PAD * 2) // 4
for i, (val, label, color) in enumerate(cells):
    cx = PAD + i * cell_w + cell_w // 2
    vy = H_HEADER + 34
    line(f'<text x="{cx}" y="{vy}" font-family="\'Segoe UI\',system-ui,sans-serif" '
         f'font-size="21" font-weight="700" fill="{color}" text-anchor="middle">{esc(val)}</text>')
    line(f'<text x="{cx}" y="{vy + 17}" font-family="\'Segoe UI\',system-ui,sans-serif" '
         f'font-size="9" fill="{TEXT2}" text-anchor="middle" letter-spacing="0.5">{esc(label)}</text>')

# divider
y = H_HEADER + H_STATS
line(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{BORDER}"/>')

# ── languages
y = H_HEADER + H_STATS + 18
line(f'<text x="{PAD}" y="{y}" font-family="\'Segoe UI\',system-ui,sans-serif" '
     f'font-size="9" font-weight="700" fill="{TEXT2}" letter-spacing="2.5">LANGUAGES</text>')

name_w  = 108
pct_w   = 36
bar_w   = W - PAD * 2 - name_w - pct_w
bar_h   = 5

if top_langs:
    for i, (lang, b) in enumerate(top_langs):
        ly      = y + 16 + i * 26
        pct     = round(b / total_lb * 100)
        fill    = LANG_COLORS.get(lang, TEXT2)
        track_x = PAD + name_w
        filled  = max(3, round(bar_w * pct / 100))
        line(f'<text x="{PAD}" y="{ly + 4}" font-family="\'Segoe UI\',system-ui,sans-serif" '
             f'font-size="11" fill="{WHITE}">{esc(lang)}</text>')
        line(f'<rect x="{track_x}" y="{ly - 1}" width="{bar_w}" height="{bar_h}" rx="2" fill="{BORDER}"/>')
        line(f'<rect x="{track_x}" y="{ly - 1}" width="{filled}" height="{bar_h}" rx="2" fill="{fill}"/>')
        line(f'<text x="{W - PAD}" y="{ly + 4}" font-family="\'Courier New\',Consolas,monospace" '
             f'font-size="10" fill="{TEXT2}" text-anchor="end">{pct}%</text>')
else:
    line(f'<text x="{PAD}" y="{y + 20}" font-family="\'Segoe UI\',system-ui,sans-serif" '
         f'font-size="11" fill="{TEXT2}">No activity in this period</text>')

# ── top repos
if top_repos:
    y = H_HEADER + H_STATS + H_LANGS
    line(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{BORDER}"/>')
    y += 18
    line(f'<text x="{PAD}" y="{y}" font-family="\'Segoe UI\',system-ui,sans-serif" '
         f'font-size="9" font-weight="700" fill="{TEXT2}" letter-spacing="2.5">TOP REPOS</text>')
    for i, (repo, s) in enumerate(top_repos):
        ry    = y + 16 + i * 24
        short = repo.split('/')[-1]
        add_s = f'+{fmt(s["add"])}'
        del_s = f'-{fmt(s["del"])}'
        commits_s = f'{s["commits"]}c'
        line(f'<text x="{PAD}" y="{ry + 4}" font-family="\'Segoe UI\',system-ui,sans-serif" '
             f'font-size="12" fill="{WHITE}">{esc(short)}</text>')
        line(f'<text x="{W - PAD}" y="{ry + 4}" font-family="\'Courier New\',Consolas,monospace" '
             f'font-size="10" fill="{TEXT3}" text-anchor="end">'
             f'<tspan fill="{GREEN}">{esc(add_s)}</tspan>  '
             f'<tspan fill="{RED}">{esc(del_s)}</tspan>  '
             f'<tspan fill="{TEXT2}">{commits_s}</tspan></text>')

line('</svg>')

svg = '\n'.join(out)
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(svg)

print(f'Written {OUTPUT} ({H_TOTAL}px)')
