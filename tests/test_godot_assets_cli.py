"""Offline contracts for skill acquisition; no model, engine or external requests."""
import hashlib
import importlib.util
import io
import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / 'agentevolver/skill/game/godot_game_development_skill/scripts/assets.py'
spec = importlib.util.spec_from_file_location('godot_assets_cli', SCRIPT)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, data in entries:
            bundle.writestr(name, data)
    return buffer.getvalue()


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.downloads = 0

    def remaining(self):
        return 10

    def download(self, url, path, limit):
        self.downloads += 1
        path.write_bytes(self.payload)
        return dict(bytes=len(self.payload), sha256=hashlib.sha256(self.payload).hexdigest(),
                    final_url='https://example.org/free.zip')


def options(tmp_path, **updates):
    return SimpleNamespace(**(dict(id='catalog:test', godot='4.7', file=None, out=str(tmp_path),
                                  max_mib=1, max_expanded_mib=1, extract=True, sha256=None) | updates))


@pytest.fixture
def resource(monkeypatch):
    value = dict(id='catalog:test', page='https://example.org/pack', license='CC0', price_cent=0,
                 files=[dict(key='1', version='v1', url='https://example.org/free.zip?secret=value',
                             _csrf='do-not-write', _download_key='do-not-write')])
    monkeypatch.setattr(assets, 'info', lambda *args: value)
    return value


def test_download_provenance_and_no_implicit_install(tmp_path, resource):
    data = archive([('models/tree.gltf', '{}'), ('models/tree.bin', 'bytes'), ('LICENSE.txt', 'CC0')])
    result = assets.download(FakeHTTP(data), options(tmp_path))
    manifest = Path(result['manifest']).read_text()
    record = json.loads(manifest)
    assert 'secret=value' not in manifest and 'do-not-write' not in manifest
    assert record['license_files'] == ['LICENSE.txt']
    assert record['selected_file'] == '1' and record['version'] == 'v1'
    assert (Path(result['directory']) / 'files/models/tree.bin').read_text() == 'bytes'
    assert not (tmp_path / 'project.godot').exists()
    with pytest.raises(assets.AssetError, match='already staged'):
        assets.download(FakeHTTP(data), options(tmp_path))
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize('name', ['../escape', '/escape', 'C:/escape', 'a\\escape', 'a/../../escape'])
def test_unsafe_archive_never_publishes_partial_output(tmp_path, resource, name):
    with pytest.raises(assets.AssetError, match='unsafe'):
        assets.download(FakeHTTP(archive([(name, 'bad')])), options(tmp_path))
    assert not list(tmp_path.iterdir())


def test_symlink_and_expansion_limits():
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entries, limit in [([(link, '../../outside')], 100), ([('large', '12345')], 4),
                           ([('Case', '1'), ('case', '2')], 100)]:
        with zipfile.ZipFile(io.BytesIO(archive(entries))) as bundle:
            with pytest.raises(assets.AssetError):
                assets.archive_members(bundle, limit)


def test_checksum_mismatch_and_html_leave_no_output(tmp_path, resource):
    data = archive([('LICENSE', 'MIT')])
    with pytest.raises(assets.AssetError, match='SHA-256'):
        assets.download(FakeHTTP(data), options(tmp_path, sha256='0' * 64))
    with pytest.raises(zipfile.BadZipFile):
        assets.download(FakeHTTP(b'<html>login</html>'), options(tmp_path))
    assert not list(tmp_path.iterdir())


def test_ambiguous_and_paid_downloads_require_no_network(tmp_path, resource):
    http = FakeHTTP(b'')
    resource['files'].append(dict(resource['files'][0], key='2'))
    with pytest.raises(assets.AssetError, match='Multiple'):
        assets.download(http, options(tmp_path))
    resource['price_cent'] = 100
    with pytest.raises(assets.AssetError, match='free'):
        assets.download(http, options(tmp_path, file='1'))
    assert http.downloads == 0


def test_itch_only_uses_zero_price_uploads():
    class Itch:
        calls = []
        def data(self, url):
            if '/download/' in url:
                return '<meta name="csrf_token" value="temporary"><a data-upload_id="123">Free</a>'
            return '<meta name="csrf_token" value="temporary"><script>{"min_price":0}</script>'
        def json(self, url, form):
            self.calls.append((url, form))
            return {'url': 'https://author.itch.io/pack/download/temporary-key'}
    http = Itch()
    result = assets.itch_files(http, 'https://author.itch.io/pack')
    assert [f['key'] for f in result] == ['123']
    assert http.calls[0][1] == {'csrf_token': 'temporary'}
    http.data = lambda url: '<script>{"min_price":900}</script>'
    with pytest.raises(assets.AssetError, match='zero-price'):
        assets.itch_files(http, 'https://author.itch.io/pack')


def test_search_reports_provider_failure_without_hiding_good_results():
    class Failed:
        def json(self, url):
            raise assets.AssetError('offline', 'offline')
    args = SimpleNamespace(query='forest', provider='all', limit=10, page=1, godot='4.7')
    result = assets.search(Failed(), args)
    assert not result['success'] and result['partial']
    assert result['results'] and len(result['errors']) == 2


def test_store_passes_compatibility_and_keeps_release_identity():
    calls = []
    class Store:
        def json(self, url):
            calls.append(url)
            if '/releases/' in url:
                return [dict(id=42, version='v1', download_url='https://example.org/file.zip')]
            return dict(publisher=dict(slug='author', name='Author'), slug='pack', name='Pack',
                        store_url='https://example.org/pack', license_type='MIT', price_cent=0)
    result = assets.info(Store(), 'store:author/pack', '4.7')
    assert 'compatibility=4.7' in calls[1] and 'stable_only=true' in calls[1]
    assert result['files'][0]['key'] == '42'


def test_catalog_and_cli_errors(capsys):
    entries = assets.catalog()
    assert len({a['id'] for a in entries}) == len(entries)
    assert assets.main(['search', '森林', '--provider', 'catalog']) == 0
    assert json.loads(capsys.readouterr().out)['results']
    assert assets.main(['info', 'store:../../etc/passwd']) == 2
    assert json.loads(capsys.readouterr().out)['error'] == 'invalid_id'


def test_streaming_download_limit_and_deadline(tmp_path):
    http = assets.HTTP()
    class Response(io.BytesIO):
        headers = {}
        def geturl(self):
            return 'https://example.org/file.zip?token=private'
    http.open = lambda *args: Response(b'123456')
    with pytest.raises(assets.AssetError, match='size limit'):
        http.download('https://example.org/file.zip', tmp_path / 'partial', 5)
    http.expires = 0
    with pytest.raises(assets.AssetError, match='deadline'):
        http.remaining()


def test_cancelled_or_failed_transfer_is_cleaned_up(tmp_path, resource):
    class Interrupted(FakeHTTP):
        def download(self, url, path, limit):
            path.write_bytes(b'partial')
            raise TimeoutError()
    with pytest.raises(TimeoutError):
        assets.download(Interrupted(b''), options(tmp_path))
    assert not list(tmp_path.iterdir())


def test_assetlib_normalizes_patch_versions_and_rejects_other_major():
    class Library:
        version = '4.7.0'
        def json(self, url):
            return dict(asset_id='1', title='Fixture', author='Author', cost='MIT',
                        godot_version=self.version, version='2', version_string='v2',
                        download_url='https://example.org/free.zip', download_commit='abc')
    http = Library()
    assert assets.info(http, 'assetlib:1', '4.7')['files'][0]['source_revision'] == 'abc'
    http.version = '3.6'
    with pytest.raises(assets.AssetError, match='different/newer'):
        assets.info(http, 'assetlib:1', '4.7')


def test_skill_loader_discovers_cli_catalog_and_reference():
    from agentevolver.skill.context import SkillContextManager
    manager = SkillContextManager.model_construct()
    config = manager._parse_skill_dir(SCRIPT.parents[1])
    assert str(SCRIPT) in config.scripts
    assert str(SCRIPT.parents[1] / 'resources/catalog.json') in config.resources
    assert str(SCRIPT.parents[1] / 'references/assets-cli.md') in config.references
    assert config.version == '1.2.0'
