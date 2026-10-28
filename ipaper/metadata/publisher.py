"""Public publisher citation metadata; page text is data, never instructions."""
import re
from html.parser import HTMLParser


def usenix_path(url):
    match = re.fullmatch(r'https://www\.usenix\.org(/conference/[a-z0-9-]+/presentation/[a-z0-9-]+)/?', url)
    return match[1] if match else None


def usenix_payload(html):
    class Citations(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.meta, self.abstract, self.depth, self.capture = {}, [], 0, None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'meta':
                name = attrs.get('name', '').lower()
                if name.startswith('citation_'):
                    self.meta.setdefault(name[9:], []).append(attrs.get('content', ''))
            if tag == 'div':
                self.depth += 1
                if 'field-name-field-paper-description' in attrs.get('class', '').split():
                    self.capture = self.depth

        def handle_endtag(self, tag):
            if tag == 'div':
                if self.capture == self.depth:
                    self.capture = None
                self.depth -= 1

        def handle_data(self, text):
            if self.capture is not None:
                self.abstract.append(text)

    parser = Citations()
    parser.feed(html)
    return {'citation': parser.meta, 'abstract': ' '.join(parser.abstract)}


def usenix_records(data):
    from .providers import record, _plain
    meta = data.get('citation', {})
    def value(key):
        return _plain((meta.get(key) or [''])[0])
    url = data.get('url', '')
    if not usenix_path(url) or not value('title') or not meta.get('author'):
        return []
    first, last = value('firstpage'), value('lastpage')
    fields = {
        'title': value('title').replace('{', '').replace('}', ''),
        'author_list': [{'name': _plain(name), 'kind': 'person'} for name in meta['author'] if _plain(name)],
        'affiliation': '; '.join(dict.fromkeys(_plain(x) for x in meta.get('author_institution', []) if _plain(x))),
        'journal': value('conference_title'), 'venue_type': 'conference',
        'year': value('publication_date')[:4], 'published_date': value('publication_date'),
        'abstract': _plain(data.get('abstract', '')), 'source_url': url,
        'pages': first + ('--' + last if last and last != first else ''),
    }
    return [record('usenix', url, fields, url)]
