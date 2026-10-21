#!/usr/bin/env python3
"""Check that every internal link and image in the built site (./public) resolves.

    python3 tools/check_site.py

Only pages produced by build.py are checked (not the preserved legacy .html files).
External links are counted, not fetched. Standard library only; no network access.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / 'public'
STATIC = ROOT / 'static'

# Paths served by _redirects rules
rewritten = set()
red = PUB / '_redirects'
if red.exists():
    for line in red.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.startswith('#'):
            rewritten.add(parts[0])


def exists(path):
    p = unquote(path.split('#')[0].split('?')[0])
    if p in rewritten:
        return True
    target = PUB / p.lstrip('/')
    if p.endswith('/'):
        return (target / 'index.html').is_file()
    return target.is_file() or (target / 'index.html').is_file()


from html.parser import HTMLParser

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
TRACKED = {'div', 'article', 'main', 'header', 'footer', 'section', 'nav', 'figure', 'figcaption', 'ul', 'ol',
           'li', 'a', 'p', 'strong', 'em', 'b', 'i', 'span', 'table', 'tr', 'td', 'th', 'blockquote', 'details',
           'summary', 'h1', 'h2', 'h3', 'h4', 'iframe', 'body', 'html'}


class Structure(HTMLParser):
    """Reports mismatched or unclosed tags (these can silently break a page's layout)."""

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag in TRACKED and tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag not in TRACKED:
            return
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        else:
            self.errors.append('stray </%s> at line %d (open: %s)' % (
                tag, self.getpos()[0], self.stack[-1][0] if self.stack else 'none'))

    def finish(self):
        self.close()
        return self.errors + ['unclosed <%s> from line %d' % (t, ln) for t, ln in self.stack]


static_html = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob('*.html')}
structure_problems = {}
broken = defaultdict(list)
external = Counter()
checked = 0
pages = [p for p in PUB.rglob('*.html') if p.relative_to(PUB).as_posix() not in static_html]
for page in pages:
    rel_page = '/' + page.relative_to(PUB).as_posix()
    base_dir = rel_page.rsplit('/', 1)[0] + '/'
    text = page.read_text(encoding='utf-8')
    sp = Structure()
    sp.feed(text)
    problems = sp.finish()
    if problems:
        structure_problems[rel_page] = problems
    for m in re.finditer(r'(?:href|src)="([^"]*)"', text):
        u = m.group(1)
        if not u or u.startswith(('#', 'mailto:', 'javascript:', 'data:')):
            continue
        if re.match(r'^(?:https?:)?//', u):
            external[re.sub(r'^(?:https?:)?//([^/]+).*', r'\1', u).lower()] += 1
            continue
        checked += 1
        if not u.startswith('/'):
            u = base_dir + u
        if not exists(u):
            broken[u].append(rel_page)

print('pages checked: %d | internal links/images checked: %d | external links: %d (%d sites)' % (
    len(pages), checked, sum(external.values()), len(external)))
known = set()
kd = ROOT / 'tools' / 'known_dead_links.txt'
if kd.exists():
    known = {l.strip() for l in kd.read_text().splitlines() if l.strip() and not l.startswith('#')}
ignored = [t for t in broken if t in known]
for t in ignored:
    del broken[t]
if ignored:
    print('(ignoring %d known-dead legacy links listed in tools/known_dead_links.txt)' % len(ignored))

failed = False
if structure_problems:
    failed = True
    print('\nHTML STRUCTURE PROBLEMS (%d pages):' % len(structure_problems))
    for pg, probs in sorted(structure_problems.items())[:25]:
        print('  %s: %s' % (pg, '; '.join(probs[:3])))
else:
    print('HTML structure OK on every generated page (no stray or unclosed tags)')
if broken:
    failed = True
    print('\nBROKEN INTERNAL TARGETS (%d):' % len(broken))
    for target, where in sorted(broken.items()):
        print('  %-55s used on: %s' % (target, ', '.join(sorted(set(where))[:3]) + (' ...' if len(set(where)) > 3 else '')))
else:
    print('no broken internal links')
sys.exit(1 if failed else 0)
