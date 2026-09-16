"""Owner-scoped UI state. No asset paths, content or credentials are persisted."""
import json
import math
import sqlite3
from flask import jsonify, request
from ipaper.database.connection import get_db
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import current_user_id


def _read(key, default):
    row = get_db().execute('SELECT value FROM user_settings_v2 WHERE owner_id=? AND key=?',
                           (current_user_id(), key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except (ValueError, TypeError):
        return default


def _write(key, value):
    _write_many({key: value})


def _write_many(values):
    db = get_db()
    try:
        for key, value in values.items():
            db.execute('INSERT INTO user_settings_v2(owner_id,key,value) VALUES(?,?,?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value',
                       (current_user_id(), key, json.dumps(value, ensure_ascii=False)))
        db.commit()
    except Exception:
        db.rollback()
        raise


def _number(value, minimum, maximum):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and minimum <= value <= maximum


def register_workspace_state(app):
    def body():
        request.max_content_length = 65536
        if request.content_length and request.content_length > 65536:
            raise ValueError()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError()
        return data

    @app.route('/api/workspace/state', methods=['GET', 'PUT'])
    def workspace_state():
        try:
            if request.method == 'GET':
                data = _read('workspace_v1', {})
                data.update(_read('topic_navigation_v1', {}))
                navigation = _read('reading_navigation_v1', {})
                if navigation.get('navigationPanel') in ('outline', 'thumbnails', 'bookmarks'):
                    data['navigationPanel'] = navigation['navigationPanel']
                data['tabs'] = [p for p in data.get('tabs', []) if PaperDAO.get_paper(p)]
                if 'tabDocuments' in data:
                    data['tabDocuments'] = {k:v for k,v in data['tabDocuments'].items() if k in data['tabs']}
                if data.get('activePaper') not in data['tabs']:
                    data['activePaper'] = None
                return jsonify(data)
            data = body()
            if set(data) - {'tabs', 'activePaper', 'theme', 'categoryWidth', 'detailWidth', 'chatWidth', 'thumbnailOpen', 'navigationPanel', 'taskRefs', 'tabDocuments', 'readerResults', 'topicFilter', 'topicCollapsed'}:
                raise ValueError()
            topic_filter = data.get('topicFilter', 'all')
            collapsed = data.get('topicCollapsed', [])
            if not isinstance(topic_filter, str) or len(topic_filter) > 4096 or not (topic_filter in ('all', 'unorganized') or topic_filter.startswith('topic:')):
                raise ValueError()
            if not isinstance(collapsed,list) or len(collapsed)>1000 or any(not isinstance(i,str) or len(i)>64 for i in collapsed):
                raise ValueError()
            if topic_filter.startswith('topic:'):
                ids=topic_filter[6:].split(',')
                if len(ids)>64 or any(not i or len(i)>64 for i in ids):
                    raise ValueError()
                from ipaper.topics.store import nodes, resolve
                tree=nodes(get_db(),current_user_id())
                # Deleted/merged IDs may be retained for the UI to reconcile.
                if any(i not in tree for i in ids):
                    return jsonify(error='topic_not_found'),404
            if collapsed:
                from ipaper.topics.store import nodes
                if any(i not in nodes(get_db(),current_user_id()) for i in collapsed):
                    return jsonify(error='topic_not_found'),404
            tabs = data.get('tabs', [])
            if not isinstance(tabs, list) or len(tabs) > 20 or any(not isinstance(p, str) or len(p) > 200 for p in tabs) or len(set(tabs)) != len(tabs):
                raise ValueError()
            if any(not PaperDAO.get_paper(p) for p in tabs):
                return jsonify(error='paper_not_found'), 404
            documents = data.get('tabDocuments', {})
            if not isinstance(documents, dict) or any(k not in tabs or v not in ('original', 'translated') for k, v in documents.items()):
                raise ValueError()
            if data.get('activePaper') is not None and data['activePaper'] not in tabs:
                raise ValueError()
            if 'theme' in data and data['theme'] not in ('light', 'dark', 'system'):
                raise ValueError()
            for key, low, high in [('categoryWidth', 180, 420), ('detailWidth', 280, 560), ('chatWidth', 300, 640)]:
                if key in data and not _number(data[key], low, high):
                    raise ValueError()
            if 'thumbnailOpen' in data and not isinstance(data['thumbnailOpen'], bool):
                raise ValueError()
            if 'navigationPanel' in data and data['navigationPanel'] not in ('outline','thumbnails','bookmarks'):
                raise ValueError()
            reader_results = data.get('readerResults', {})
            if not isinstance(reader_results, dict) or len(reader_results) > 20:
                raise ValueError()
            for paper, value in reader_results.items():
                if not isinstance(value, dict) or (set(value) - {'mode','resultId','structureResultId','layoutResultId'} or not {'mode','resultId'}.issubset(value)) or value['mode'] not in ('original','translated','structure'):
                    raise ValueError()
                if not PaperDAO.get_paper(paper):
                    return jsonify(error='paper_not_found'), 404
                for field in ('resultId','structureResultId','layoutResultId'):
                    if not isinstance(value.get(field,''),str):
                        raise ValueError()
                    if not value.get(field):
                        continue
                    from ipaper.processing.store import ProcessingStore
                    from ipaper.processing.common import ProcessingError
                    from ipaper.database.connection import DB_PATH
                    try:
                        result = ProcessingStore(DB_PATH, '.', current_user_id()).result(value[field])
                        if field == 'structureResultId' and result['kind'] not in ('structure','structured_translation') or field == 'layoutResultId' and result['kind'] not in ('babeldoc_mono','babeldoc_dual'):
                            raise ValueError()
                        if result['paper_id'] != paper:
                            raise ValueError()
                    except ProcessingError:
                        raise ValueError()
            tasks = data.get('taskRefs', [])
            if not isinstance(tasks, list) or len(tasks) > 50:
                raise ValueError()
            for task in tasks:
                if not isinstance(task, dict) or set(task) != {'id', 'kind', 'label'} or task['kind'] not in ('upload', 'import', 'export', 'analysis') or any(not isinstance(task[k], str) or len(task[k]) > 250 for k in ('id', 'label')):
                    raise ValueError()
            # Keep the legacy envelope readable/writable by 1.3 during rollback.
            legacy = {k: v for k, v in data.items() if k not in ('navigationPanel','topicFilter','topicCollapsed')}
            values = {'workspace_v1': legacy}
            if 'navigationPanel' in data:
                values['reading_navigation_v1'] = {'navigationPanel': data['navigationPanel']}
            if 'topicFilter' in data or 'topicCollapsed' in data:
                values['topic_navigation_v1'] = {'topicFilter': topic_filter, 'topicCollapsed': collapsed}
            _write_many(values)
            return jsonify(data)
        except ValueError:
            return jsonify(error='invalid_workspace_state'), 400
        except sqlite3.Error:
            return jsonify(error='state_save_failed'), 503

    @app.route('/api/paper/<paper_id>/reading-position', methods=['GET', 'PUT'])
    def reading_position(paper_id):
        if not PaperDAO.get_paper(paper_id):
            return jsonify(error='paper_not_found'), 404
        key = 'reading_position_v1:' + paper_id
        try:
            if request.method == 'GET':
                return jsonify(_read(key, {}))
            data = body()
            if set(data) - {'document', 'page', 'offset', 'zoom', 'rotation', 'fingerprint', 'sessionId'}:
                raise ValueError()
            document = data.get('document')
            if document not in ('original', 'translated'):
                raise ValueError()
            if not _number(data.get('page'), 1, 100000) or int(data['page']) != data['page']:
                raise ValueError()
            if not _number(data.get('offset'), 0, 1) or not _number(data.get('rotation'), 0, 270) or data.get('rotation') not in (0, 90, 180, 270):
                raise ValueError()
            zoom = data.get('zoom')
            if not ((isinstance(zoom, str) and zoom in ('width', 'page')) or _number(zoom, .25, 4)):
                raise ValueError()
            if not isinstance(data.get('fingerprint'), str) or not 1 <= len(data['fingerprint']) <= 128:
                raise ValueError()
            if 'sessionId' in data:
                sid = data['sessionId']
                if sid is not None:
                    if not isinstance(sid, str) or not 1 <= len(sid) <= 200:
                        raise ValueError()
                    row = get_db().execute('SELECT 1 FROM chats WHERE owner_id=? AND paper_id=? AND session_id=?', (current_user_id(), paper_id, sid)).fetchone()
                    if not row:
                        raise ValueError()
            # Read-modify-write is serialized, preserving the other document variant.
            db = get_db()
            try:
                db.execute('BEGIN IMMEDIATE')
                positions = _read(key, {})
                positions[document] = data
                _write(key, positions)
            except Exception:
                db.rollback()
                raise
            return jsonify(data)
        except (ValueError, TypeError):
            return jsonify(error='invalid_reading_position'), 400
        except sqlite3.Error:
            return jsonify(error='state_save_failed'), 503
