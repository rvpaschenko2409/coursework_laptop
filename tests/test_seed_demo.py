"""Validate demo sources and opt-in atomic/idempotent PostgreSQL import."""
import copy
import os
from pathlib import Path
import shutil
import uuid

import pytest

pytest.importorskip('psycopg')
from scripts.seed_demo import canonical, fingerprint, load_payload, read_project, seed

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo_root(tmp_path):
    for relative in ('data/demo/project.json', 'data/decision/alternatives.csv', 'data/decision/dematel_direct.csv'):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def test_demo_provenance_and_pending_prediction():
    data = load_payload(ROOT)
    assert len(data['criteria']) == 5 and len(data['alternatives']) == 5
    assert len(data['scores']) == 20 and len(data['comparisons']) == 25
    assert len(data['stakeholders']) == 4
    assert all(x['criterion_code'] != 'performance_score' for x in data['scores'])
    assert all(x['features']['data_origin'] == 'synthetic' for x in data['alternatives'])
    assert next(x for x in data['criteria'] if x['code'] == 'performance_score')['source'] == 'ml'


def test_line_endings_do_not_change_fingerprint(demo_root):
    before = fingerprint(load_payload(demo_root))
    for path in demo_root.rglob('*'):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert fingerprint(load_payload(demo_root)) == before


@pytest.mark.parametrize('old,new', [('35000','0'), ('35000','NaN'), ('35000','Infinity'), ('35000','-1'), ('synthetic','real')])
def test_bad_alternative_rejected(demo_root, old, new):
    path = demo_root / 'data/decision/alternatives.csv'
    path.write_text(path.read_text(encoding='utf-8-sig').replace(old,new,1),encoding='utf-8')
    with pytest.raises(ValueError):
        load_payload(demo_root)


def test_duplicate_alternative_rejected(demo_root):
    path = demo_root / 'data/decision/alternatives.csv'
    path.write_text(path.read_text(encoding='utf-8-sig').replace('A2,','A1,',1),encoding='utf-8')
    with pytest.raises(ValueError):
        load_payload(demo_root)


def test_dematel_nonzero_diagonal_rejected(demo_root):
    path = demo_root / 'data/decision/dematel_direct.csv'
    path.write_text(path.read_text(encoding='utf-8-sig').replace('price,0,0,0,0,0','price,1,0,0,0,0'),encoding='utf-8')
    with pytest.raises(ValueError):
        load_payload(demo_root)


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'),reason='Needs isolated PostgreSQL test connection')
def test_demo_transaction_and_repeat():
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    schema = 'demo_test_' + uuid.uuid4().hex
    url = os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    with psycopg.connect(url,autocommit=True,row_factory=dict_row) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection.execute((ROOT/'db_migrations/versions/0001_initial.sql').read_text(encoding='utf-8'),prepare=False)
            connection.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
            connection.execute("INSERT INTO alembic_version VALUES('0001')")
            payload = load_payload(ROOT)
            with connection.transaction():
                first = seed(connection,payload)
            assert first['created']
            with connection.transaction():
                second = seed(connection,payload)
            assert second['project_id'] == first['project_id'] and not second['created']
            assert connection.execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == 1
            assert connection.execute('SELECT count(*) AS n FROM users').fetchone()['n'] == 3
            assert connection.execute('SELECT count(*) AS n FROM audit_events').fetchone()['n'] == 1
            assert connection.execute('SELECT count(*) AS n FROM predictions').fetchone()['n'] == 0
            assert fingerprint(read_project(connection,first['project_id'])) == fingerprint(payload)
            changed = copy.deepcopy(payload)
            changed['budget'] = 99999.0
            with pytest.raises(ValueError,match='Исходные файлы'):
                with connection.transaction():
                    seed(connection,changed)
            # Changes made after import are retained rather than silently overwritten.
            connection.execute('UPDATE projects SET budget=90000 WHERE id=%s',(first['project_id'],))
            with pytest.raises(ValueError,match='изменён в БД'):
                with connection.transaction():
                    seed(connection,payload)
            assert connection.execute('SELECT budget FROM projects').fetchone()['budget'] == 90000
            # A deliberately failing import must not leave users/project/criteria behind.
            broken = canonical(copy.deepcopy(payload))
            broken['key'] += '-rollback-test'
            broken['comparisons'][0]['value'] = 9
            with pytest.raises(psycopg.Error):
                with connection.transaction():
                    seed(connection,broken)
            assert connection.execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == 1
        finally:
            connection.execute('SET search_path TO public')
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
