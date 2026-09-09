#!/usr/bin/env python3
"""Search and acquire game resources with Python's standard library; never install them."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
STORE = 'https://store.godotengine.org/api/v1/'
ASSETLIB = 'https://godotengine.org/asset-library/api/'
UA = 'AgentEvolverAssets/1.0'


class AssetError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise AssetError(code, message)


def https(url):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username,
            'invalid_url', 'Resource URLs must use HTTPS without embedded credentials')
    return url


def public_url(url):
    """Do not persist signed CDN queries or transient itch download keys."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HTTP:
    def __init__(self, deadline=120):
        self.expires = time.monotonic() + deadline
        self.opener = build_opener(Redirects(), HTTPCookieProcessor(CookieJar()))

    def remaining(self):
        left = self.expires - time.monotonic()
        require(left > 0, 'timeout', 'Acquisition deadline exceeded')
        return min(30, left)

    def open(self, url, form=None):
        request = Request(https(url), data=urlencode(form).encode() if form is not None else None,
                          headers={'User-Agent': UA})
        return self.opener.open(request, timeout=self.remaining())

    def data(self, url, form=None):
        with self.open(url, form) as response:
            content = response.read(8 * 1024 * 1024 + 1)
        require(len(content) <= 8 * 1024 * 1024, 'metadata_too_large', 'Metadata exceeds 8 MiB')
        return content.decode('utf-8')

    def json(self, url, form=None):
        try:
            return json.loads(self.data(url, form))
        except (ValueError, UnicodeError) as error:
            raise AssetError('invalid_response', 'Provider returned non-JSON metadata') from error

    def download(self, url, path, limit):
        digest = hashlib.sha256()
        size = 0
        with self.open(url) as response, path.open('xb') as output:
            if response.headers.get('Content-Length'):
                require(int(response.headers['Content-Length']) <= limit,
                        'download_too_large', 'Archive exceeds download size limit')
            while True:
                self.remaining()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                require(size <= limit, 'download_too_large', 'Archive exceeds download size limit')
                digest.update(chunk)
                output.write(chunk)
            final_url = public_url(response.geturl())
        return {'bytes': size, 'sha256': digest.hexdigest(), 'final_url': final_url}


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.uploads, self.csrf = [], [], ''
        self.upload_names = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if attrs.get('data-upload_id'):
            self.uploads.append(attrs['data-upload_id'])
        if tag == 'strong' and attrs.get('title') and self.uploads:
            self.upload_names[self.uploads[-1]] = attrs['title']
        if tag == 'meta' and attrs.get('name') == 'csrf_token':
            self.csrf = attrs.get('value', attrs.get('content', ''))


def catalog():
    return json.loads((ROOT / 'resources/catalog.json').read_text())['assets']


def store_record(asset):
    return dict(id=f"store:{asset['publisher']['slug']}/{asset['slug']}",
                name=asset['name'], author=asset['publisher']['name'],
                description=asset.get('description', '')[:700], page=asset['store_url'],
                license=asset.get('license_type'), license_url=asset.get('license_url'),
                price_cent=asset.get('price_cent'),
                tags=[t['slug'] for t in asset.get('tags', [])])


def library_record(asset):
    return dict(id=f"assetlib:{asset['asset_id']}", name=asset['title'], author=asset['author'],
                page=f"https://godotengine.org/asset-library/asset/{asset['asset_id']}",
                license=asset.get('cost'), version=asset.get('version_string'),
                declared_godot=asset.get('godot_version'), price_cent=0)


def search(http, args):
    results, errors, pages = [], [], {}
    providers = ['catalog', 'store', 'assetlib'] if args.provider == 'all' else [args.provider]
    for provider in providers:
        try:
            if provider == 'catalog':
                terms = args.query.casefold().split()
                hits = [a for a in catalog() if all(t in json.dumps(a, ensure_ascii=False).casefold() for t in terms)]
                start = (args.page - 1) * args.limit
                results.extend(hits[start:start + args.limit])
                pages[provider] = {'total': len(hits), 'page': args.page}
            elif provider == 'store':
                query = urlencode(dict(query=args.query, batch_size=args.limit, page=args.page,
                                       compatibility=args.godot, stable_only='true'))
                data = http.json(STORE + 'search/query/?' + query)
                results.extend(store_record(h['asset']) for h in data['hits'])
                pages[provider] = {'total': data['count'], 'page': args.page}
            else:
                query = urlencode(dict(filter=args.query, max_results=args.limit, page=args.page - 1,
                                       godot_version=args.godot, type='any'))
                data = http.json(ASSETLIB + 'asset?' + query)
                results.extend(library_record(a) for a in data['result'])
                pages[provider] = {'total': data['total_items'], 'page': args.page}
        except (AssetError, HTTPError, URLError, TimeoutError, KeyError, TypeError, ValueError) as error:
            errors.append({'provider': provider, 'error': error_message(error)})
    return dict(success=not errors, partial=bool(results and errors), results=results,
                pagination=pages, errors=errors, compatibility='Provider metadata only; import/play not verified')


def itch_files(http, page):
    """Use itch's public zero-price download flow, without login or checkout."""
    html = http.data(page)
    match = re.search(r'"min_price"\s*:\s*(\d+)', html)
    require(match and int(match.group(1)) == 0, 'manual_download',
            'No verified zero-price itch download; inspect the official page')
    parser = Links(html)
    require(parser.csrf, 'provider_changed', 'itch page has no download token')
    response = http.json(page.rstrip('/') + '/download_url', {'csrf_token': parser.csrf})
    target = response.get('url', '')
    require(urlsplit(target).netloc == urlsplit(page).netloc and '/download/' in target,
            'manual_download', 'itch did not return a public free download page')
    parser = Links(http.data(target))
    require(parser.uploads and parser.csrf, 'manual_download', 'No downloadable free files on itch page')
    files = []
    for upload in dict.fromkeys(parser.uploads):
        require(upload.isdigit(), 'provider_changed', 'Invalid itch upload ID')
        # Only IDs exposed by the zero-price download page are eligible.
        files.append(dict(key=upload, name=parser.upload_names.get(upload, 'Free upload ' + upload),
                          version='free-upload-' + upload, kind='zip',
                          _itch_page=page, _csrf=parser.csrf,
                          _download_key=urlsplit(target).path.rstrip('/').rsplit('/', 1)[-1]))
    return files


def info(http, resource_id, godot):
    require(re.fullmatch(r'[a-z]+:[A-Za-z0-9_./-]+', resource_id) and '..' not in resource_id,
            'invalid_id', 'Use a resource ID returned by search')
    provider, key = resource_id.split(':', 1)
    if provider == 'store':
        require(re.fullmatch(r'[\w-]+/[\w-]+', key), 'invalid_id', 'Expected store:publisher/asset')
        result = store_record(http.json(STORE + f'assets/{key}/'))
        query = urlencode(dict(compatibility=godot, stable_only='true'))
        releases = http.json(STORE + f'releases/{key}/?' + query)
        result['files'] = [dict(key=str(r['id']), version=r['version'], url=r['download_url'], kind='zip',
                                min_godot=r.get('min_godot_version'), max_godot=r.get('max_godot_version'))
                           for r in releases if r.get('download_url')]
    elif provider == 'assetlib':
        require(key.isdigit(), 'invalid_id', 'Expected assetlib:numeric-id')
        data = http.json(ASSETLIB + 'asset/' + key)
        result = library_record(data)
        # AssetLib's version field is the entry's target/minimum, not a tested compatibility range.
        minimum = tuple((list(map(int, result['declared_godot'].split('.'))) + [0, 0])[:3])
        target = tuple((list(map(int, godot.split('.'))) + [0, 0])[:3])
        require(minimum[0] == target[0] and minimum <= target,
                'incompatible_version', 'AssetLib entry targets a different/newer Godot version')
        result['files'] = [dict(key=str(data['version']), version=data['version_string'],
                                url=data['download_url'], kind='zip',
                                upstream_sha256=data.get('download_hash', ''),
                                source_revision=data.get('download_commit'))]
    elif provider == 'catalog':
        entry = next((a for a in catalog() if a['id'] == resource_id), None)
        require(entry, 'unknown_id', 'Resource is not in the curated catalog')
        result = dict(entry)
        if entry['adapter'] == 'store':
            resolved = info(http, entry['remote_id'], godot)
            result.update(files=resolved['files'], resolved_id=resolved['id'],
                          license=resolved['license'], license_url=resolved.get('license_url'),
                          price_cent=resolved['price_cent'])
        elif entry['adapter'] == 'kenney':
            parser = Links(http.data(entry['page']))
            urls = list(dict.fromkeys(urljoin(entry['page'], u) for u in parser.links
                                      if urlsplit(u).path.lower().endswith('.zip')))
            result['files'] = [dict(key=str(i + 1), name=urlsplit(u).path.rsplit('/', 1)[-1],
                                   url=u, kind='zip', version='current-page')
                               for i, u in enumerate(urls)]
        elif entry['adapter'] == 'itch':
            result['files'] = itch_files(http, entry['page'])
        elif entry['adapter'] == 'github':
            commit = http.json('https://api.github.com/repos/' + entry['repo'] + '/commits/HEAD')['sha']
            require(re.fullmatch('[0-9a-f]{40}', commit), 'provider_changed', 'Invalid GitHub commit')
            result['files'] = [dict(key=commit, version=commit, source_revision=commit, kind='zip',
                                    url=f"https://codeload.github.com/{entry['repo']}/zip/{commit}")]
        else:
            result.update(files=[], download_status='manual', reason=entry['download_note'])
    else:
        raise AssetError('invalid_provider', 'Use catalog:, store:, or assetlib: IDs')
    result['requested_godot'] = godot
    result['verification'] = 'not_imported_or_played'
    return result


def clean_info(result):
    # Keep ephemeral itch cookies/keys and signed URLs in memory only.
    result = {k: v for k, v in result.items() if k != 'files'} | {'files': [
        {k: public_url(v) if k == 'url' else v for k, v in file.items() if not k.startswith('_')}
        for file in result.get('files', [])]}
    return result


def archive_members(bundle, expanded_limit):
    members = bundle.infolist()
    require(len(members) <= 20000, 'unsafe_archive', 'Too many archive members')
    require(sum(m.file_size for m in members) <= expanded_limit,
            'unsafe_archive', 'Archive exceeds expanded size limit')
    paths = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        mode = stat.S_IFMT(member.external_attr >> 16)
        require(name and not path.is_absolute() and '..' not in path.parts
                and '\\' not in name and ':' not in name and '\x00' not in name
                and mode in (0, stat.S_IFREG, stat.S_IFDIR),
                'unsafe_archive', 'Archive contains an unsafe path or special file')
        key = str(path).casefold()
        require(key not in paths and str(path) != '.', 'unsafe_archive', 'Duplicate/empty archive path')
        paths.add(key)
    return members


def download(http, args):
    resource = info(http, args.id, args.godot)
    require(resource.get('price_cent') == 0, 'paid_or_unknown_price', 'Only verified free resources are downloaded')
    files = resource.get('files', [])
    if args.file:
        files = [f for f in files if f['key'] == args.file]
    require(files, 'manual_download', resource.get('reason', 'No eligible file; run info and inspect the official page'))
    require(len(files) == 1, 'choose_file', 'Multiple files/releases available; use info then download --file KEY')
    selected = dict(files[0])
    if '_itch_page' in selected:
        data = http.json(selected['_itch_page'] + '/file/' + selected['key'],
                         {'csrf_token': selected['_csrf'], 'download_key': selected['_download_key']})
        selected['url'] = data.get('url', '')
    require(selected.get('url'), 'manual_download', 'Provider supplied no file URL')
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.asset-', dir=out))
    try:
        archive = stage / 'original.zip'
        receipt = http.download(selected['url'], archive, args.max_mib * 1024 * 1024)
        expected = args.sha256 or selected.get('upstream_sha256')
        require(not expected or receipt['sha256'] == expected.lower(), 'checksum_mismatch', 'SHA-256 mismatch')
        listing = []
        with zipfile.ZipFile(archive) as bundle:
            members = archive_members(bundle, args.max_expanded_mib * 1024 * 1024)
            for member in members:
                http.remaining()
                if member.is_dir():
                    continue
                digest = hashlib.sha256()
                dest = stage / 'files' / member.filename
                output = None
                try:
                    if args.extract:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        output = dest.open('xb')
                    with bundle.open(member) as content:
                        while chunk := content.read(256 * 1024):
                            http.remaining()
                            digest.update(chunk)
                            if output:
                                output.write(chunk)
                finally:
                    if output:
                        output.close()
                listing.append(dict(path=member.filename, bytes=member.file_size, sha256=digest.hexdigest()))
        receipt.update(resource=clean_info(resource), selected_file=selected['key'],
                       version=selected['version'], source_revision=selected.get('source_revision'),
                       acquired_at=datetime.now(timezone.utc).isoformat(),
                       extracted=args.extract, files=listing,
                       license_files=[f['path'] for f in listing if re.search(r'licen[cs]e|copying|copyright', f['path'], re.I)],
                       verification='archive_verified; Godot import, code review and visual review pending')
        (stage / 'manifest.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
        folder = re.sub(r'[^a-zA-Z0-9_-]', '-', args.id) + '-' + receipt['sha256'][:16]
        target = out / folder
        require(not target.exists(), 'already_exists', f'Archive already staged at {target}; inspect its manifest instead of overwriting')
        stage.rename(target)
        return dict(success=True, id=args.id, directory=str(target), archive=str(target / 'original.zip'),
                    manifest=str(target / 'manifest.json'), files=len(listing), bytes=receipt['bytes'],
                    sha256=receipt['sha256'], extracted=args.extract, verification=receipt['verification'])
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def error_message(error):
    if isinstance(error, HTTPError):
        return f'Provider HTTP {error.code}; login, rate limit, or unavailable resource may require manual inspection'
    if isinstance(error, AssetError):
        return str(error)
    return type(error).__name__ + ': provider/network or archive operation failed'


def positive(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('Must be positive')
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['search', 'info', 'download']:
        p = commands.add_parser(name)
        p.add_argument('--godot', default='4.7')
        p.add_argument('--deadline', type=positive, default=120, help='Total network/archive work budget in seconds')
        if name == 'search':
            p.add_argument('query', nargs='?', default='')
            p.add_argument('--provider', choices=['catalog', 'store', 'assetlib', 'all'], default='all')
            p.add_argument('--limit', type=positive, default=10, help='Results per provider, maximum 50')
            p.add_argument('--page', type=positive, default=1)
        else:
            p.add_argument('id')
        if name == 'download':
            p.add_argument('--file', help='Exact file/release key from info; required when multiple are offered')
            p.add_argument('--out', required=True, help='Workspace staging directory outside the Godot project')
            p.add_argument('--extract', action='store_true', help='Extract validated archive into staging, never auto-install')
            p.add_argument('--max-mib', type=positive, default=64)
            p.add_argument('--max-expanded-mib', type=positive, default=512)
            p.add_argument('--sha256', help='Optional expected SHA-256 for reproducible downloads')
    args = parser.parse_args(argv)
    try:
        require(re.fullmatch(r'\d+\.\d+(?:\.\d+)?', args.godot), 'invalid_version', 'Expected major.minor[.patch]')
        if args.command == 'search':
            require(args.limit <= 50, 'invalid_limit', '--limit must be at most 50')
        if args.command == 'download' and args.sha256:
            require(re.fullmatch('[0-9a-fA-F]{64}', args.sha256), 'invalid_checksum', 'Expected a SHA-256 hex digest')
        http = HTTP(args.deadline)
        if args.command == 'search':
            result = search(http, args)
        elif args.command == 'info':
            result = {'success': True, 'resource': clean_info(info(http, args.id, args.godot))}
        else:
            result = download(http, args)
    except (AssetError, HTTPError, URLError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as error:
        result = dict(success=False, error=getattr(error, 'code', type(error).__name__), message=error_message(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['success'] else 2


if __name__ == '__main__':
    sys.exit(main())
