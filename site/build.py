#!/usr/bin/env python3
"""Build the course site from the lecturer's slides folder.

The slides folder is READ-ONLY: this script only reads from it and copies files into this repository.

One run builds two versions from the same code:
  - the public version (repo root): only items listed in site/site.json -> "published";
  - the full version (repo/<staff_dir>/, "גרסת מתרגלים"): every week and everything students will eventually get.
    Its folder name is the TA password. The lecturer also uses it as his own local view.
Lecturer-only material never reaches either version ("דף למרצה" etc.: extra links are private unless listed in "more_public").
Files are copied only when they changed; files no longer needed are removed.

Usage:
  python3 site/build.py --slides <path to slides folder>
  python3 site/build.py --slides <path> --publish-week 3        # add all of week 3's items to "published", then build
  python3 site/build.py --slides <path> --today 2026-11-20      # build as if today were that date
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote, unquote

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / 'site'
CONFIG = SITE / 'site.json'

# Unit colors: main (text, icons, rule) · line (bars) · tint (small surfaces). Source of truth: the course map, "הגדרות צבעים".
UNIT_COLORS = {
    '0': ('#6a737d', '#dfe2e6', '#f3f4f5'),
    'א': ('#185fa5', '#9fbbd9', '#eaf1f9'),
    'ב': ('#2e7440', '#a9cdb3', '#ebf5ee'),
    'ג': ('#94670f', '#dcc596', '#f8f1e2'),
    'ד': ('#6a47a0', '#c3b2de', '#f2edf9'),
    'ה': ('#9b3d55', '#e0b3bd', '#f9ecef'),
}
UNIT_SHORT = {'א': 'מהמודל המייצר אל הפרמטר', 'ב': 'פאקטוריאלי', 'ג': 'הסקה', 'ד': 'רציפים', 'ה': 'הרחבות'}
DAYS = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']  # date.weekday(): Monday = 0
MONTHS = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
TITLE_PREFIXES = ['התנסות בכיתה — ', 'התנסות — ', 'תרגול: ', 'היסטוריה: ']

ICON_DECK = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2.5" y="3.5" width="15" height="10" rx="1.5"/><path d="M10 13.5v3M7 16.5h6"/></svg>'
ICON_DOC = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M5 2.5h7l3.5 3.5v11.5H5z"/><path d="M12 2.5V6h3.5M7.5 10h5M7.5 13h5"/></svg>'
ICON_ACT = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="2.5"/></svg>'
ICON_VIDEO = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2.5" y="4" width="15" height="12" rx="2"/><path d="M8.5 7.5v5l4-2.5z"/></svg>'
ICON_BOOK = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 5.5C8.5 4.2 6.3 3.5 3 3.5v11c3.3 0 5.5.7 7 2 1.5-1.3 3.7-2 7-2v-11c-3.3 0-5.5.7-7 2z"/><path d="M10 5.5v11"/></svg>'
ICON_HW = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M5 2.5h7l3.5 3.5v11.5H5z"/><path d="M12 2.5V6h3.5M7.5 10h5M7.5 13h3"/></svg>'
CHEV_BACK = '<svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M8 5l5 5-5 5"/></svg>'
CHEV_FWD = '<svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 5l-5 5 5 5"/></svg>'


def esc(s):
    return html.escape(str(s or ''), quote=True)


def d(s):
    return dt.date.fromisoformat(s) if s else None


def dm(s):
    x = d(s)
    return f'{x.day}.{x.month}' if x else ''


def day_dm(s):
    x = d(s)
    return f'{DAYS[x.weekday()]} {x.day}.{x.month}' if x else ''


def span(a, b):
    a, b = d(a), d(b)
    if not a:
        return ''
    if not b:
        return f'{a.day} ב{MONTHS[a.month - 1]}'
    if a.month == b.month:
        return f'{a.day}–{b.day} ב{MONTHS[a.month - 1]}'
    return f'{a.day}.{a.month}–{b.day}.{b.month}'


def href(path):
    return quote(path.replace(os.sep, '/'))


class Files:
    """Copies what the pages need into the repo, only when changed, and removes what is no longer needed."""
    def __init__(self, repo):
        self.repo = repo
        self.need = set()
        self.changed = 0
        self.libs = {}

    def _mark(self, rel):
        if rel in self.need:
            return None
        self.need.add(rel)
        return self.repo / rel

    def copy(self, src, rel):
        dest = self._mark(rel)
        if dest is None:
            return
        if dest.exists():
            a, b = src.stat(), dest.stat()
            if a.st_size == b.st_size and a.st_mtime <= b.st_mtime + 2:
                return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self.changed += 1

    def tree(self, src_dir, rel_dir):
        for p in src_dir.rglob('*'):
            if p.is_file():
                self.copy(p, f'{rel_dir}/{p.relative_to(src_dir).as_posix()}')

    def write(self, text, rel):
        dest = self._mark(rel)
        if dest is None:
            return
        if dest.exists() and dest.read_text(encoding='utf-8') == text:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
        self.changed += 1

    def prune(self, roots, root_globs):
        removed = 0
        for r in roots:
            base = self.repo / r
            if not base.exists():
                continue
            for p in sorted(base.rglob('*'), key=lambda x: len(x.parts), reverse=True):
                rel = p.relative_to(self.repo).as_posix()
                if p.is_file() and rel not in self.need:
                    p.unlink()
                    removed += 1
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
        for g in root_globs:
            for p in self.repo.glob(g):
                if p.is_file() and p.name not in self.need:
                    p.unlink()
                    removed += 1
        return removed


class Site:
    def __init__(self, slides, cfg, data, files, published, folder='', staff=False, today=None):
        self.today = today or dt.date.today()
        self.slides = slides
        self.cfg = cfg
        self.data = data
        self.files = files
        self.folder = folder                      # '' = repo root (public) or the staff folder
        self.p = '../' if folder else ''          # prefix from a page to the shared assets/, decks/, files/
        self.staff = staff
        self.decks_dir = slides / 'כלים' / 'צפייה'
        self.weeks = [w for w in self.data['weeks'] if w.get('lecture')]
        self.units = {u['id']: u for u in self.data['units']}
        self.published = published
        self.warnings = []

    # ---------- files ----------
    def copy_file(self, src_rel, dest_dir):
        """Copy a file from the slides folder (relative path) into the repo at dest_dir. Returns the href from a page."""
        src = self.slides / src_rel
        if not src.is_file():
            self.warnings.append(f'missing file: {src_rel}')
            return None
        self.files.copy(src, f'{dest_dir}/{src.name}')
        return self.p + href(f'{dest_dir}/{src.name}')

    def deck_for(self, code):
        p = self.decks_dir / (code.replace('.', '_') + '.html')
        return p if p.is_file() else None

    def copy_deck(self, code):
        deck = self.deck_for(code)
        text = deck.read_text(encoding='utf-8')
        if f'decks/{deck.name}' not in self.files.need:
            out = text
            fdir = deck.with_name(deck.stem + '_files')
            if fdir.is_dir():
                libs = fdir / 'libs'
                for c in fdir.iterdir():  # everything except libs (walking libs on a synced drive is slow)
                    if c.name == 'libs':
                        continue
                    if c.is_dir():
                        self.files.tree(c, f'decks/{fdir.name}/{c.name}')
                    else:
                        self.files.copy(c, f'decks/{fdir.name}/{c.name}')
                if libs.is_dir():
                    # Quarto gives every deck its own identical copy of reveal.js etc. (~6MB): keep one shared copy per version.
                    # Signature = the lib files the deck loads, with their sizes (cheap, and changes when Quarto's libs change).
                    used = sorted(set(re.findall(re.escape(fdir.name) + r'/libs/([^"\'?#\s)]+)', text)))
                    sizes = [(libs / unquote(u)).stat().st_size if (libs / unquote(u)).is_file() else None for u in used]
                    sig = hashlib.sha1('\n'.join(f'{u}:{z}' for u, z in zip(used, sizes)).encode()).hexdigest()[:10]
                    if None in sizes and self.files.libs:
                        # the deck's own libs are incomplete (a render that did not finish): borrow a complete copy
                        self.warnings.append(f'{deck.name}: its _files/libs folder is incomplete in the slides folder; using the shared copy')
                        sig = next(iter(self.files.libs))
                    elif sig not in self.files.libs:
                        self.files.libs[sig] = True
                        self.files.tree(libs, f'decks/_libs/{sig}')
                    for name in {fdir.name, quote(fdir.name)}:
                        out = out.replace(f'{name}/libs/', f'_libs/{sig}/')
            self.files.write(out, f'decks/{deck.name}')
            # Shared resources the deck refers to by name (images, the design folder).
            refs = {unquote(v) for v in re.findall(r'(?:src|href|data-background-image|data-src)="([^"#?]+)', text)}
            refs |= {unquote(v) for v in re.findall(r'url\(["\']?([^"\')]+)', text)}
            for p in self.decks_dir.iterdir():
                if p.suffix == '.html' or p.name.endswith('_files'):
                    continue
                if p.name in refs or any(r.startswith(p.name + '/') for r in refs):
                    if p.is_dir():
                        self.files.tree(p, f'decks/{p.name}')
                    else:
                        self.files.copy(p, f'decks/{p.name}')
        def grab(pat):
            m = re.search(pat, text, re.S)
            t = html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip() if m else ''
            return re.sub(r'^[A-Z]\d+\.[A-Z]+\d+\s*[—:\-–]\s*', '', t)  # never show resource codes
        link = self.p + href(f'decks/{deck.name}')
        return link, grab(r'<h1 class="title"[^>]*>(.*?)</h1>'), grab(r'<p class="subtitle"[^>]*>(.*?)</p>')

    # ---------- items ----------
    def kind(self, it):
        m = re.match(r'W\d+\.([A-Z])\d+$', it.get('code', ''))
        return m.group(1) if m else ''

    def clean_title(self, it):
        t = self.cfg.get('titles', {}).get(it['code'], it.get('title', ''))
        for p in TITLE_PREFIXES:
            if t.startswith(p):
                t = t[len(p):]
        return t

    def split_title(self, t):
        """'Main: rest' / 'Question? rest' -> (main, rest), only when the main part has 2+ words."""
        m = re.match(r'(.+?)(:|\?)\s+(.+)$', t)
        if m and len(m.group(1).split()) >= 2:
            return m.group(1) + ('?' if m.group(2) == '?' else ''), m.group(3)
        return t, ''

    def resolve(self, it):
        """Main link, subtitle, icon and extra links for an item."""
        link, sub, icon = None, '', ICON_ACT
        title, rest = self.split_title(self.clean_title(it))
        if self.deck_for(it['code']):
            link, dtitle, sub = self.copy_deck(it['code'])
            icon = ICON_DECK
            if it['code'] not in self.cfg.get('titles', {}):
                title = dtitle or title
        if not sub:
            sub = rest
        elif it.get('link'):
            folder = 'files/' + it['code'].replace('.', '_')
            link = self.copy_file(it['link'], folder)
            icon = ICON_VIDEO if it['link'].lower().endswith('.mp4') else ICON_DOC
        k = self.kind(it)
        if not sub:
            sub = {'K': 'דף עבודה לכיתה' if link else '', 'H': 'דף היסטוריה', 'B': 'תרגיל להגשה'}.get(k, '')
        sub = self.cfg.get('subtitles', {}).get(it['code'], sub)
        more = []
        public_more = set(self.cfg.get('more_public', []))
        for m in it.get('more', []):
            if m.get('href') and m.get('label') in public_more:  # extra links are private unless their label is listed
                folder = 'files/' + it['code'].replace('.', '_')
                h = self.copy_file(m['href'], folder)
                if h:
                    more.append((m['label'], h))
        return link, title, sub, icon, more

    def mat_row(self, it):
        link, title, sub, icon, more = self.resolve(it)
        title = esc(title)
        thumb = self.cfg.get('thumbs', {}).get(it['code'])
        ic = (f'<span class="ic thumb"><img src="{self.p}assets/img/{href(thumb)}" alt=""></span>' if thumb
              else f'<span class="ic">{icon}</span>')
        extra = ''.join(f' · <a class="ln" href="{h}" target="_blank" rel="noopener">{esc(lbl)}</a>' for lbl, h in more)
        subhtml = f'<span class="ms">{esc(sub)}{extra}</span>' if (sub or extra) else ''
        if extra.startswith(' · ') and not sub:
            subhtml = f'<span class="ms">{extra[3:]}</span>'
        target = '' if link and 'decks/' in link else ' target="_blank" rel="noopener"'
        if link and not more:
            return f'<a class="mat" href="{link}"{target}>{ic}<span><span class="mt">{title}</span>{subhtml}</span></a>'
        t = f'<a class="mt" href="{link}"{target}>{title}</a>' if link else f'<span class="mt">{title}</span>'
        return f'<div class="mat">{ic}<span>{t}{subhtml}</span></div>'

    # ---------- weeks ----------
    def open_weeks(self):
        return [w for w in self.weeks if any(it['code'] in self.published for it in w.get('items', []))]

    def split(self, w):
        lesson, practice, reading, hw, notes = [], [], [], [], []
        for it in w.get('items', []):
            if it['code'] not in self.published or it['code'] == 'TO.DO':
                continue
            k = self.kind(it)
            if k == 'Q':
                notes.append(it)
            elif k in ('H', 'R'):
                reading.append(it)
            elif k == 'B':
                hw.append(it)
            elif k == 'T' or it.get('phase') == 'practice' or (it.get('release') and w.get('practice') and it['release'] >= w['practice'] and it.get('phase') != 'class'):
                practice.append(it)
            else:
                lesson.append(it)
        return lesson, practice, reading, hw, notes

    def next_lecture(self, w):
        later = [x for x in self.weeks if x['n'] > w['n']]
        return later[0]['lecture'] if later else None

    # ---------- page parts ----------
    def header(self):
        gen = ''.join(f'<a href="{h}"{t}>{esc(lbl)}</a>' for lbl, h, t in self.general_links())
        mob = ''.join(f'<a href="{h}"{t}>{esc(lbl)}</a>' for lbl, h, t in self.general_links())
        menu = (f'<details class="mmenu"><summary aria-label="צוות, סילבוס ומידע על הקורס">'
                f'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="#1c2a31" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M3.5 6h13M3.5 10h13M3.5 14h13"/></svg>'
                f'</summary><nav aria-label="מידע כללי">{mob}</nav></details>') if mob else ''
        c = self.data['course']
        badge = ' · <b class="badge">גרסת מתרגלים</b>' if self.staff else ''
        return (f'<header class="hdr"><a class="home" href="index.html" aria-label="לדף הבית"><img class="logo" src="{self.p}assets/img/tau-logo.png" alt="אוניברסיטת תל אביב"></a>'
                f'<span class="sep"></span><a class="ttl home" href="index.html"><span class="fr ttl1">{esc(c["title"])}</span>'
                f'<span class="ttl2">{esc(c["year"])} · {esc(c["lecturer"])}{badge}</span></a>'
                f'<nav class="gen" aria-label="מידע כללי">{gen}</nav>{menu}</header>')

    def general_links(self):
        out = []
        labels = self.cfg.get('general_labels', {})
        for g in self.data.get('general', []):
            code = g.get('code')
            if not code or code not in self.published or not g.get('link'):
                continue
            h = self.copy_file(g['link'], 'files/' + code)
            if code == 'G0':  # the staff page is an html page with its own images next to it
                src_dir = (self.slides / g['link']).parent
                for p in src_dir.iterdir():
                    if p.is_file() and p.name != Path(g['link']).name:
                        self.files.copy(p, f'files/{code}/{p.name}')
            if h:
                out.append((labels.get(code, g['title']), h, '' if h.endswith('.html') else ' target="_blank" rel="noopener"'))
        return out

    def menu(self, sel_n, cur_n, open_ns):
        # Desktop: all units in one row. Phone: one unit at a time, big arrows to page between units,
        # swipe also pages (RTL: finger to the right = forward). The exam is the last page.
        units = []
        focus = sel_n or cur_n
        ulist = [u for u in self.data['units'] if u['id'] in UNIT_COLORS and u['id'] != '0'
                 and any(w['unit'] == u['id'] for w in self.weeks)]
        for i, u in enumerate(ulist):
            uw = [w for w in self.weeks if w['unit'] == u['id']]
            line, ink = UNIT_COLORS[u['id']][1], UNIT_COLORS[u['id']][0]
            on = any(w['n'] == focus for w in uw) or (focus is None and i == 0)
            wks = []
            for w in uw:
                n = w['n']
                medal = self.cfg.get('weeks', {}).get(str(n), {}).get('medal')
                face = f'<img src="{self.p}assets/img/{href(medal)}" alt="">' if medal else str(n)
                cls = ['wk']
                if n in open_ns:
                    cls.append('cur' if n == cur_n else 'past')
                    if n == sel_n:
                        cls.append('sel')
                    label = 'השבוע' if n == cur_n else dm(w['lecture'])
                    aria = f'שבוע {n}' + (', השבוע' if n == cur_n else '')
                    cur = ' aria-current="page"' if n == sel_n else ''
                    wks.append(f'<a class="{" ".join(cls)}" href="week-{n}.html" style="--u: {line};" aria-label="{aria}"{cur}><span class="c">{face}</span><span class="wl">{label}</span></a>')
                else:
                    wks.append(f'<span class="wk" style="--u: {line};"><span class="c f">{n}</span><span class="wl">{dm(w["lecture"])}</span></span>')
            name = esc(u['name'].replace('יחידה ', ''))
            units.append(f'<div class="unit{" on" if on else ""}" data-i="{i}" style="--u: {line}; --ui: {ink};">'
                         f'<span class="ul"><span class="un">יחידה </span>{name} · <span class="ush">{esc(UNIT_SHORT.get(u["id"], u["title"]))}</span><span class="ufull">{esc(u["title"])}</span></span>'
                         f'<div class="wks">{"".join(wks)}</div></div>')
        exam = self.data['course'].get('exam')
        if exam:
            x = d(exam)
            units.append(f'<div class="unit xu" data-i="{len(units)}" style="--u: #001a24; --ui: #001a24;"><span class="ul">בחינה</span><div class="wks"><span class="wk exam" style="--u: transparent;"><span class="c">{dm(exam)}</span><span class="wl">{x.year}</span></span></div></div>')
        prev = f'<button type="button" class="mpg mprev" aria-label="היחידה הקודמת">{CHEV_BACK}</button>'
        nxt = f'<button type="button" class="mpg mnext" aria-label="היחידה הבאה">{CHEV_FWD}</button>'
        return f'<nav class="menu" aria-label="שבועות הסמסטר"><div class="mpager">{prev}<div class="mrow">{"".join(units)}</div>{nxt}</div></nav>'

    def week_article(self, w, open_ns):
        n = w['n']
        extra = self.cfg.get('weeks', {}).get(str(n), {})
        parts = [p.strip() for p in w['topic'].split('·')]
        h1, sub = parts[0], ' · '.join(parts[1:])
        u = self.units[w['unit']]
        img = ''
        if extra.get('image'):
            cap = f'<figcaption>{esc(extra.get("caption", ""))}</figcaption>' if extra.get('caption') else ''
            img = f'<figure class="eng"><img src="{self.p}assets/img/{href(extra["image"])}" alt="{esc(extra.get("caption", ""))}">{cap}</figure>'
        hero = (f'<header class="hero{"" if img else " noimg"}"><div><p class="eyebrow">שבוע {n} · {span(w["lecture"], w.get("practice"))}</p>'
                f'<h1 class="h1 fr">{esc(h1)}</h1>' + (f'<p class="fr sub">{esc(sub)}</p>' if sub else '') +
                f'<p class="unitline">{esc(u["name"])} · {esc(u["title"])}</p></div>{img}</header>')
        lesson, practice, reading, hw, notes = self.split(w)
        def note_text(it):
            t = it['title']
            m = re.match(r'שאלת חובה ב-Moodle\s*—\s*(.+)', t)
            if m:
                t = f'פותחים בשאלת חובה ב-Moodle על {m.group(1)}'
            mins = re.match(r'(\d+)\s*דק', it.get('load') or '')
            return t + (f', {mins.group(1)} דקות' if mins else '') + '.'
        notes_html = ''.join(f'<p class="note">{esc(note_text(it))}</p>' for it in notes)
        cols = ''
        if lesson or notes:
            cols += (f'<section class="mcard" aria-label="שיעור"><div class="mh"><h2 class="fr">שיעור</h2><span>{day_dm(w["lecture"])}</span></div>'
                     f'{notes_html}{"".join(self.mat_row(it) for it in lesson)}</section>')
        if practice:
            cols += (f'<section class="mcard" aria-label="תרגול"><div class="mh"><h2 class="fr">תרגול</h2><span>{day_dm(w.get("practice"))}</span></div>'
                     f'{"".join(self.mat_row(it) for it in practice)}</section>')
        cols = f'<div class="cols">{cols}</div>' if cols else ''
        nxt = ''
        cards = []
        medal = extra.get('medal')
        nl = self.next_lecture(w)
        for it in reading:
            link, rtitle, sub, icon, more = self.resolve(it)
            pic = f'<img class="npimg" src="{self.p}assets/img/{href(medal)}" alt="">' if (medal and self.kind(it) == 'H') else f'<span class="ic npic">{ICON_DOC}</span>'
            when = f'<span class="nd">עד {day_dm(nl).replace(" ", ", ", 1)}</span>' if nl else ''
            note = 'השיעור הבא נפתח בשאלה עליו' if self.kind(it) == 'H' else sub
            cards.append(self.np_card(link, pic, ICON_BOOK + 'קריאה', rtitle, note, when))
        for it in hw:
            link, _t, sub, icon, more = self.resolve(it)
            title = self.clean_title(it)
            m = re.match(r'(תרגיל להגשה מספר \d+)\s*—\s*(.+)', title)
            t1, s1 = (m.group(1), m.group(2)) if m else (title, '')
            when = f'<span class="nd">עד {day_dm(it.get("due")).replace(" ", ", ", 1)}</span>' if it.get('due') else ''
            cards.append(self.np_card(link, f'<span class="ic npic">{ICON_DOC}</span>', ICON_HW + 'הגשה', t1, s1, when))
        if cards:
            nxt = (f'<section class="next" aria-label="לשבוע הבא"><h2 class="fr nexth">לשבוע הבא</h2>'
                   f'<div class="ng" style="grid-template-columns: repeat({min(len(cards), 3)}, minmax(0, 1fr));">{"".join(cards)}</div></section>')
        prev = [x for x in self.weeks if x['n'] < n and x['n'] in open_ns]
        later = [x for x in self.weeks if x['n'] > n]
        nav = ''
        if prev:
            p = prev[-1]
            nav += f'<a href="week-{p["n"]}.html">{CHEV_BACK}שבוע {p["n"]} · {esc(p["topic"].split("·")[0].strip())}</a>'
        else:
            nav += '<i></i>'
        if later:
            q = later[0]
            if q['n'] in open_ns:
                nav += f'<a href="week-{q["n"]}.html">שבוע {q["n"]} · {esc(q["topic"].split("·")[0].strip())}{CHEV_FWD}</a>'
            else:
                nav += f'<span>שבוע {q["n"]} ייפתח ב-{dm(q["lecture"])}{CHEV_FWD}</span>'
        return f'<article class="week">{hero}{cols}{nxt}<nav class="nav" aria-label="מעבר בין שבועות">{nav}</nav></article>'

    def np_card(self, link, pic, label, title, sub, when):
        tag, attr = ('a', f' href="{link}" target="_blank" rel="noopener"') if link else ('div', '')
        sub_html = f'<span class="ms">{esc(sub)}</span>' if sub else ''
        return (f'<{tag} class="np"{attr}>{pic}<span class="npt"><span class="nk">{label}</span>'
                f'<span class="mt">{esc(title)}</span>{sub_html}{when}</span></{tag}>')

    def welcome(self):
        c = self.data['course']
        return (f'<article class="week"><header class="hero noimg"><div><p class="eyebrow">{esc(c["year"])}</p>'
                f'<h1 class="h1 fr">{esc(c["title"])}</h1>'
                f'<p class="fr sub">השיעור הראשון: {day_dm(c["firstLecture"])}, {esc(c["lecture"].split(",")[-1].strip())}</p>'
                f'<p class="unitline">חומרי הקורס יופיעו כאן שבוע אחרי שבוע.</p></div></header></article>')

    def page(self, title, unit, body):
        ink, line, tint = UNIT_COLORS.get(unit, UNIT_COLORS['0'])
        c = self.data['course']
        robots = '<meta name="robots" content="noindex, nofollow">\n' if self.staff else ''
        if self.staff:
            gate = '<a class="ln" href="../index.html">לגרסה של הסטודנטים</a>'
        else:
            gate = ('<details class="tlog"><summary>כניסת מתרגלים</summary><form class="tlogf">'
                    '<input type="password" autocomplete="off" aria-label="סיסמה" placeholder="סיסמה">'
                    '<button type="submit">כניסה</button><span class="terr" hidden>סיסמה שגויה</span></form></details>')
        lic = 'חומרי הקורס ברישיון <a class="ln" href="https://creativecommons.org/licenses/by-nc-sa/4.0/" rel="license">CC BY-NC-SA 4.0</a>'
        return f'''<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@500;700;900&amp;family=Assistant:wght@400;500;600;700&amp;display=swap">
{robots}<link rel="stylesheet" href="{self.p}assets/site.css">
<style>:root{{--u-ink:{ink};--u-line:{line};--u-tint:{tint}}}</style>
</head>
<body>
<div class="wrap">
{self.header()}
<div class="sheet card">
{body}
</div>
<footer class="foot"><img src="{self.p}assets/img/tau-logo.png" alt="" class="flogo"><span>{esc(c["title"])} · {esc(c["year"])}</span><span>{lic}</span>{gate}</footer>
</div>
<script>
document.querySelectorAll('.menu').forEach(function(m){{
  var us=[].slice.call(m.querySelectorAll('.unit[data-i]'));
  var pv=m.querySelector('.mprev'), nx=m.querySelector('.mnext'), pg=m.querySelector('.mpager');
  var i=Math.max(0,us.findIndex(function(u){{return u.classList.contains('on')}}));
  function show(j,dir){{ if(j<0||j>=us.length) return; i=j;
    us.forEach(function(u,k){{u.classList.toggle('on',k===i); u.classList.remove('in-f','in-b')}});
    if(dir){{ void us[i].offsetWidth; us[i].classList.add(dir>0?'in-f':'in-b'); }}
    pv.disabled=i===0; nx.disabled=i===us.length-1; }}
  pv.onclick=function(){{show(i-1,-1)}}; nx.onclick=function(){{show(i+1,1)}};
  var x0=null,y0=0;
  pg.addEventListener('touchstart',function(e){{x0=e.touches[0].clientX;y0=e.touches[0].clientY}},{{passive:true}});
  pg.addEventListener('touchend',function(e){{ if(x0===null) return;
    var dx=e.changedTouches[0].clientX-x0, dy=e.changedTouches[0].clientY-y0; x0=null;
    if(Math.abs(dx)>40&&Math.abs(dx)>1.5*Math.abs(dy)){{ dx>0?show(i+1,1):show(i-1,-1); }} }},{{passive:true}});
  show(i);
}});
// TA sign-in: the password is the name of the full version's folder.
document.querySelectorAll('.tlogf').forEach(function(f){{
  f.addEventListener('submit',function(e){{ e.preventDefault();
    var v=f.querySelector('input').value.trim().toLowerCase().replace(/[^a-z0-9_-]/g,''), er=f.querySelector('.terr');
    if(!v) return;
    if(location.protocol==='file:'){{ location.href=v+'/index.html'; return; }}
    fetch(v+'/index.html',{{method:'HEAD',cache:'no-store'}}).then(function(r){{ if(r.ok) location.href=v+'/'; else er.hidden=false; }}).catch(function(){{er.hidden=false}});
  }});
}});
</script>
</body>
</html>
'''

    # ---------- build ----------
    def build(self):
        pre = f'{self.folder}/' if self.folder else ''
        opened = self.open_weeks()
        open_ns = [w['n'] for w in opened]
        # "This week": the latest open week whose lecture is at most 6 days away; before that, the first open week.
        soon = [w['n'] for w in opened if d(w['lecture']) <= self.today + dt.timedelta(days=6)]
        cur = soon[-1] if soon else (open_ns[0] if open_ns else None)
        course = self.data['course']['title']
        for w in opened:
            body = self.menu(w['n'], cur, open_ns) + self.week_article(w, open_ns)
            self.files.write(self.page(f'שבוע {w["n"]} · {course}', w['unit'], body), f'{pre}week-{w["n"]}.html')
        if cur:
            w = next(x for x in opened if x['n'] == cur)
            body = self.menu(cur, cur, open_ns) + self.week_article(w, open_ns)
            unit = w['unit']
        else:
            body = self.menu(None, None, []) + self.welcome()
            unit = '0'
        self.files.write(self.page(course, unit, body), f'{pre}index.html')
        print(f'{"full" if self.staff else "public"}: {len(opened)} week page(s) in /{self.folder}; current week: {cur}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slides', help='path to the slides folder (read-only)')
    ap.add_argument('--publish-week', type=int, action='append', help='add every item of this week to "published" in site.json')
    ap.add_argument('--today', help='pretend today is YYYY-MM-DD (to see the site as it will look on that day)')
    a = ap.parse_args()
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    slides = Path(a.slides or os.environ.get('SLIDES_DIR') or cfg.get('slides', ''))
    if not (slides / 'משאבים' / 'מבנה הקורס.json').is_file():
        sys.exit(f'slides folder not found: {slides}')
    data = json.loads((slides / 'משאבים' / 'מבנה הקורס.json').read_text(encoding='utf-8'))
    if a.publish_week:
        pub = list(cfg.get('published', []))
        for w in data['weeks']:
            if w['n'] in a.publish_week:
                pub += [it['code'] for it in w.get('items', []) if it.get('code') not in pub and it.get('code') != 'TO.DO']
        cfg['published'] = pub
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('published now:', ', '.join(pub))
    today = d(a.today) if a.today else None
    files = Files(REPO)
    files.copy(SITE / 'site.css', 'assets/site.css')
    for p in (SITE / 'img').iterdir():
        if p.is_file():
            files.copy(p, f'assets/img/{p.name}')
    staff_dir = cfg.get('staff_dir', 'mylab')
    everything = {it['code'] for w in data['weeks'] if w.get('lecture') for it in w.get('items', []) if it.get('code') != 'TO.DO'}
    everything |= {g.get('code') for g in data.get('general', []) if g.get('code')}
    sites = [Site(slides, cfg, data, files, set(cfg.get('published', [])), today=today),
             Site(slides, cfg, data, files, everything, folder=staff_dir, staff=True, today=today)]
    for s in sites:
        s.build()
    removed = files.prune(['assets', 'decks', 'files', staff_dir], ['week-*.html'])
    print(f'files written or updated: {files.changed}; removed: {removed}')
    for x in sorted({w for s in sites for w in s.warnings}):
        print('warning:', x)


if __name__ == '__main__':
    main()
