"""Validate persisted ML provenance and atomic registration with real SQL."""
import copy
import importlib.util
import os
import json
import shutil
from pathlib import Path
import uuid

import pandas as pd
import pytest

pytest.importorskip('psycopg')
from scripts.register_ml import load_bundle, register, validate_split
from scripts.seed_demo import load_payload, seed

ROOT=Path(__file__).resolve().parents[1]


def migration_module():
    spec=importlib.util.spec_from_file_location('registry_migration',ROOT/'db_migrations/versions/0002_model_registration.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def bundle():
    return load_bundle(ROOT)


def test_existing_models_and_origin(bundle):
    assert len(bundle['rows'])==4000 and len(bundle['groups'])==100
    assert len(bundle['predictions'])==5 and len(bundle['models'])==2
    assert bundle['selected']=='random_forest'
    assert bundle['models']['random_forest']['metrics']['test_mae']==pytest.approx(40.1206,abs=0.001)
    assert bundle['predictions']['A4']==pytest.approx(1718.2286348085,abs=1e-8)
    assert bundle['environment']['training_timestamp_known'] is False
    assert len(bundle['manifest']['cv_folds'])==5
    assert all(set(f['fit_configuration_ids']).isdisjoint(f['validation_configuration_ids']) for f in bundle['manifest']['cv_folds'])


def test_split_group_leakage_rejected():
    data=pd.read_csv(ROOT/'data/synthetic/laptops_synthetic.csv')
    train=pd.read_csv(ROOT/'data/processed/train.csv')
    test=pd.read_csv(ROOT/'data/processed/test.csv')
    test.loc[0,'configuration_id']=train.loc[0,'configuration_id']
    with pytest.raises(ValueError):
        validate_split(data,train,test)


def test_changed_target_in_split_rejected():
    data=pd.read_csv(ROOT/'data/synthetic/laptops_synthetic.csv')
    train=pd.read_csv(ROOT/'data/processed/train.csv')
    test=pd.read_csv(ROOT/'data/processed/test.csv')
    test.loc[0,'synthetic_score']+=1
    with pytest.raises(ValueError):
        validate_split(data,train,test)


def test_artifact_mismatch_rejected_before_deserialization(tmp_path,monkeypatch):
    for relative in ('models','results','scripts','data/synthetic','data/processed','data/decision'):
        target=tmp_path/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(ROOT/relative,target)
    (tmp_path/'data/demo').mkdir()
    shutil.copy2(ROOT/'data/demo/project.json',tmp_path/'data/demo/project.json')
    reference=json.loads((ROOT/'data/demo/ml_reference.json').read_text(encoding='utf-8'))
    reference['artifacts']['ridge']='0'*64
    (tmp_path/'data/demo/ml_reference.json').write_text(json.dumps(reference),encoding='utf-8')
    def forbidden(*args,**kwargs):
        raise AssertionError('Artifact must be rejected before loading')
    monkeypatch.setattr('scripts.register_ml.joblib.load',forbidden)
    with pytest.raises(ValueError,match='контрольная сумма'):
        load_bundle(tmp_path)


def test_migration_is_successor_of_0001():
    migration=migration_module()
    assert migration.down_revision=='0001' and migration.revision=='0002'


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'),reason='Needs isolated PostgreSQL test connection')
def test_registration_repeat_preservation_and_rollback(bundle):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    url=os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    schema='registry_test_'+uuid.uuid4().hex
    with psycopg.connect(url,autocommit=True,row_factory=dict_row) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection.execute((ROOT/'db_migrations/versions/0001_initial.sql').read_text(encoding='utf-8'),prepare=False)
            connection.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
            connection.execute("INSERT INTO alembic_version VALUES('0001')")
            with connection.transaction():
                project=seed(connection,load_payload(ROOT))
            migration=migration_module()
            connection.execute(migration.UP_SQL,prepare=False)
            connection.execute("UPDATE alembic_version SET version_num='0002'")
            # Empty-table round-trip is supported without inventing training dates.
            connection.execute(migration.DOWN_SQL,prepare=False)
            connection.execute(migration.UP_SQL,prepare=False)
            with connection.transaction():
                first=register(connection,bundle)
            assert first['created'] and first['project_id']==project['project_id'] and first['scores']==25
            assert connection.execute('SELECT count(*) AS n FROM ml_models WHERE trained_at IS NULL AND registered_at IS NOT NULL').fetchone()['n']==2
            assert connection.execute("SELECT count(*) AS n FROM dataset_rows WHERE layer='raw'").fetchone()['n']==2000
            assert connection.execute("SELECT count(*) AS n FROM dataset_rows WHERE layer='processed'").fetchone()['n']==2000
            with connection.transaction():
                second=register(connection,bundle)
            assert not second['created'] and second['dataset_id']==first['dataset_id']
            assert connection.execute("SELECT count(*) AS n FROM audit_events WHERE action='register_ml'").fetchone()['n']==1
            changed=copy.deepcopy(bundle)
            changed['fingerprint']='0'*64
            with pytest.raises(ValueError,match='Файлы ML'):
                with connection.transaction():register(connection,changed)
            # A manual correction must survive a re-run.
            score=connection.execute("SELECT id FROM scores WHERE source='ml' ORDER BY id LIMIT 1").fetchone()['id']
            actor=connection.execute('SELECT owner_id FROM projects WHERE id=%s',(first['project_id'],)).fetchone()['owner_id']
            connection.execute("UPDATE scores SET value=value+1,source='manual',expert_id=%s,override_reason='User correction' WHERE id=%s",(actor,score))
            with pytest.raises(ValueError,match='ручные изменения'):
                with connection.transaction():register(connection,bundle)
            assert connection.execute('SELECT source::text AS source FROM scores WHERE id=%s',(score,)).fetchone()['source']=='manual'
            assert connection.execute('SELECT count(*) AS n FROM score_history').fetchone()['n']==1
            with pytest.raises(psycopg.Error):
                with connection.transaction():connection.execute(migration.DOWN_SQL,prepare=False)
            # A fresh project and deliberately invalid batch must roll back the full import.
            connection.execute('DELETE FROM projects WHERE id=%s',(first['project_id'],))
            connection.execute("UPDATE alembic_version SET version_num='0001'")
            with connection.transaction():seed(connection,load_payload(ROOT))
            connection.execute("UPDATE alembic_version SET version_num='0002'")
            broken=copy.deepcopy(bundle)
            broken['groups'][0]['split']='unknown'
            with pytest.raises(psycopg.Error):
                with connection.transaction():register(connection,broken)
            assert connection.execute('SELECT count(*) AS n FROM datasets').fetchone()['n']==0
            assert connection.execute('SELECT count(*) AS n FROM ml_models').fetchone()['n']==0
            assert connection.execute('SELECT count(*) AS n FROM predictions').fetchone()['n']==0
            assert connection.execute('SELECT count(*) AS n FROM scores').fetchone()['n']==20
        finally:
            connection.execute('SET search_path TO public')
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
