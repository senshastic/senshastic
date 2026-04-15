#!/usr/bin/env python3
"""
Weekly coding stats card generator for GitHub profile README.
Local testing mirror of the GitHub Actions JavaScript version.
Queries the GitHub API, then writes stats.svg.
"""
import os, sys, time
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
        if r.status_code == 202:
            time.sleep(3)
            continue
        if r.status_code in (409, 404, 403):
            return {}
        if r.ok:
            return r.json()
        time.sleep(1)
    return {}

def api_list(url, params=None, max_pages=2):
    results = []
    p = dict(params or {})
    p['per_page'] = 100
    for page in range(1, max_pages + 1):
        p['page'] = page
        data = api(url, p)
        if not isinstance(data, list) or not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
    return results

# ── date range ────────────────────────────────────────────────────────────────
now      = datetime.now(timezone.utc)
since_dt = now - timedelta(days=DAYS)
since    = since_dt.isoformat()
until    = now.isoformat()

def fmt_date(dt):
    return dt.strftime('%b ') + str(dt.day)

period = f"{fmt_date(since_dt)} – {fmt_date(now)}"

# ── fetch events (up to 300) ──────────────────────────────────────────────────
print(f'Fetching events for @{USERNAME}, last {DAYS} days...')
all_events = []
for page in range(1, 4):
    batch = api(f'https://api.github.com/users/{USERNAME}/events',
                {'per_page': 100, 'page': page})
    if not isinstance(batch, list) or not batch:
        break
    relevant = [e for e in batch if e.get('created_at', '') >= since]
    all_events.extend(relevant)
    if len(relevant) < len(batch):
        break

print(f'  {len(all_events)} events in window')

# ── collect active repos from multiple event types ────────────────────────────
active_repos = set()
seed_shas    = {}   # full_name -> set of SHAs

for e in all_events:
    etype = e.get('type', '')
    repo  = e['repo']['name']

    if etype == 'PushEvent':
        active_repos.add(repo)
        seed_shas.setdefault(repo, set())
        # capture ALL commits in payload (not just head)
        for c in e['payload'].get('commits', []):
            seed_shas[repo].add(c['sha'])
        if e['payload'].get('head'):
            seed_shas[repo].add(e['payload']['head'])

    elif etype == 'CreateEvent' and e['payload'].get('ref_type') == 'repository':
        # newly created repo — fetch its initial commits below
        active_repos.add(repo)

    elif etype == 'ForkEvent':
        forkee = e['payload'].get('forkee', {})
        if forkee.get('full_name'):
            active_repos.add(forkee['full_name'])


# ── also scan user's own repos sorted by push time ───────────────────────────
# catches repos missed by the events API (e.g. merge-only activity)
user_repos = api_list(f'https://api.github.com/users/{USERNAME}/repos',
                      {'sort': 'pushed', 'direction': 'desc'}, max_pages=1)
for repo in user_repos:
    if repo.get('pushed_at', '') < since:
        break
    full_name = repo['full_name']
    if full_name in active_repos:
        continue
    check = api_list(f'https://api.github.com/repos/{full_name}/commits',
                     {'since': since, 'until': until},
                     max_pages=1)
    if check:
        active_repos.add(full_name)

print(f'  active repos: {", ".join(sorted(active_repos))}')

# ── for each repo, fetch ALL commits by user in range ────────────────────────
# catches initial pushes to new repos + big batches truncated in push payload
repo_commit_shas = {}   # full_name -> set of SHAs

for repo in active_repos:
    shas = set(seed_shas.get(repo, set()))
    commits = api_list(
        f'https://api.github.com/repos/{repo}/commits',
        {'author': USERNAME, 'since': since, 'until': until},
        max_pages=2
    )
    for c in commits:
        if isinstance(c, dict) and c.get('sha'):
            shas.add(c['sha'])
    if shas:
        repo_commit_shas[repo] = shas

# ── fetch per-commit stats (capped at 15 per repo) ───────────────────────────
total_add = total_del = total_commits = 0
repo_stats = {}

for repo, shas in repo_commit_shas.items():
    radd = rdel = 0
    for sha in list(shas)[:15]:
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
for repo in repo_commit_shas:
    for lang, b in api(f'https://api.github.com/repos/{repo}/languages').items():
        lang_bytes[lang] = lang_bytes.get(lang, 0) + b

total_lb  = sum(lang_bytes.values()) or 1
top_langs = sorted(lang_bytes.items(), key=lambda x: -x[1])[:5]

top_repos = sorted(
    repo_stats.items(),
    key=lambda x: -(x[1]['add'] + x[1]['del'])
)[:5]

repo_count = len(repo_commit_shas)
print(f'  +{total_add} / -{total_del}, {repo_count} repos, {total_commits} commits')
print(f'  langs: {[l for l, _ in top_langs]}')

# ═════════════════════════════════════════════════════════════════════════════
# SVG — SNESHLIB design
# ═════════════════════════════════════════════════════════════════════════════

def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def fmt(n):
    return f'{n:,}'

# ── palette (SNESHLIB tokens) ─────────────────────────────────────────────────
CREAM       = '#d0b575'
CREAM_MUTED = '#7a6035'
GREEN       = '#7ec87e'
RED         = '#c47070'
BLUE        = '#7ab0cf'
PURPLE      = '#a08fd4'
WHITE       = '#e0d5c8'
MUTED       = '#5a4e42'
DIM         = '#302820'

LANG_COLORS = {
    'JavaScript': '#f1e05a', 'TypeScript': '#3178c6', 'Python':  '#3572A5',
    'HTML':       '#e34c26', 'CSS':        '#563d7c', 'SCSS':    '#c6538c',
    'Shell':      '#89e051', 'Rust':       '#dea584', 'Go':      '#00ADD8',
    'Java':       '#b07219', 'C++':        '#f34b7d', 'C':       '#555555',
    'Ruby':       '#701516', 'PHP':        '#4F5D95', 'Kotlin':  '#A97BFF',
    'Swift':      '#F05138', 'Dart':       '#00B4AB', 'Vue':     '#41b883',
    'Svelte':     '#ff3e00', 'Lua':        '#000080', 'Less':    '#1d365d',
}

# ── layout ────────────────────────────────────────────────────────────────────
W      = 740
PAD    = 22
RX_C   = 14

SP_Y   = 55
SP_H   = 94

BP_GAP = 14
BP_Y   = SP_Y + SP_H + 22
BP_W   = (W - PAD * 2 - BP_GAP) // 2

LANG_ROWS = max(len(top_langs), 1)
REPO_ROWS = len(top_repos)
ROW_H     = 28
BP_H      = 36 + max(LANG_ROWS, REPO_ROWS) * ROW_H + 14

H_FOOT  = 34
H_TOTAL = BP_Y + BP_H + H_FOOT

PERIM    = 2 * (W + H_TOTAL)
DASH_VIS = round(PERIM * 0.18)
DASH_GAP = PERIM - DASH_VIS

MF = "font-family=\"'Cascadia Code','Fira Code','SF Mono',ui-monospace,monospace\""

out = []
def line(s): out.append(s)

line(f'<svg width="{W}" height="{H_TOTAL}" viewBox="0 0 {W} {H_TOTAL}" xmlns="http://www.w3.org/2000/svg">')
line('<defs>')
line('<style>')
line('  @keyframes fadein { from{opacity:0} to{opacity:1} }')
line('  @keyframes pulse  { 0%,100%{opacity:1} 50%{opacity:.4} }')
line(f'  @keyframes snake  {{ 0%{{stroke-dashoffset:0}} 100%{{stroke-dashoffset:-{PERIM}}} }}')
line('  .card   { animation: fadein .4s ease-out both; }')
line('  .snake  { animation: snake 5s linear infinite; }')
line('  .p1     { animation: pulse 4s ease-in-out infinite; }')
line('  .p2     { animation: pulse 4s ease-in-out infinite .8s; }')
line('  .p3     { animation: pulse 4s ease-in-out infinite 1.6s; }')
line('  .p4     { animation: pulse 4s ease-in-out infinite 2.4s; }')
line('</style>')
line('<linearGradient id="bg" x1="0" y1="0" x2=".6" y2="1">')
line('  <stop offset="0%"   stop-color="#1c1625"/>')
line('  <stop offset="50%"  stop-color="#140f1c"/>')
line('  <stop offset="100%" stop-color="#0e0b14"/>')
line('</linearGradient>')
line('<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">')
line('  <stop offset="0%"  stop-color="#ffffff" stop-opacity=".07"/>')
line('  <stop offset="60%" stop-color="#ffffff" stop-opacity="0"/>')
line('</linearGradient>')
line(f'<linearGradient id="creamBar" x1="0" y1="0" x2="1" y2="0">')
line(f'  <stop offset="0%"   stop-color="{CREAM}" stop-opacity="0"/>')
line(f'  <stop offset="20%"  stop-color="{CREAM}" stop-opacity=".7"/>')
line(f'  <stop offset="80%"  stop-color="{CREAM}" stop-opacity=".7"/>')
line(f'  <stop offset="100%" stop-color="{CREAM}" stop-opacity="0"/>')
line('</linearGradient>')
line('<filter id="creamGlow" x="-40%" y="-40%" width="180%" height="180%">')
line('  <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="b"/>')
line('  <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>')
line('</filter>')
line('<filter id="numglow" x="-60%" y="-60%" width="220%" height="220%">')
line('  <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="b"/>')
line('  <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>')
line('</filter>')
line('<pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse">')
line('  <path d="M8 0 L0 8" stroke="#ffffff" stroke-opacity=".035" stroke-width=".5"/>')
line('  <path d="M0 0 L8 8" stroke="#ffffff" stroke-opacity=".02"  stroke-width=".5"/>')
line('</pattern>')
for lang, _ in top_langs:
    c    = LANG_COLORS.get(lang, MUTED)
    safe = ''.join(x if x.isalnum() else '_' for x in lang)
    line(f'<linearGradient id="lb_{safe}" x1="0" y1="0" x2="1" y2="0">')
    line(f'  <stop offset="0%"   stop-color="{c}" stop-opacity=".9"/>')
    line(f'  <stop offset="70%"  stop-color="{c}" stop-opacity=".5"/>')
    line(f'  <stop offset="100%" stop-color="{c}" stop-opacity=".1"/>')
    line(f'</linearGradient>')
line(f'<clipPath id="clip"><rect width="{W}" height="{H_TOTAL}" rx="{RX_C}"/></clipPath>')
line('</defs>')

line('<g class="card">')
line('<g clip-path="url(#clip)">')
line(f'<rect width="{W}" height="{H_TOTAL}" fill="url(#bg)"/>')
line(f'<rect width="{W}" height="{H_TOTAL}" fill="url(#hatch)"/>')
line('</g>')
line(f'<rect width="{W}" height="{H_TOTAL}" rx="{RX_C}" fill="none" stroke="#ffffff" stroke-opacity=".08" stroke-width="1"/>')
line(f'<rect width="{W}" height="{H_TOTAL}" rx="{RX_C}" fill="none" stroke="{CREAM}" stroke-opacity=".55" stroke-width="1.5" stroke-dasharray="{DASH_VIS} {DASH_GAP}" class="snake"/>')

line(f'<text x="{PAD}" y="38" {MF} font-size="11" font-weight="700" fill="{CREAM}" letter-spacing="3.5" filter="url(#creamGlow)">CODING STATS</text>')
line(f'<text x="{PAD+134}" y="38" {MF} font-size="10" fill="{MUTED}" letter-spacing="1.5">\u00b7 LAST {DAYS} DAYS</text>')
line(f'<text x="{W-PAD}" y="38" {MF} font-size="9.5" fill="{MUTED}" text-anchor="end">{esc(period)}</text>')

line(f'<rect x="{PAD}" y="{SP_Y}" width="{W-PAD*2}" height="{SP_H}" rx="10" fill="#ffffff" fill-opacity=".05"/>')
line(f'<rect x="{PAD}" y="{SP_Y}" width="{W-PAD*2}" height="{SP_H}" rx="10" fill="url(#sheen)"/>')
line(f'<rect x="{PAD}" y="{SP_Y}" width="{W-PAD*2}" height="{SP_H}" rx="10" fill="none" stroke="#ffffff" stroke-opacity=".1" stroke-width="1"/>')
line(f'<rect x="{PAD+1}" y="{SP_Y+1}" width="{W-PAD*2-2}" height="1" fill="#ffffff" fill-opacity=".06"/>')

stat_defs = [
    (f'+{fmt(total_add)}', 'lines added',   GREEN,  'p1'),
    (f'-{fmt(total_del)}', 'lines removed', RED,    'p2'),
    (str(repo_count),      'repos',         BLUE,   'p3'),
    (str(total_commits),   'commits',       PURPLE, 'p4'),
]
CW = (W - PAD * 2) // 4
for i, (val, label, color, cls) in enumerate(stat_defs):
    cx = PAD + i * CW + CW // 2
    if i > 0:
        sx = PAD + i * CW
        line(f'<line x1="{sx}" y1="{SP_Y+16}" x2="{sx}" y2="{SP_Y+SP_H-16}" stroke="#ffffff" stroke-opacity=".06" stroke-width="1"/>')
    fs = 25 if len(val) <= 5 else (21 if len(val) <= 7 else 18)
    line(f'<text x="{cx}" y="{SP_Y+52}" {MF} font-size="{fs}" font-weight="700" fill="{color}" text-anchor="middle" filter="url(#numglow)" class="{cls}">{esc(val)}</text>')
    line(f'<text x="{cx}" y="{SP_Y+72}" {MF} font-size="8.5" fill="{MUTED}" text-anchor="middle" letter-spacing="1.5">{label}</text>')

line(f'<rect x="{PAD}" y="{SP_Y+SP_H+10}" width="{W-PAD*2}" height="1" fill="url(#creamBar)" opacity=".5"/>')

def glass_panel(x, y, w, h):
    line(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#ffffff" fill-opacity=".05"/>')
    line(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="url(#sheen)"/>')
    line(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" stroke="#ffffff" stroke-opacity=".09" stroke-width="1"/>')
    line(f'<rect x="{x+1}" y="{y+1}" width="{w-2}" height="1" fill="#ffffff" fill-opacity=".05"/>')

LX  = PAD
RX2 = PAD + BP_W + BP_GAP
glass_panel(LX, BP_Y, BP_W, BP_H)
glass_panel(RX2, BP_Y, BP_W, BP_H)

lhd_y = BP_Y + 18
line(f'<text x="{LX+12}" y="{lhd_y}" {MF} font-size="7" font-weight="700" fill="{CREAM_MUTED}" letter-spacing="3">LANGUAGES</text>')

BAR_X = LX + 96
BAR_W = BP_W - 96 - 30
BAR_H = 5

for i, (lang, b) in enumerate(top_langs):
    ly   = lhd_y + 18 + i * ROW_H
    pct  = round(b / total_lb * 100)
    lc   = LANG_COLORS.get(lang, MUTED)
    safe = ''.join(x if x.isalnum() else '_' for x in lang)
    fill = max(4, round(BAR_W * pct / 100))
    line(f'<circle cx="{LX+17}" cy="{ly+3}" r="3.5" fill="{lc}" opacity=".9"/>')
    line(f'<text x="{LX+26}" y="{ly+7}" {MF} font-size="10.5" fill="{WHITE}">{esc(lang)}</text>')
    line(f'<rect x="{BAR_X}" y="{ly}" width="{BAR_W}" height="{BAR_H}" rx="2.5" fill="#ffffff" fill-opacity=".05"/>')
    line(f'<rect x="{BAR_X}" y="{ly}" width="{fill}" height="{BAR_H}" rx="2.5" fill="url(#lb_{safe})"/>')
    line(f'<text x="{LX+BP_W-8}" y="{ly+7}" {MF} font-size="9" fill="{MUTED}" text-anchor="end">{pct}%</text>')

rhd_y = BP_Y + 18
line(f'<text x="{RX2+12}" y="{rhd_y}" {MF} font-size="7" font-weight="700" fill="{CREAM_MUTED}" letter-spacing="3">TOP REPOS</text>')

for i, (repo, s) in enumerate(top_repos):
    ry    = rhd_y + 18 + i * ROW_H
    short = repo.split('/')[-1]
    if i % 2 == 0:
        line(f'<rect x="{RX2+6}" y="{ry-10}" width="{BP_W-12}" height="{ROW_H-2}" rx="5" fill="#ffffff" fill-opacity=".03"/>')
    line(f'<text x="{RX2+14}" y="{ry+6}" {MF} font-size="11" fill="{WHITE}">{esc(short)}</text>')
    line(f'<text x="{RX2+BP_W-8}" y="{ry+6}" {MF} font-size="9" text-anchor="end">'
         f'<tspan fill="{GREEN}">+{fmt(s["add"])}</tspan>'
         f'<tspan fill="{DIM}">  </tspan>'
         f'<tspan fill="{RED}">-{fmt(s["del"])}</tspan>'
         f'<tspan fill="{DIM}">  </tspan>'
         f'<tspan fill="{MUTED}">{s["commits"]}c</tspan>'
         f'</text>')

line(f'<text x="{W//2}" y="{H_TOTAL-12}" {MF} font-size="7.5" fill="{CREAM_MUTED}" text-anchor="middle" letter-spacing="2.5" opacity=".6">AUTO \u00b7 UPDATED \u00b7 WEEKLY</text>')
line('</g>')
line('</svg>')

svg = '\n'.join(out)
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(svg)

print(f'Written {OUTPUT} ({W}x{H_TOTAL}px)')
