#!/usr/bin/env python3
"""Static site generator for ashleyguberman.com.

    python3 build.py            # writes the finished site into ./public

Reads ./content (posts, pages, attachments.json, site.json) and copies ./static
verbatim. Python 3.8+, standard library only. Contains no credentials and makes
no network requests.

URL structure matches the old WordPress site exactly:
  /                      home (paginated: /page/2/ ...)
  /<post-slug>/          posts and pages
  /category/<slug>/      category archives (include child categories)
  /YYYY/ and /YYYY/MM/   date archives
  /author/ashleyg/       author archive
  /<parent>/<image>/     image attachment pages
  /feed/                 RSS feed (served from /feed.xml by _redirects)
"""
import datetime as dt
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / 'content'
STATIC = ROOT / 'static'
OUT = ROOT / 'public'

SITE = json.loads((CONTENT / 'site.json').read_text(encoding='utf-8'))
SITE_URL = SITE['url']
PER_PAGE = SITE['posts_per_page']
OVERRIDES = SITE.get('label_overrides', {})
YEAR_NOW = dt.date.today().year
CSS_VERSION = '1'

esc = html.escape


def label(text):
    return OVERRIDES.get(text, text)


# --------------------------------------------------------------------------
# Content loading
# --------------------------------------------------------------------------

def parse_front(path):
    s = path.read_text(encoding='utf-8')
    if not s.startswith('---\n'):
        raise ValueError('missing front matter: %s' % path)
    head, body = s[4:].split('\n---\n', 1)
    return json.loads(head), body.rstrip('\n')


def load_entries(folder):
    out = []
    for p in sorted((CONTENT / folder).glob('*.html')):
        meta, raw = parse_front(p)
        meta['raw'] = raw
        out.append(meta)
    return out


# --------------------------------------------------------------------------
# Body rendering: reproduces what WordPress did when it displayed a post
# --------------------------------------------------------------------------

BLOCK_COMMENT = re.compile(r'<!--\s*/?wp:.*?-->', re.S)
ALLBLOCKS = (r'(?:table|thead|tfoot|caption|col|colgroup|tbody|tr|td|th|div|dl|dd|dt|ul|ol|li|pre|'
             r'form|map|area|blockquote|address|math|style|p|h[1-6]|hr|fieldset|legend|section|'
             r'article|aside|hgroup|header|footer|nav|figure|figcaption|details|menu|summary)')


def wpautop(pee):
    """Port of WordPress's wpautop(): blank lines become paragraphs, single newlines become <br />."""
    if not pee.strip():
        return ''
    pee = pee.replace('\r\n', '\n').replace('\r', '\n') + '\n'
    pee = re.sub(r'<br\s*/?>\s*<br\s*/?>', '\n\n', pee)
    pee = re.sub(r'(<' + ALLBLOCKS + r'[\s/>])', r'\n\n\1', pee)
    pee = re.sub(r'(</' + ALLBLOCKS + r'>)', r'\1\n\n', pee)
    pee = re.sub(r'\n\n+', '\n\n', pee)
    chunks = [c for c in re.split(r'\n\s*\n', pee) if c != '']
    pee = ''.join('<p>' + c.strip('\n') + '</p>\n' for c in chunks)
    pee = re.sub(r'<p>\s*</p>', '', pee)
    pee = re.sub(r'<p>([^<]+)</(div|address|form)>', r'<p>\1</p></\2>', pee)
    pee = re.sub(r'<p>\s*(</?' + ALLBLOCKS + r'[^>]*>)\s*</p>', r'\1', pee)
    pee = re.sub(r'<p>(<li.+?)</p>', r'\1', pee)
    pee = re.sub(r'<p><blockquote([^>]*)>', r'<blockquote\1><p>', pee, flags=re.I)
    pee = pee.replace('</blockquote></p>', '</p></blockquote>')
    pee = re.sub(r'<p>\s*(</?' + ALLBLOCKS + r'[^>]*>)', r'\1', pee)
    pee = re.sub(r'(</?' + ALLBLOCKS + r'[^>]*>)\s*</p>', r'\1', pee)
    pee = pee.replace('<br>', '<br />').replace('<br/>', '<br />')
    pee = re.sub(r'(?<!<br />)\s*\n', '<br />\n', pee)
    pee = re.sub(r'(</?' + ALLBLOCKS + r'[^>]*>)\s*<br />', r'\1', pee)
    pee = re.sub(r'<br />(\s*</?(?:p|li|div|dl|dd|dt|th|pre|td|ul|ol)[^>]*>)', r'\1', pee)
    pee = re.sub(r'\n</p>$', '</p>', pee)
    return pee


def youtube_iframe(video_id):
    return ('<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/%s" '
            'title="YouTube video player" loading="lazy" allowfullscreen '
            'referrerpolicy="strict-origin-when-cross-origin"></iframe></div>' % video_id)


YT_URL = r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})[^\s<"]*'


def stash(blocks, html_s):
    """Park finished HTML behind a token so wpautop() cannot mangle it; render_body() restores it."""
    blocks.append(html_s)
    return '\n\n@@BLOCK%d@@\n\n' % (len(blocks) - 1)


def convert_embeds(s, blocks):
    # Block-editor embed wrappers
    def figure(m):
        y = re.search(YT_URL, m.group(0))
        return stash(blocks, youtube_iframe(y.group(1))) if y else m.group(0)
    s = re.sub(r'<figure class="wp-block-embed[^"]*">.*?</figure>', figure, s, flags=re.S)
    # Classic editor: a bare URL on its own line becomes an embed
    s = re.sub(r'^[ \t]*' + YT_URL + r'[ \t]*$', lambda m: stash(blocks, youtube_iframe(m.group(1))), s, flags=re.M)
    return s


def convert_captions(s, blocks):
    def repl(m):
        inner = m.group(1)
        mm = re.match(r'^\s*((?:<a\b[^>]*>\s*)?<img\b[^>]*>(?:\s*</a>)?)\s*(.*)$', inner, re.S)
        if not mm:
            return stash(blocks, '<figure>%s</figure>' % inner)
        cap = mm.group(2).strip()
        return stash(blocks, '<figure class="wp-caption">%s%s</figure>' % (
            mm.group(1), '<figcaption>%s</figcaption>' % cap if cap else ''))
    return re.sub(r'\[caption[^\]]*\](.*?)\[/caption\]', repl, s, flags=re.S)


def balance_divs(s):
    """Drop orphan </div> tags and close any unclosed <div>, so one old post cannot break the page layout."""
    out, depth, pos = [], 0, 0
    for m in re.finditer(r'<div\b[^>]*>|</div\s*>', s, re.I):
        out.append(s[pos:m.start()])
        pos = m.end()
        if m.group(0).startswith('</'):
            if depth == 0:
                continue
            depth -= 1
        else:
            depth += 1
        out.append(m.group(0))
    out.append(s[pos:])
    out.append('</div>' * depth)
    return ''.join(out)


LEGACY_FIXES = {
    # relative links in 2008-era posts whose targets now live in these folders
    'BackLinks.html': '/consulting/BackLinks.html',
    'LeftBrake.mov': '/sky/LeftBrake.mov',
}
OWN_HOST = re.compile(r'^https?://(?:www\.)?(?:ashleyguberman\.com|ashleyg\.wpengine\.com)(/.*)?$', re.I)


def fix_url(u):
    u = u.strip()
    m = OWN_HOST.match(u)
    if m:
        return m.group(1) or '/'
    if u.startswith('../'):
        return '/' + u.lstrip('./')
    return LEGACY_FIXES.get(u, u)


def fix_urls(s):
    return re.sub(r'(\s(?:href|src))=(["\'])(.*?)\2',
                  lambda m: '%s=%s%s%s' % (m.group(1), m.group(2), fix_url(m.group(3)), m.group(2)),
                  s, flags=re.S)


def harden_links(s):
    def repl(m):
        tag = m.group(0)
        if 'rel=' in tag:
            return tag
        return tag[:-1] + ' rel="noopener">'
    return re.sub(r'<a\b[^>]*\btarget="_blank"[^>]*>', repl, s)


def tidy_headings(s):
    """A blank line inside a heading would be split into paragraphs; keep it a line break instead."""
    return re.sub(r'<(h[1-6])\b[^>]*>.*?</\1>',
                  lambda m: re.sub(r'\n\s*\n', '<br />', m.group(0)), s, flags=re.S | re.I)


_P_BOUNDARY = re.compile(
    r'<(/?)(p|div|ul|ol|li|dl|dd|dt|table|blockquote|h[1-6]|pre|figure|figcaption|hr|section|article|aside|'
    r'header|footer|nav|details)\b[^>]*>', re.I)


def balance_paragraphs(s):
    """Make <p> tags nest properly: close an open paragraph before the next block, drop orphan </p>."""
    out, pos, open_p = [], 0, False
    for m in _P_BOUNDARY.finditer(s):
        closing, tag = m.group(1) == '/', m.group(2).lower()
        out.append(s[pos:m.start()])
        pos = m.end()
        if tag == 'p':
            if closing:
                if open_p:
                    out.append('</p>')
                    open_p = False
            else:
                if open_p:
                    out.append('</p>')
                out.append(m.group(0))
                open_p = True
        else:
            if open_p:
                out.append('</p>')
                open_p = False
            out.append(m.group(0))
    out.append(s[pos:])
    if open_p:
        out.append('</p>')
    return ''.join(out)


def lazy_images(s):
    def repl(m):
        tag = m.group(0)
        if 'loading=' not in tag:
            tag = tag.replace('<img', '<img loading="lazy" decoding="async"', 1)
        return tag
    return re.sub(r'<img\b[^>]*>', repl, s)


def render_body(raw):
    blocks = []
    s = BLOCK_COMMENT.sub('', raw)
    s = convert_embeds(s, blocks)
    s = convert_captions(s, blocks)
    s = balance_divs(s)
    s = tidy_headings(s)
    s = wpautop(s)
    s = balance_paragraphs(s)
    for i, b in enumerate(blocks):
        s = s.replace('<p>@@BLOCK%d@@</p>' % i, b).replace('@@BLOCK%d@@' % i, b)
    s = fix_urls(s)
    s = harden_links(s)
    s = lazy_images(s)
    return s


def text_of(html_s):
    t = re.sub(r'<(script|style)\b.*?</\1>', ' ', html_s, flags=re.S)
    t = re.sub(r'</?(?:p|div|br|li|ul|ol|h[1-6]|figure|figcaption|blockquote|tr|td|th)\b[^>]*>', ' ', t)
    t = re.sub(r'<[^>]+>', '', t)  # inline tags (links, emphasis) leave no gap
    return re.sub(r'\s+', ' ', html.unescape(t)).strip()


def make_excerpt(body_html, words=48):
    t = text_of(body_html)
    w = t.split(' ')
    if len(w) <= words:
        return t
    return ' '.join(w[:words]).rstrip(' ,;:-') + '…'


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def parse_dt(s):
    return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')


def fmt_date(d):
    return '%s %d, %d' % (d.strftime('%B'), d.day, d.year)


def month_name(d):
    return d.strftime('%B')


# --------------------------------------------------------------------------
# HTML building blocks
# --------------------------------------------------------------------------

def render_nav(nodes, depth=0):
    out = []
    for n in nodes:
        name, href = esc(label(n['label'])), n['href']
        ext = ' rel="noopener"' if href.startswith('http') else ''
        kids = n['children']
        if not kids:
            out.append('<li><a href="%s"%s>%s</a></li>' % (esc(href), ext, name))
        elif href == '#':
            out.append('<li class="group"><span>%s</span><ul>%s</ul></li>' % (name, render_nav(kids, depth + 1)))
        else:
            out.append('<li class="has-sub"><details><summary>%s</summary><ul>'
                       '<li><a href="%s">All: %s</a></li>%s</ul></details></li>' % (
                           name, esc(href), name, render_nav(kids, depth + 1)))
    return ''.join(out)


def head_html(title, path, description, kind, noindex):
    full = title if title.startswith(SITE['title']) else '%s — %s' % (title, SITE['title'])
    canonical = SITE_URL + path
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ''
    return '\n'.join([
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>%s</title>' % esc(full),
        '<meta name="description" content="%s">' % esc(description or SITE['description']),
        robots,
        '<link rel="canonical" href="%s">' % esc(canonical),
        '<link rel="alternate" type="application/rss+xml" title="%s" href="/feed/">' % esc(SITE['title']),
        '<link rel="icon" href="/favicon.ico">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        '<meta property="og:site_name" content="%s">' % esc(SITE['title']),
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:type" content="%s">' % kind,
        '<meta property="og:url" content="%s">' % esc(canonical),
        '<meta property="og:description" content="%s">' % esc(description or SITE['description']),
        '<link rel="stylesheet" href="/assets/site.css?v=%s">' % CSS_VERSION,
        '</head>',
    ])


def footer_html(cat_counts, years):
    cats = ''.join('<li><a href="/category/%s/">%s</a> <span class="count">%d</span></li>' % (
        c['slug'], esc(label(c['name'])), cat_counts.get(c['slug'], 0)) for c in SITE['categories']
        if cat_counts.get(c['slug'], 0))
    yrs = ' '.join('<a href="/%d/">%d</a>' % (y, y) for y in years)
    return (
        '<footer class="site-footer"><div class="wrap">'
        '<div class="cols">'
        '<section><h2>Categories</h2><ul>%s</ul></section>'
        '<section><h2>Archives</h2><p class="years">%s</p></section>'
        '<section><h2>More</h2><ul><li><a href="/blog/">All posts, by year</a></li>'
        '<li><a href="/where-to-start/">My other sites</a></li>'
        '<li><a href="/feed/">RSS feed</a></li></ul></section>'
        '</div>'
        '<p class="legal">© %d Ashley Guberman. All rights reserved.</p>'
        '</div></footer>' % (cats, yrs, YEAR_NOW))


NAV_SCRIPT = """<script>
document.addEventListener('click',function(e){document.querySelectorAll('.nav details[open]').forEach(function(d){if(!d.contains(e.target))d.removeAttribute('open');});});
document.addEventListener('keydown',function(e){if(e.key==='Escape')document.querySelectorAll('.nav details[open]').forEach(function(d){d.removeAttribute('open');});});
</script>"""


class Site:
    def __init__(self):
        self.pages = {}   # url path -> html string (or bytes for xml)
        self.sitemap = []  # (path, lastmod)

    def add(self, path, content, sitemap=True, lastmod=None):
        if path in self.pages:
            raise SystemExit('URL collision: %s' % path)
        self.pages[path] = content
        if sitemap:
            self.sitemap.append((path, lastmod))


def shell(site, *, title, body, path, description='', kind='website', noindex=False, nav_html, footer):
    return '\n'.join([
        head_html(title, path, description, kind, noindex),
        '<body>',
        '<a class="skip" href="#main">Skip to content</a>',
        '<header class="site-header"><div class="wrap">',
        '<p class="site-title"><a href="/">%s</a></p>' % esc(SITE['title']),
        '<p class="tagline">%s</p>' % esc(SITE['description']),
        '<nav aria-label="Primary"><ul class="nav">%s</ul></nav>' % nav_html,
        '</div></header>',
        '<main id="main" class="wrap">',
        body,
        '</main>',
        footer,
        NAV_SCRIPT,
        '</body></html>',
    ])


def pager(base, page, total):
    if total <= 1:
        return ''
    def url(n):
        return base if n == 1 else '%spage/%d/' % (base, n)
    newer = '<a rel="prev" href="%s">« Newer posts</a>' % url(page - 1) if page > 1 else '<span></span>'
    older = '<a rel="next" href="%s">Older posts »</a>' % url(page + 1) if page < total else '<span></span>'
    return '<nav class="pager" aria-label="Pagination">%s<span class="pageno">Page %d of %d</span>%s</nav>' % (
        newer, page, total, older)


def main():
    posts = load_entries('posts')
    pages = load_entries('pages')
    attachments = json.loads((CONTENT / 'attachments.json').read_text(encoding='utf-8'))

    cats = {c['slug']: c for c in SITE['categories']}
    children = defaultdict(list)
    for c in SITE['categories']:
        if c['parent']:
            children[c['parent']].append(c['slug'])

    for p in posts:
        p['dt'] = parse_dt(p['date'])
        p['body'] = render_body(p['raw'])
        p['excerpt'] = make_excerpt(p['body'])
        p['path'] = '/%s/' % p['slug']
    posts.sort(key=lambda p: (p['dt'], p['id']), reverse=True)  # newest first

    by_slug = {p['slug']: p for p in posts}
    cat_counts = defaultdict(int)
    for p in posts:
        for s in set(p['categories']):
            cat_counts[s] += 1
    # a category archive also lists its children's posts (WordPress behaviour)
    def cat_posts(slug):
        want = {slug, *children.get(slug, [])}
        return [p for p in posts if want & set(p['categories'])]
    for slug in cats:
        cat_counts[slug] = len(cat_posts(slug))

    years = sorted({p['dt'].year for p in posts})
    nav_html = render_nav(SITE['nav'])
    footer = footer_html(cat_counts, years)
    site = Site()

    def page(path, title, body, **kw):
        return shell(site, title=title, body=body, path=path, nav_html=nav_html, footer=footer, **kw)

    def cat_links(p):
        return ', '.join('<a href="/category/%s/">%s</a>' % (s, esc(label(cats[s]['name'])))
                         for s in p['categories'] if s in cats)

    def entry(p):
        meta = '<time datetime="%s">%s</time>' % (p['dt'].date().isoformat(), fmt_date(p['dt']))
        if p['categories']:
            meta += ' · ' + cat_links(p)
        ex = '<p class="excerpt">%s</p>' % esc(p['excerpt']) if p['excerpt'] else ''
        return ('<article class="entry"><h2><a href="%s">%s</a></h2><p class="meta">%s</p>%s'
                '<p class="more"><a href="%s" aria-label="Read %s">Read more →</a></p></article>' % (
                    p['path'], esc(p['title']), meta, ex, p['path'], esc(p['title'])))

    def listing(base, title, intro, plist, *, description='', noindex=False, sitemap=True):
        total = max(1, -(-len(plist) // PER_PAGE))
        for n in range(1, total + 1):
            chunk = plist[(n - 1) * PER_PAGE:n * PER_PAGE]
            path = base if n == 1 else '%spage/%d/' % (base, n)
            head = '<header class="archive-head"><h1>%s</h1>%s</header>' % (esc(title), intro) if title else ''
            body = head + ''.join(entry(p) for p in chunk) + pager(base, n, total)
            if not title:  # the home page and its pages
                ttl = SITE['title'] + ' — ' + SITE['description'] if n == 1 else 'Page %d — %s' % (n, SITE['title'])
            else:
                ttl = title if n == 1 else '%s (page %d)' % (title, n)
            site.add(path, page(path, ttl, body, description=description, noindex=noindex),
                     sitemap=sitemap and n == 1)

    # ---- home ------------------------------------------------------------
    listing('/', '', '', posts, description=SITE['description'])

    # ---- posts -----------------------------------------------------------
    for i, p in enumerate(posts):
        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        meta = '<time datetime="%s">%s</time>' % (p['dt'].date().isoformat(), fmt_date(p['dt']))
        foot = ''
        if p['categories']:
            foot += '<p class="filed">Filed under: %s</p>' % cat_links(p)
        nav_p = '<nav class="pager" aria-label="More posts">%s<span></span>%s</nav>' % (
            ('<a rel="prev" href="%s">« %s</a>' % (older['path'], esc(older['title']))) if older else '<span></span>',
            ('<a rel="next" href="%s">%s »</a>' % (newer['path'], esc(newer['title']))) if newer else '<span></span>')
        body = ('<article class="post"><header><h1>%s</h1><p class="meta">%s%s</p></header>'
                '<div class="prose">%s</div><footer>%s</footer></article>%s' % (
                    esc(p['title']), meta, (' · ' + cat_links(p)) if p['categories'] else '',
                    p['body'], foot, nav_p))
        desc = p['excerpt'][:155] if p['excerpt'] else ''
        site.add(p['path'], page(p['path'], p['title'], body, description=desc, kind='article'),
                 lastmod=p['modified'][:10])

    # ---- pages -----------------------------------------------------------
    for pg in pages:
        path = '/%s/' % pg['slug']
        if pg['slug'] == 'blog':
            by_year = defaultdict(list)
            for p in posts:
                by_year[p['dt'].year].append(p)
            parts = ['<header class="archive-head"><h1>Blog</h1><p class="lede">Every post, newest first.</p></header>']
            for y in sorted(by_year, reverse=True):
                parts.append('<h2 class="year"><a href="/%d/">%d</a></h2><ul class="postlist">' % (y, y))
                for p in by_year[y]:
                    parts.append('<li><a href="%s">%s</a> <span class="when">%s</span></li>' % (
                        p['path'], esc(p['title']), '%s %d' % (p['dt'].strftime('%b'), p['dt'].day)))
                parts.append('</ul>')
            site.add(path, page(path, 'Blog', ''.join(parts), description='All posts on %s' % SITE['title']),
                     lastmod=pg['modified'][:10])
            continue
        body = '<article class="post"><header><h1>%s</h1></header><div class="prose">%s</div></article>' % (
            esc(pg['title']), render_body(pg['raw']))
        site.add(path, page(path, pg['title'], body), lastmod=pg['modified'][:10])

    # ---- category archives ---------------------------------------------
    for slug, c in cats.items():
        plist = cat_posts(slug)
        if plist:
            listing('/category/%s/' % slug, label(c['name']),
                    '<p class="lede">%d post%s</p>' % (len(plist), '' if len(plist) == 1 else 's'), plist)

    # ---- date archives ---------------------------------------------------
    for y in years:
        plist = [p for p in posts if p['dt'].year == y]
        listing('/%d/' % y, str(y), '<p class="lede">%d post%s</p>' % (len(plist), '' if len(plist) == 1 else 's'),
                plist, sitemap=False)
        for m in sorted({p['dt'].month for p in plist}):
            mlist = [p for p in plist if p['dt'].month == m]
            listing('/%d/%02d/' % (y, m), '%s %d' % (month_name(mlist[0]['dt']), y),
                    '<p class="lede">%d post%s</p>' % (len(mlist), '' if len(mlist) == 1 else 's'), mlist,
                    sitemap=False)

    # ---- author archive --------------------------------------------------
    listing('/author/%s/' % SITE['author']['slug'], 'Posts by %s' % SITE['author']['name'], '', posts)

    # ---- attachment pages -----------------------------------------------
    for a in attachments:
        parent = by_slug.get(a['parent']) if a['parent'] else None
        back = ('<p class="back">From: <a href="%s">%s</a></p>' % (parent['path'], esc(parent['title']))) if parent else ''
        body = ('<article class="post attachment"><header><h1>%s</h1></header>'
                '<figure><img src="%s" alt="%s"></figure>%s</article>' % (
                    esc(a['title']), esc(a['file_path']), esc(a['title']), back))
        site.add(a['page_path'], page(a['page_path'], a['title'], body, noindex=True), sitemap=False)

    # ---- 404 --------------------------------------------------------------
    body = ('<article class="post"><header><h1>Page not found</h1></header><div class="prose">'
            '<p>That page doesn’t exist here. Try the <a href="/">home page</a>, '
            'the <a href="/blog/">full list of posts</a>, or one of the categories in the menu above.</p></div></article>')
    site.pages['/404.html'] = page('/404.html', 'Page not found', body, noindex=True)

    # ---- feeds, sitemap, robots -------------------------------------------
    def absolute(h):
        return re.sub(r'(\s(?:href|src))="/(?!/)', r'\1="%s/' % SITE_URL, h)

    def rfc822(p):
        g = p.get('date_gmt') or ''
        d = parse_dt(g) if g and not g.startswith('0000') else p['dt']
        return d.strftime('%a, %d %b %Y %H:%M:%S +0000')

    def cdata(s):
        return '<![CDATA[%s]]>' % s.replace(']]>', ']]]]><![CDATA[>')

    latest = posts[:20]
    items = []
    for p in latest:
        items.append(
            '<item><title>%s</title><link>%s%s</link><guid isPermaLink="true">%s%s</guid>'
            '<pubDate>%s</pubDate>%s<description>%s</description>'
            '<content:encoded>%s</content:encoded></item>' % (
                esc(p['title']), SITE_URL, p['path'], SITE_URL, p['path'], rfc822(p),
                ''.join('<category>%s</category>' % esc(label(cats[s]['name'])) for s in p['categories'] if s in cats),
                esc(p['excerpt']), cdata(absolute(p['body']))))
    feed = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            'xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>%s</title>'
            '<atom:link href="%s/feed/" rel="self" type="application/rss+xml"/><link>%s/</link>'
            '<description>%s</description><language>en-US</language><lastBuildDate>%s</lastBuildDate>%s</channel></rss>\n' % (
                esc(SITE['title']), SITE_URL, SITE_URL, esc(SITE['description']), rfc822(latest[0]), ''.join(items)))
    site.pages['/feed.xml'] = feed
    site.pages['/comments-feed.xml'] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Comments on %s</title>'
        '<link>%s/</link><description>%s</description></channel></rss>\n' % (esc(SITE['title']), SITE_URL, esc(SITE['description'])))

    urls = []
    for path, lastmod in sorted(site.sitemap):
        urls.append('<url><loc>%s%s</loc>%s</url>' % (SITE_URL, esc(path), '<lastmod>%s</lastmod>' % lastmod if lastmod else ''))
    sm = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n' % ''.join(urls))
    site.pages['/wp-sitemap.xml'] = sm
    site.pages['/sitemap.xml'] = sm
    site.pages['/robots.txt'] = 'User-agent: *\nAllow: /\n\nSitemap: %s/wp-sitemap.xml\n' % SITE_URL

    site.pages['/_headers'] = '\n'.join([
        '# Applies to every response',
        '/*',
        '  X-Content-Type-Options: nosniff',
        '  Referrer-Policy: strict-origin-when-cross-origin',
        '  X-Frame-Options: SAMEORIGIN',
        '',
        '# Static assets can be cached for a long time',
        '/wp-content/uploads/*',
        '  Cache-Control: public, max-age=31536000',
        '/images/*',
        '  Cache-Control: public, max-age=31536000',
        '',
        '# Keep the temporary pages.dev addresses (previews) out of search engines',
        'https://*.pages.dev/*',
        '  X-Robots-Tag: noindex',
        '',
    ])
    site.pages['/_redirects'] = '\n'.join([
        '# The feeds are plain files; serve them at their old WordPress addresses',
        '/feed /feed.xml 200',
        '/feed/ /feed.xml 200',
        '/comments/feed /comments-feed.xml 200',
        '/comments/feed/ /comments-feed.xml 200',
        '',
        '# Old WordPress sitemap files now live in one file',
        '/wp-sitemap-posts-post-1.xml /wp-sitemap.xml 301',
        '/wp-sitemap-posts-page-1.xml /wp-sitemap.xml 301',
        '/wp-sitemap-taxonomies-category-1.xml /wp-sitemap.xml 301',
        '/wp-sitemap-users-1.xml /wp-sitemap.xml 301',
        '',
    ])

    # ---- write -----------------------------------------------------------
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(STATIC, OUT)
    for path, content in site.pages.items():
        if path.endswith('/'):
            target = OUT / path.lstrip('/') / 'index.html'
        else:
            target = OUT / path.lstrip('/')
        if target.exists():
            raise SystemExit('Refusing to overwrite a static file: %s' % path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')

    n_files = sum(1 for _ in OUT.rglob('*') if _.is_file())
    print('built %d pages from %d posts; %d files in public/' % (len(site.pages), len(posts), n_files))


if __name__ == '__main__':
    sys.exit(main())
