#!/usr/bin/env python3
"""Build the public development site (ttdev) from this repo.

    python3 devsite/build.py <out-dir>

Everything on the site comes from files already in this repo, so it can never
disagree with what devices are served:

    releases/<v>/NOTES.md, SOURCE.txt, *.bin   one card per firmware release
    releases/<v>/WITHDRAWN                     optional; its text is the reason
    channels/stable, channels/beta             what each channel serves now
    channels/history.log                       when each promotion happened
    changelog/configurator.md, feeds.md        hand-written, reader-facing

The one live number, trackers connected now, is fetched by the page from
/live/counts.json, which ttdash/collect.py writes next to the published tree.

Settings arrive as environment variables from release.sh (channel.conf):
SITE_URL, DEV_SITE_URL, CONTACT_EMAIL, GITHUB_ORG. The last two may be empty;
the page leaves their sections out.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TZ = ZoneInfo("America/Edmonton")
VERSION_RE = re.compile(r"^v(\d+\.\d+\.\d+)-etr\.(\d+)$")

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
DEV_SITE_URL = os.environ.get("DEV_SITE_URL", "").rstrip("/")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")

# Bootstrap Icons 1.11.3 (MIT), inlined: the public sites use no emoji.
ICONS = json.load(open(os.path.join(HERE, "icons.json")))


def icon(name, cls="bi"):
    return (f'<svg class="{cls}" viewBox="0 0 16 16" width="16" height="16" '
            f'fill="currentColor" aria-hidden="true">{ICONS[name]}</svg>')


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- markdown --
# Just enough for release notes and changelogs: headings, bullet lists,
# paragraphs, **bold**, `code`, [text](url) and bare https:// links.

def inline(text):
    out, pos = [], 0
    for m in re.finditer(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s)<]+)", text):
        out.append(esc(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<code>{esc(m.group(1))}</code>")
        elif m.group(2) is not None:
            out.append(f"<strong>{inline(m.group(2))}</strong>")
        elif m.group(3) is not None:
            out.append(f'<a href="{esc(m.group(4))}">{esc(m.group(3))}</a>')
        else:
            url = m.group(5).rstrip(".,;:")
            out.append(f'<a href="{esc(url)}">{esc(url)}</a>{esc(m.group(5)[len(url):])}')
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def markdown(src, base_level=3):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in src.splitlines():
        s = line.strip()
        h = re.match(r"^(#{1,6})\s+(.*)$", s)
        if not s:
            flush()
        elif h:
            flush()
            lvl = min(6, base_level + len(h.group(1)) - 1)
            out.append(f"<h{lvl}>{inline(h.group(2))}</h{lvl}>")
        elif s.startswith("- "):
            if para:
                flush()
            items.append(s[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + s          # wrapped bullet
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


# -------------------------------------------------------------------- data --

def vkey(v):
    m = VERSION_RE.match(v)
    return int(m.group(2)) if m else -1


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return default


def local_date(iso):
    t = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(TZ)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_history():
    events = []
    for line in read(os.path.join(REPO, "channels/history.log")).splitlines():
        m = re.match(r"^(\S+) (beta|stable) (\S+) -> (\S+)$", line.strip())
        if m:
            events.append({"at": m.group(1), "channel": m.group(2), "from": m.group(3), "to": m.group(4)})
    return events


def load_releases(events, channels):
    rels = []
    root = os.path.join(REPO, "releases")
    for v in sorted(os.listdir(root), key=vkey, reverse=True):
        d = os.path.join(root, v)
        m = VERSION_RE.match(v)
        if not m or not os.path.isdir(d):
            continue
        src = {}
        for line in read(os.path.join(d, "SOURCE.txt")).splitlines():
            k, _, val = line.partition(":")
            src[k.strip()] = val.strip()
        to_beta = next((e["at"] for e in events if e["channel"] == "beta" and e["to"] == v), None)
        to_stable = next((e["at"] for e in events if e["channel"] == "stable" and e["to"] == v), None)
        # Superseded = the last channel holding it moved on. A release can sit on
        # both channels at once (promoted to stable while still the beta).
        left = None
        if v not in channels.values():
            moves = [e["at"] for e in events if e["from"] == v]
            left = moves[-1] if moves else None
        withdrawn = read(os.path.join(d, "WITHDRAWN")).strip() or None
        if withdrawn:
            status = "withdrawn"
        elif channels.get("stable") == v:
            status = "stable"
        elif channels.get("beta") == v:
            status = "beta"
        elif to_beta or to_stable:
            status = "superseded"
        else:
            status = "built"
        files = []
        for kind in ("factory", "ota"):
            name = f"firmware-{v}.{kind}.bin"
            p = os.path.join(d, name)
            if os.path.exists(p):
                files.append({"kind": kind, "name": name, "bytes": os.path.getsize(p),
                              "sha256": sha256(p), "url": f"{SITE_URL}/firmware/{name}"})
        esphome = src.get("esphome image", "").rpartition(":")[2] or None
        rels.append({
            "version": v, "upstream": "v" + m.group(1), "etr": int(m.group(2)),
            "status": status, "withdrawn": withdrawn,
            "built": src.get("built", "").split(" ")[0] or None,
            "to_beta": to_beta, "to_stable": to_stable, "superseded": left,
            "esphome": esphome,
            "firmware_commit": src.get("firmware repo", "").split(" ")[0] or None,
            "component_commit": src.get("component repo", "").split(" ")[0] or None,
            "notes_md": read(os.path.join(d, "NOTES.md")).strip(),
            "files": files,
        })
    return rels


def load_changelog(component):
    """`## YYYY-MM-DD — Title` blocks; anything before the first is preamble."""
    text = read(os.path.join(REPO, "changelog", f"{component}.md"))
    entries = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[—-]+\s*(.+)$", head.strip())
        if not m:
            sys.exit(f"changelog/{component}.md: bad heading '## {head.strip()}' (want '## YYYY-MM-DD — Title')")
        entries.append({"component": component, "date": m.group(1), "title": m.group(2).strip(),
                        "body_md": body.strip()})
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


# ------------------------------------------------------------------- pages --

NAV = [("index", "Overview"), ("firmware", "Firmware"), ("configurator", "Configurator"),
       ("feeds", "Feeds"), ("beta", "Beta program")]
COMPONENT_NAMES = {"firmware": "Firmware", "configurator": "Configurator", "feeds": "Feeds"}
STATUS_LABEL = {"stable": "Stable", "beta": "Beta", "superseded": "Superseded",
                "withdrawn": "Withdrawn", "built": "Built, not released"}

CSS = """
:root{--bg:#0e1020;--panel:#161a2e;--panel2:#1b2040;--line:#262c4a;--text:#eef0f8;--muted:#8b91ad;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--etr:#5b8cff;--led:#2346c8;--navy:#27368f;color-scheme:dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--etr)}a:hover{color:#8fb0ff}
code{font:.86em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#0b0d1a;border:1px solid var(--line);border-radius:4px;padding:1px 5px;overflow-wrap:anywhere}
.bi{width:1em;height:1em;vertical-align:-.125em;flex:none}
header{background:linear-gradient(90deg,#1b2668,var(--navy));border-bottom:3px solid var(--led)}
.bar{max-width:980px;margin:0 auto;padding:14px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.bar img{height:34px;width:auto;filter:brightness(0) invert(1)}
.bar .rule{width:1px;height:30px;background:rgba(255,255,255,.35)}
.bar h1{font-size:1.1rem;margin:0;color:#fff;line-height:1.2}
.bar .sub{color:#c5cbef;font-size:.82rem}
nav{background:#12162a;border-bottom:1px solid var(--line)}
nav ul{max-width:980px;margin:0 auto;padding:0 8px;list-style:none;display:flex;flex-wrap:wrap}
nav a{display:block;padding:10px 10px;color:var(--muted);text-decoration:none;font-size:.9rem;border-bottom:2px solid transparent}
nav a:hover{color:var(--text)}
nav a[aria-current=page]{color:#fff;border-bottom-color:var(--etr)}
nav li.ext{margin-left:auto}nav li.ext a,nav li.ext~li a{display:inline-flex;align-items:center;gap:5px}
nav .bi{width:.75em;height:.75em;opacity:.7}
main{max-width:980px;margin:0 auto;padding:8px 16px 40px}
h2{font-size:1.15rem;margin:30px 0 10px}
h3{font-size:1rem;margin:18px 0 6px}
h4{font-size:.8rem;margin:14px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.lede{color:#c9cde3;max-width:68ch}
.muted{color:var(--muted)}.small{font-size:.84rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:14px 0;align-items:start}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.card.stable{border-color:#1f5f3a}.card.beta{border-color:#4d3b12}.card.live{border-color:var(--led)}
.card .k{color:var(--muted);font-size:.8rem;display:flex;align-items:center;gap:6px}
.card .v{font-size:1.35rem;font-weight:600;margin:2px 0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.pill{display:inline-block;font-size:.72rem;font-weight:600;padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);vertical-align:2px;white-space:nowrap}
.pill.stable{color:#7ee2a0;border-color:#1f5f3a;background:#10261a}
.pill.beta{color:#f0c35a;border-color:#4d3b12;background:#2a210c}
.pill.withdrawn{color:#ff9b94;border-color:#5c1f1c;background:#2a1110}
.pill.c-firmware{color:#9db8ff;border-color:#2a3a78}.pill.c-configurator{color:#c9a7ff;border-color:#43307a}.pill.c-feeds{color:#7fd6e0;border-color:#1e5560}
.rel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:4px 18px 14px;margin:14px 0}
.rel.stable{border-left:4px solid var(--ok)}.rel.beta{border-left:4px solid var(--warn)}.rel.withdrawn{border-left:4px solid var(--bad)}
.rel h3{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:1.1rem;margin:14px 0 4px}
.rel h3 a{color:inherit;text-decoration:none}
.timeline{display:flex;flex-wrap:wrap;gap:4px 18px;color:var(--muted);font-size:.84rem;margin-bottom:6px}
.notes ul{padding-left:1.2em;margin:6px 0}.notes p{margin:6px 0}
details{margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--muted);font-size:.86rem}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:10px 0;font-size:.86rem}
dl.facts dt{color:var(--muted)}dl.facts dd{margin:0;min-width:0;overflow-wrap:anywhere}
.entry{border-left:2px solid var(--line);padding:2px 0 2px 16px;margin:18px 0}
.entry .when{color:var(--muted);font-size:.84rem;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.entry h3{margin:2px 0 4px}
.entry ul{padding-left:1.2em;margin:4px 0}
.feed{list-style:none;padding:0;margin:8px 0}
.feed li{display:grid;grid-template-columns:6.5em 1fr;gap:4px 12px;padding:9px 0;border-bottom:1px solid var(--line)}
.feed .d{color:var(--muted);font-size:.84rem;font-variant-numeric:tabular-nums}
.bars{list-style:none;padding:0;margin:10px 0 0;font-size:.84rem}
.bars li{display:grid;grid-template-columns:minmax(0,1fr) 2.5em;gap:2px 10px;margin:6px 0}
.bars .track{grid-column:1/-1;height:6px;background:#0b0d1a;border-radius:3px;overflow:hidden}
.bars .fill{height:100%;background:var(--etr);border-radius:3px}
.bars .n{text-align:right;font-variant-numeric:tabular-nums}
.callout{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:14px 0}
.callout.warn{border-color:#4d3b12}
.links{display:flex;flex-wrap:wrap;gap:8px 18px;margin:10px 0}
.links a{display:inline-flex;align-items:center;gap:6px}
ol.steps{padding-left:1.3em}ol.steps li{margin:6px 0}
footer{border-top:1px solid var(--line);margin-top:30px}
footer .in{max-width:980px;margin:0 auto;padding:18px 16px 28px;color:var(--muted);font-size:.84rem}
footer p{margin:4px 0}
@media (max-width:560px){.bar .rule,.bar .sub{display:none}nav li.ext,nav li.ext~li{display:none}.feed li{grid-template-columns:1fr}.rel{padding:2px 14px 12px}}
"""


def page(name, title, body, counts_js=False):
    nav = "".join(
        f'<li><a href="{"/" if k == "index" else "/" + k}"{" aria-current=page" if k == name else ""}>{esc(label)}</a></li>'
        for k, label in NAV)
    credits = ('Built on <a href="https://github.com/EastsideUrbanism/transit-tracker">Transit Tracker</a> '
               'by Eastside Urbanism and <a href="https://github.com/tjhorner">TJ Horner</a>, used with their permission. '
               'ETR’s changes were developed with AI assistance (Claude) and are tested on ETR hardware.')
    extra = [f'<a href="https://edmontontransitriders.org/">{icon("box-arrow-up-right")} edmontontransitriders.org</a>',
             f'<a href="https://edmontontransitriders.org/transittracker/">{icon("box-arrow-up-right")} About Transit Tracker</a>']
    if GITHUB_ORG:
        extra.append(f'<a href="https://github.com/{esc(GITHUB_ORG)}">{icon("github")} Source on GitHub</a>')
    if CONTACT_EMAIL:
        extra.append(f'<a href="mailto:{esc(CONTACT_EMAIL)}">{icon("envelope")} {esc(CONTACT_EMAIL)}</a>')
    extra.append(f'<a href="/feed.xml">{icon("rss")} Atom feed</a>')
    extra.append('<a href="/releases.json">releases.json</a>')
    canonical = f'<link rel="canonical" href="{DEV_SITE_URL}/{"" if name == "index" else name}">' if DEV_SITE_URL else ""
    nav += ('<li class="ext"><a href="https://edmontontransitriders.org/transittracker/">About Transit Tracker '
            f'{icon("box-arrow-up-right")}</a></li>'
            '<li><a href="https://edmontontransitriders.org/">Edmonton Transit Riders '
            f'{icon("box-arrow-up-right")}</a></li>')
    return f"""<!doctype html>
<html lang="en-CA">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="Release log, beta status and changelogs for Edmonton Transit Riders' Transit Tracker.">
<meta name="theme-color" content="#27368f">
{canonical}
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate" type="application/atom+xml" title="ETR Transit Tracker development" href="/feed.xml">
<style>{CSS}</style>
</head>
<body>
<header><div class="bar">
  <a href="/"><img src="/etr-logo.svg" alt="Edmonton Transit Riders" width="111" height="34"></a>
  <span class="rule"></span>
  <div><h1>Transit Tracker development</h1><div class="sub">Releases, beta builds and what changed</div></div>
</div></header>
<nav aria-label="Sections"><ul>{nav}</ul></nav>
<main>
{body}
</main>
<footer><div class="in">
  <p>{credits}</p>
  <div class="links small">{"".join(extra)}</div>
</div></footer>
{COUNTS_JS if counts_js else ""}
</body>
</html>
"""


# Fills the "connected now" card from /live/counts.json (written each minute by
# the fleet collector). Only totals per firmware version, never a device.
COUNTS_JS = """<script>
(function(){
  var el=document.getElementById('live'); if(!el) return;
  var ch=JSON.parse(document.getElementById('channels').textContent);
  function label(g){
    if(g.version&&g.version===ch.stable&&g.version===ch.beta) return g.label+' (stable + beta)';
    if(g.version&&g.version===ch.stable) return g.label+' (stable)';
    if(g.version&&g.version===ch.beta) return g.label+' (beta)';
    return g.label;
  }
  function ago(ms){var m=Math.round((Date.now()-ms)/60000);return m<1?'just now':m===1?'1 minute ago':m<120?m+' minutes ago':'over 2 hours ago';}
  fetch('/live/counts.json',{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){
    var n=document.getElementById('live-n'); n.textContent=d.total;
    var stale=Date.now()-d.updated_ms>15*60000;
    document.getElementById('live-when').textContent=(stale?'Not updating. Last count ':'Updated ')+ago(d.updated_ms);
    var ul=document.getElementById('live-bars'); ul.textContent='';
    var max=Math.max.apply(null,d.groups.map(function(g){return g.count;}).concat([1]));
    d.groups.forEach(function(g){
      var li=document.createElement('li');
      var a=document.createElement('span'); a.textContent=label(g);
      var b=document.createElement('span'); b.className='n'; b.textContent=g.count;
      var t=document.createElement('span'); t.className='track';
      var f=document.createElement('span'); f.className='fill'; f.style.width=(100*g.count/max)+'%'; f.style.display='block';
      t.appendChild(f); li.appendChild(a); li.appendChild(b); li.appendChild(t); ul.appendChild(li);
    });
  }).catch(function(){document.getElementById('live-when').textContent='Live count unavailable right now.';});
})();
</script>"""


def fmt_date(iso):
    return local_date(iso).strftime("%Y-%m-%d") if iso else None


def long_date(iso_or_date):
    if not iso_or_date:
        return ""
    d = local_date(iso_or_date) if "T" in iso_or_date else dt.date.fromisoformat(iso_or_date)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def short(sha):
    return sha[:7] if sha else "unknown"


def commit_link(repo, sha):
    if not sha:
        return "unknown"
    if GITHUB_ORG:
        return f'<a href="https://github.com/{esc(GITHUB_ORG)}/{repo}/commit/{esc(sha)}"><code>{short(sha)}</code></a>'
    return f"<code>{short(sha)}</code>"


def release_card(r):
    tl = []
    if r["to_beta"]:
        tl.append(f"Beta {fmt_date(r['to_beta'])}")
    if r["to_stable"]:
        tl.append(f"Stable {fmt_date(r['to_stable'])}")
    if r["superseded"]:
        tl.append(f"Superseded {fmt_date(r['superseded'])}")
    if not tl and r["built"]:
        tl.append(f"Built {fmt_date(r['built'])}")
    withdrawn = (f'<div class="callout warn">{icon("exclamation-triangle-fill")} <strong>Withdrawn.</strong> '
                 f'{inline(r["withdrawn"])}</div>') if r["withdrawn"] else ""
    files = "".join(
        f'<dt>{"Factory image" if f["kind"] == "factory" else "Update image"}</dt>'
        f'<dd><a href="{esc(f["url"])}">{esc(f["name"])}</a> <span class="muted">({f["bytes"] / 1048576:.2f} MB)</span>'
        f'<br><span class="small muted">SHA-256</span> <code>{f["sha256"]}</code></dd>'
        for f in r["files"])
    return f"""<article class="rel {r['status']}" id="{esc(r['version'])}">
<h3><a href="#{esc(r['version'])}">{esc(r['version'])}</a> <span class="pill {r['status']}">{STATUS_LABEL[r['status']]}</span></h3>
<div class="timeline">{"".join(f"<span>{icon('clock-history')} {esc(t)}</span>" for t in tl)}</div>
{withdrawn}
<div class="notes">{markdown(r['notes_md'], base_level=4)}</div>
<details><summary>Build details and downloads</summary>
<dl class="facts">
<dt>Based on upstream</dt><dd><a href="https://github.com/EastsideUrbanism/transit-tracker/releases/tag/{esc(r['upstream'])}">{esc(r['upstream'])}</a></dd>
<dt>ESPHome</dt><dd>{esc(r['esphome'] or 'unknown')}</dd>
<dt>Firmware source</dt><dd>{commit_link('transit-tracker', r['firmware_commit'])} <span class="muted">badge-layout</span></dd>
<dt>Display component</dt><dd>{commit_link('esphome-transit-tracker', r['component_commit'])} <span class="muted">badge-layout</span></dd>
{files}
</dl>
<p class="small muted">Most people never need these files: trackers update themselves, and the configurator’s
<a href="https://configurator.edmontontransitriders.org/setup">Initial setup</a> flashes a new one from the browser.
The factory image is for a first install over USB; the update image is what trackers download.</p>
</details>
</article>"""


def entry_html(e):
    return f"""<article class="entry" id="{e['component']}-{e['date']}">
<div class="when">{long_date(e['date'])}</div>
<h3>{inline(e['title'])}</h3>
{markdown(e['body_md'], base_level=4)}
</article>"""


def build(out):
    channels = {c: read(os.path.join(REPO, "channels", c)).strip() for c in ("stable", "beta")}
    channels = {k: v for k, v in channels.items() if v}
    events = load_history()
    releases = load_releases(events, channels)
    changes = {c: load_changelog(c) for c in ("configurator", "feeds")}
    by_v = {r["version"]: r for r in releases}

    # One combined "what changed" stream: firmware promotions + changelog entries.
    stream = []
    for e in events:
        r = by_v.get(e["to"])
        first_line = next((l.strip() for l in (r["notes_md"] if r else "").splitlines() if l.strip()), "")
        stream.append({"component": "firmware", "date": fmt_date(e["at"]), "at": e["at"],
                       "title": f"{e['to']} {'released to stable' if e['channel'] == 'stable' else 'released to beta'}",
                       "summary": first_line, "href": f"/firmware#{e['to']}"})
    for c, entries in changes.items():
        for e in entries:
            stream.append({"component": c, "date": e["date"], "at": e["date"] + "T12:00:00Z",
                           "title": e["title"], "summary": "", "href": f"/{c}#{c}-{e['date']}"})
    stream.sort(key=lambda s: s["at"], reverse=True)

    def since(channel):
        v = channels.get(channel)
        at = next((e["at"] for e in reversed(events) if e["channel"] == channel and e["to"] == v), None)
        return v, at

    stable_v, stable_at = since("stable")
    beta_v, beta_at = since("beta")
    beta_note = ("Same build as stable" if beta_v and beta_v == stable_v
                 else f"since {long_date(beta_at)}" if beta_v else "No beta right now; beta trackers follow stable")

    # ------------------------------------------------------------ overview --
    recent = "".join(
        f'<li><span class="d">{esc(s["date"])}</span><span><span class="pill c-{s["component"]}">{COMPONENT_NAMES[s["component"]]}</span> '
        f'<a href="{s["href"]}">{inline(s["title"])}</a>'
        f'{"<br><span class=small>" + inline(s["summary"]) + "</span>" if s["summary"] else ""}</span></li>'
        for s in stream[:10])
    overview = f"""
<p class="lede" style="margin-top:22px">Transit Tracker is an LED sign that shows live departure times for nearby
stops. <a href="https://edmontontransitriders.org/">Edmonton Transit Riders</a> builds and runs its own version
for signs in homes, shops and community spaces (<a href="https://edmontontransitriders.org/transittracker/">find out
more or get one</a>). This page tracks what’s being released, what’s in testing, and what changed.</p>

<script type="application/json" id="channels">{json.dumps(channels)}</script>
<div class="grid">
  <div class="card stable"><div class="k">{icon("check-circle-fill")} Stable firmware</div>
    <div class="v"><a href="/firmware#{esc(stable_v)}" style="color:inherit">{esc(stable_v or "none")}</a></div>
    <div class="small muted">{"since " + long_date(stable_at) if stable_at else ""}</div></div>
  <div class="card beta"><div class="k">{icon("bug")} Beta firmware</div>
    <div class="v"><a href="/firmware#{esc(beta_v)}" style="color:inherit">{esc(beta_v or "none")}</a></div>
    <div class="small muted">{esc(beta_note)}</div></div>
  <div class="card live" id="live"><div class="k">{icon("broadcast")} Trackers connected now</div>
    <div class="v" id="live-n">&ndash;</div>
    <div class="small muted" id="live-when">Loading&hellip;</div>
    <ul class="bars" id="live-bars"></ul></div>
</div>
<p class="small muted">The count covers every tracker connected to ETR’s data servers, including ones running
stock upstream firmware. No locations or device details are published.</p>

<h2>Recent changes</h2>
<ul class="feed">{recent}</ul>

<h2>What’s here</h2>
<div class="grid">
  <a class="card" href="/firmware" style="text-decoration:none;color:inherit"><div class="k">{icon("cpu")} Firmware</div>
    <p class="small" style="margin:6px 0 0">Every release, its status, release notes and checksums.</p></a>
  <a class="card" href="/configurator" style="text-decoration:none;color:inherit"><div class="k">{icon("window-sidebar")} Configurator</div>
    <p class="small" style="margin:6px 0 0">The web app for setting up a tracker.</p></a>
  <a class="card" href="/feeds" style="text-decoration:none;color:inherit"><div class="k">{icon("database")} Feeds</div>
    <p class="small" style="margin:6px 0 0">The data servers trackers get departures from, and which transit systems they cover.</p></a>
  <a class="card" href="/beta" style="text-decoration:none;color:inherit"><div class="k">{icon("bug")} Beta program</div>
    <p class="small" style="margin:6px 0 0">Run new builds early and help catch problems.</p></a>
</div>
"""

    # ------------------------------------------------------------ firmware --
    firmware = f"""
<h2>Firmware</h2>
<p class="lede">Trackers check for new firmware every few hours and install it on their own, unless
<strong>Auto-Update</strong> is turned off on the tracker’s web page. Each tracker follows one channel:
<strong>Stable</strong> (the default) or <strong>Beta</strong>. A build only reaches stable after running on beta.
Both channels serve exactly the same file; moving a build to stable doesn’t rebuild it.</p>
<div class="callout small"><strong>How versions are named.</strong> <code>v3.3.2-etr.4</code> means: based on upstream
Transit Tracker <code>v3.3.2</code>, ETR build 4. The ETR number only ever goes up, even when the upstream version changes
(<code>v3.3.2-etr.7</code> could be followed by <code>v3.4.0-etr.8</code>). Builds made outside the release process report
<code>dev</code> and never update themselves.</div>
<h3 id="how-a-build-gets-here">How a build gets here</h3>
<div class="grid steps3">
  <div class="card"><div class="k">{icon("cpu")} 1. Bench</div>
    <p class="small" style="margin:6px 0">On test units at ETR, installed by hand. Never offered to anyone. To leave the bench,
    a build must pass all of these on the hardware it is for:</p>
    <ul class="small" style="margin:0;padding-left:1.1em">
      <li>Shows departures with correct colours and panel order</li>
      <li>No clipped letters and no visible flicker</li>
      <li>Settings survive a push from the configurator and a power cycle</li>
      <li>Updating from the previous release works and keeps settings</li>
      <li>Runs 48 hours without restarting</li>
    </ul></div>
  <div class="card beta"><div class="k">{icon("bug")} 2. Beta</div>
    <p class="small" style="margin:6px 0">Installed automatically on trackers whose owners chose the
    <a href="/beta">Beta channel</a>. To move on, a build needs:</p>
    <ul class="small" style="margin:0;padding-left:1.1em">
      <li>At least 7 days on beta</li>
      <li>At least 2 to 3 trackers running it</li>
      <li>No problem reports and no unexplained disconnects</li>
    </ul></div>
  <div class="card stable"><div class="k">{icon("check-circle-fill")} 3. Stable</div>
    <p class="small" style="margin:6px 0">Every tracker, including signs in businesses that nobody watches day to day.
    If a problem turns up, the previous stable build goes straight back out; trackers reinstall it on their next check.</p></div>
</div>
<p class="small muted">Any exception to these steps is stated in that release’s notes. For a new hardware model with no
trackers in the field yet, ETR’s own test units stand in for beta testers.</p>
<div class="links">
  <a href="{esc(SITE_URL)}/firmware/manifest.json">{icon("box-arrow-up-right")} Stable manifest</a>
  <a href="{esc(SITE_URL)}/firmware/beta/manifest.json">{icon("box-arrow-up-right")} Beta manifest</a>
</div>
{"".join(release_card(r) for r in releases)}
<h3>What ETR’s firmware adds to upstream</h3>
<ul>
<li><strong>Badge layout</strong> (optional): route numbers in coloured badges, with ETR’s route colours.</li>
<li><strong>Real-time indicators</strong>: wave, bar or dot, chosen in the configurator.</li>
<li><strong>ETR update channel</strong>: stable and beta, served by ETR, with the choice on the tracker itself.</li>
<li>ETR branding on the status screens.</li>
</ul>
"""

    # ------------------------------------------------------- configurator --
    def changelog_page(c, intro):
        entries = "".join(entry_html(e) for e in changes[c]) or '<p class="muted">Nothing yet.</p>'
        return f"<h2>{COMPONENT_NAMES[c]}</h2>\n{intro}\n{entries}"

    configurator = changelog_page("configurator", f"""<p class="lede">The web app for setting up a tracker: flashing,
Wi-Fi, routes and display settings, at <a href="https://configurator.edmontontransitriders.org">configurator.edmontontransitriders.org</a>.
Changes go live as soon as they’re published; there’s no beta channel for the configurator.</p>""")

    feeds = changelog_page("feeds", f"""<p class="lede">Every tracker connects to a data server for its departure times.
ETR runs its own, using TJ Horner’s <a href="https://github.com/tjhorner/transit-tracker-api">transit-tracker-api</a>.</p>
<div class="callout small"><strong>Transit systems covered:</strong> Edmonton (ETS, which includes St. Albert, Strathcona County,
Spruce Grove, Fort Saskatchewan, Beaumont and Leduc), Calgary, Red Deer, Lethbridge, Medicine Hat, Grande Prairie,
Roam (Bow Valley) and Wood Buffalo.</div>""")

    # ---------------------------------------------------------------- beta --
    report = (f'Email <a href="mailto:{esc(CONTACT_EMAIL)}">{esc(CONTACT_EMAIL)}</a> with the version '
              f'(shown on the tracker’s web page), what you saw, and when.' if CONTACT_EMAIL
              else 'Tell the ETR volunteer who set up your tracker, with the version (shown on the tracker’s web page), what you saw, and when.')
    beta = f"""
<h2>Beta program</h2>
<p class="lede">Beta trackers get new firmware first, usually days before stable. If something is wrong with a build,
we want to find out on a handful of beta trackers, not on every sign in the city.</p>
<div class="grid">
  <div class="card beta"><div class="k">{icon("bug")} Current beta</div>
    <div class="v"><a href="/firmware#{esc(beta_v)}" style="color:inherit">{esc(beta_v or "none")}</a></div>
    <div class="small muted">{esc(beta_note)}</div></div>
  <div class="card stable"><div class="k">{icon("check-circle-fill")} Current stable</div>
    <div class="v"><a href="/firmware#{esc(stable_v)}" style="color:inherit">{esc(stable_v or "none")}</a></div></div>
</div>

<h3>Who should join</h3>
<p>Trackers in a home or anywhere someone will notice a problem and tell us. Signs in businesses and public spaces
that nobody watches day to day should stay on stable.</p>

<h3>How to join</h3>
<ol class="steps">
<li>Open the tracker’s own web page: type its address (for example <code>http://192.168.1.50</code>) into a browser
on the same Wi-Fi. Your router’s device list shows the address.</li>
<li>Set <strong>Update Channel</strong> to <strong>Beta</strong>.</li>
<li>Leave <strong>Auto-Update</strong> on. The tracker checks every few hours and installs the beta when it next checks.</li>
</ol>
<p>Your routes and display settings are kept through every update.</p>

<h3>How to leave</h3>
<p>Set <strong>Update Channel</strong> back to <strong>Stable</strong>. At its next check, the tracker installs the
current stable build, even if that is older than the beta it was running. That’s intended: it’s how a tracker
gets back to a known-good build.</p>

<h3>Reporting a problem</h3>
<p>{report}</p>

<h3>The first install needs a cable</h3>
<p class="small muted">Trackers running ETR builds from before the update channel (etr.1 and etr.2, which report
<code>dev</code>) can’t update themselves. They join the channel once they’re flashed with a current build
over USB, from <a href="https://configurator.edmontontransitriders.org/setup">Initial setup</a>.</p>
"""

    notfound = """<h2>Page not found</h2><p>Try the <a href="/">overview</a>.</p>"""

    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    pages = {
        "index": ("Transit Tracker development · ETR", overview, True),
        "firmware": ("Firmware releases · ETR Transit Tracker", firmware, False),
        "configurator": ("Configurator changelog · ETR Transit Tracker", configurator, False),
        "feeds": ("Feeds changelog · ETR Transit Tracker", feeds, False),
        "beta": ("Beta program · ETR Transit Tracker", beta, False),
        "404": ("Not found · ETR Transit Tracker", notfound, False),
    }
    for name, (title, body, js) in pages.items():
        with open(os.path.join(out, f"{name}.html"), "w") as f:
            f.write(page(name, title, body, counts_js=js))
    for asset in ("etr-logo.svg", "favicon.svg"):
        shutil.copy(os.path.join(HERE, asset), out)

    # ------------------------------------------------ machine-readable feeds --
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pub = {
        "generated": generated,
        "channels": {c: {"version": v, "manifest": f"{SITE_URL}/firmware/{'beta/' if c == 'beta' else ''}manifest.json"}
                     for c, v in channels.items()},
        "firmware": [{k: r[k] for k in ("version", "upstream", "etr", "status", "withdrawn", "to_beta", "to_stable",
                                         "superseded", "esphome", "firmware_commit", "component_commit", "files")}
                     | {"notes": r["notes_md"]} for r in releases],
        "changelog": {c: [{k: e[k] for k in ("date", "title")} | {"body": e["body_md"]} for e in entries]
                      for c, entries in changes.items()},
    }
    with open(os.path.join(out, "releases.json"), "w") as f:
        json.dump(pub, f, indent=2)

    base = DEV_SITE_URL or ""
    host = re.sub(r"^https?://", "", base) or "ttdev"
    items = []
    for s in stream[:40]:
        if s["component"] == "firmware":
            r = by_v.get(s["title"].split(" ")[0])
            content = markdown(r["notes_md"]) if r else ""
        else:
            e = next(e for e in changes[s["component"]] if e["date"] == s["date"] and e["title"] == s["title"])
            content = markdown(e["body_md"])
        items.append(f"""<entry>
<id>tag:{esc(host)},2026:{esc(s['href'])}:{esc(s['at'])}</id>
<title>{esc(COMPONENT_NAMES[s['component']] + ': ' + s['title'])}</title>
<updated>{esc(s['at'])}</updated>
<link href="{esc(base + s['href'])}"/>
<content type="html">{esc(content)}</content>
</entry>""")
    with open(os.path.join(out, "feed.xml"), "w") as f:
        f.write(f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>ETR Transit Tracker development</title>
<id>tag:{esc(host)},2026:feed</id>
<updated>{stream[0]['at'] if stream else generated}</updated>
<author><name>Edmonton Transit Riders</name></author>
<link rel="self" href="{esc(base)}/feed.xml"/>
<link href="{esc(base)}/"/>
{"".join(items)}
</feed>
""")
    print(f"  devsite built: {len(releases)} firmware releases, "
          f"{sum(len(v) for v in changes.values())} changelog entries -> {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
