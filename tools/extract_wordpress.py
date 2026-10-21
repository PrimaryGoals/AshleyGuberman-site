#!/usr/bin/env python3
"""One-time importer: WordPress export (WXR .xml) -> plain content files.

    python3 tools/extract_wordpress.py /path/to/export.xml content/

Writes posts/pages as HTML files with a JSON front matter block, plus
site.json (title, categories, navigation) and attachments.json.
It deliberately drops author e-mail addresses and anything else that is not
public site content. Do not commit the raw export file.
Standard library only.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

NS = {
    'wp': 'http://wordpress.org/export/1.2/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
}

# Visible-label typo fixes (URLs are NOT changed, so old links keep working).
LABEL_OVERRIDES = {
    'Refelctions': 'Reflections',
    'how-to: web sites for coahces': 'how-to: web sites for coaches',
}


def text(el, tag):
    return el.findtext(tag, namespaces=NS) or ''


def main(xml_path, out_dir):
    ch = ET.parse(xml_path).getroot().find('channel')
    items = ch.findall('item')

    cats = {}
    for c in ch.findall('wp:category', NS):
        tid = text(c, 'wp:term_id')
        cats[tid] = {
            'id': int(tid),
            'slug': text(c, 'wp:category_nicename'),
            'name': text(c, 'wp:cat_name'),
            'parent': text(c, 'wp:category_parent') or None,
        }

    id_to_slug = {}
    for it in items:
        if text(it, 'wp:post_type') in ('post', 'page'):
            id_to_slug[text(it, 'wp:post_id')] = text(it, 'wp:post_name')

    os.makedirs(os.path.join(out_dir, 'posts'), exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'pages'), exist_ok=True)

    counts = {'post': 0, 'page': 0}
    for it in items:
        kind = text(it, 'wp:post_type')
        if kind not in ('post', 'page') or text(it, 'wp:status') != 'publish':
            continue
        slug = text(it, 'wp:post_name')
        meta = {
            'id': int(text(it, 'wp:post_id')),
            'type': kind,
            'title': it.findtext('title') or '',
            'slug': slug,
            'date': text(it, 'wp:post_date'),
            'date_gmt': text(it, 'wp:post_date_gmt'),
            'modified': text(it, 'wp:post_modified'),
            'categories': [c.get('nicename') for c in it.findall('category')
                           if c.get('domain') == 'category'],
        }
        body = text(it, 'content:encoded')
        folder = 'posts' if kind == 'post' else 'pages'
        fname = '%s_%s.html' % (meta['date'][:10], slug) if kind == 'post' else '%s.html' % slug
        with open(os.path.join(out_dir, folder, fname), 'w', encoding='utf-8') as f:
            f.write('---\n%s\n---\n%s\n' % (json.dumps(meta, ensure_ascii=False), body))
        counts[kind] += 1

    attachments = []
    for it in items:
        if text(it, 'wp:post_type') != 'attachment':
            continue
        parent = text(it, 'wp:post_parent')
        attachments.append({
            'slug': text(it, 'wp:post_name'),
            'page_path': urlparse(it.findtext('link') or '').path,
            'file_path': urlparse(text(it, 'wp:attachment_url')).path,
            'title': it.findtext('title') or '',
            'parent': id_to_slug.get(parent) if parent and parent != '0' else None,
        })
    with open(os.path.join(out_dir, 'attachments.json'), 'w', encoding='utf-8') as f:
        json.dump(attachments, f, indent=1, ensure_ascii=False)

    # Navigation tree from the 'main' menu.
    nodes = {}
    for it in items:
        if text(it, 'wp:post_type') != 'nav_menu_item':
            continue
        m = {text(x, 'wp:meta_key'): text(x, 'wp:meta_value')
             for x in it.findall('wp:postmeta', NS)}
        kind, objid = m.get('_menu_item_type'), m.get('_menu_item_object_id')
        if kind == 'taxonomy':
            c = cats[objid]
            label, href = c['name'], '/category/%s/' % c['slug']
        elif kind == 'post_type':
            label, href = (it.findtext('title') or ''), '/%s/' % id_to_slug[objid]
        else:
            label, href = (it.findtext('title') or ''), m.get('_menu_item_url') or '#'
        nodes[text(it, 'wp:post_id')] = {
            'order': int(text(it, 'wp:menu_order') or 0),
            'parent': m.get('_menu_item_menu_item_parent') or '0',
            'label': label, 'href': href, 'children': [],
        }
    nav = []
    for pid, n in sorted(nodes.items(), key=lambda kv: kv[1]['order']):
        (nodes[n['parent']]['children'] if n['parent'] in nodes else nav).append(n)

    def clean(n):
        return {'label': n['label'], 'href': n['href'], 'children': [clean(c) for c in n['children']]}

    site = {
        'title': ch.findtext('title') or '',
        'description': ch.findtext('description') or '',
        'url': (ch.findtext('link') or '').rstrip('/'),
        'author': {'slug': 'ashleyg', 'name': 'Ashley Guberman'},
        'posts_per_page': 10,
        'label_overrides': LABEL_OVERRIDES,
        'categories': sorted(cats.values(), key=lambda c: c['id']),
        'nav': [clean(n) for n in nav],
    }
    with open(os.path.join(out_dir, 'site.json'), 'w', encoding='utf-8') as f:
        json.dump(site, f, indent=1, ensure_ascii=False)

    print('posts: %d, pages: %d, attachments: %d, categories: %d' % (
        counts['post'], counts['page'], len(attachments), len(cats)))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
