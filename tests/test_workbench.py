import json
from pathlib import Path

import pytest

from ipaper.database import connection
from ipaper.workbench import build_assets
from tests.workbench_support import make_workbench_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app, first, second = make_workbench_fixture(tmp_path, monkeypatch)
    # Unit tests work before npm build, using a local manifest fixture.
    static = tmp_path / 'static'
    (static / 'workbench/.vite').mkdir(parents=True)
    (static / 'workbench/main.js').write_text('// bundled')
    (static / 'workbench/main.css').write_text('body{}')
    (static / 'workbench/.vite/manifest.json').write_text(json.dumps({
        'src/main.tsx': {'file': 'main.js', 'css': ['main.css']}
    }))
    app.static_folder = str(static)
    yield app, first, second
    connection.close_db()


def login(client, name='reader_one'):
    result = client.post('/api/auth/login', json={'username': name, 'password': 'workbench-test-pass'})
    assert result.status_code == 200
    return result.json['csrf_token']


@pytest.mark.parametrize('obsolete_flag', [False, True])
def test_unified_entry_ignores_retired_flag(fixture, obsolete_flag):
    app, _, _ = fixture
    app.config['IPAPER_WORKBENCH_ENABLED'] = obsolete_flag
    c = app.test_client()
    assert c.get('/workbench').location == '/'
    assert c.get('/workbench/').location == '/'
    assert c.get('/').status_code == 200
    assert 'type="module"' in c.get('/').text
    assert '新版工作台' not in c.get('/').text
    assert c.get('/api/papers/all').status_code == 401


def test_auth_entry_manifest_and_logout(fixture):
    app, _, _ = fixture
    c = app.test_client()
    assert c.get('/workbench').location == '/'
    csrf = login(c)
    page = c.get('/')
    assert page.status_code == 200
    assert 'type="module"' in page.text
    assert '/static/workbench/main.js' in page.text
    assert '/static/workbench/main.css' in page.text
    assert 'app.js' not in page.text
    assert page.headers['Cache-Control'] == 'no-store'
    assert "script-src 'self'" in page.headers['Content-Security-Policy']
    assert c.get('/workbench/?paper=a-0').location == '/?paper=a-0'
    assert '新版工作台' not in c.get('/').text
    assert c.delete('/api/auth/session').status_code == 403
    assert c.delete('/api/auth/session', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert c.get('/workbench').location == '/'
    assert c.get('/api/papers/all').status_code == 401


def test_first_password_change_redirects(fixture):
    app, first, _ = fixture
    with app.app_context():
        connection.get_db().execute('UPDATE users SET must_change_password=1 WHERE id=?', (first['id'],))
        connection.get_db().commit()
    c = app.test_client(); login(c)
    assert c.get('/workbench').location == '/'
    assert c.get('/api/papers/all').status_code == 403


def test_real_list_detail_and_owner_isolation(fixture):
    app, _, _ = fixture
    a, b = app.test_client(), app.test_client()
    login(a); login(b, 'reader_two')
    assert {p['id'] for p in a.get('/api/papers/all').json} == {'a-0', 'a-1', 'a-2'}
    assert {p['id'] for p in b.get('/api/papers/all').json} == {'b-0'}
    assert a.get('/api/paper/a-0').json['has_chinese_version'] is True
    assert a.get('/api/paper/b-0').status_code == 404
    assert b.get('/api/paper/a-0').status_code == 404
    assert a.get('/viewer/a-0').location == '/?paper=a-0&view=reader&document=original'


def test_missing_or_escaping_assets_return_safe_unavailable_page(fixture):
    app, _, _ = fixture
    c = app.test_client(); login(c)
    manifest = Path(app.static_folder) / 'workbench/.vite/manifest.json'
    manifest.write_text(json.dumps({'src/main.tsx': {'file': '../../outside.js'}}))
    assert c.get('/').status_code == 503
    assert '重新加载' in c.get('/').text
    assert '返回旧版' not in c.get('/').text
    manifest.unlink()
    assert c.get('/').status_code == 503
    assert c.get('/api/papers/all').status_code == 200


def test_manifest_includes_imported_css(tmp_path):
    root = tmp_path / 'workbench'; (root / '.vite').mkdir(parents=True)
    for file in ['a.js', 'b.js', 'b.css']:
        (root / file).write_text('/* fixture */')
    (root / '.vite/manifest.json').write_text(json.dumps({
        'src/main.tsx': {'file': 'a.js', 'imports': ['b']},
        'b': {'file': 'b.js', 'css': ['b.css']},
    }))
    assert build_assets(str(tmp_path))['styles'] == ['workbench/b.css']


def test_public_asset_precompression_and_private_boundaries(fixture):
    import gzip
    app,_,_=fixture
    assets=Path(app.static_folder)/'workbench/assets';assets.mkdir()
    name='main-123abcDE.js';content=b'export const x=1; '*200
    (assets/name).write_bytes(content);(assets/(name+'.gz')).write_bytes(gzip.compress(content))
    c=app.test_client();url='/static/workbench/assets/'+name
    raw=c.get(url,headers={'Accept-Encoding':'identity'})
    zipped=c.get(url,headers={'Accept-Encoding':'gzip'})
    assert raw.data==content and 'Content-Encoding' not in raw.headers
    assert gzip.decompress(zipped.data)==content and zipped.headers['Content-Encoding']=='gzip'
    assert 'Accept-Encoding' in zipped.headers['Vary'] and 'immutable' in zipped.headers['Cache-Control']
    assert len(zipped.data)<len(raw.data)//2
    assert c.get(url,headers={'Accept-Encoding':'gzip','If-None-Match':zipped.headers['ETag']}).status_code==304
    refused=c.get('/api/paper/a-0/file',headers={'Accept-Encoding':'gzip'})
    assert refused.status_code==401 and 'Content-Encoding' not in refused.headers
    assert 'no-store' in refused.headers['Cache-Control']
    login(c)
    pdf=c.get('/api/paper/a-0/file',headers={'Accept-Encoding':'gzip','Range':'bytes=0-9'})
    # This legacy-path fixture is rejected by the file confinement guard.
    assert pdf.status_code==409 and 'Content-Encoding' not in pdf.headers
    assert 'no-store' in pdf.headers['Cache-Control']
