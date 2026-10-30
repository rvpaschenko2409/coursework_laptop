"""Check calculation snapshots, historical replay and atomic PostgreSQL writes."""
import importlib.util
import os
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import pytest

pytest.importorskip('psycopg')
from decision_store.calculations import compute, replay_result, run_calculation
from scripts.seed_demo import load_payload, seed

ROOT=Path(__file__).resolve().parents[1]
FEATURES=['cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_gb','storage_type']


@pytest.fixture
def snapshot():
    payload=load_payload(ROOT)
    criteria=[dict(row,id=index) for index,row in enumerate(payload['criteria'],1)]
    alternatives=[dict(row,id=index) for index,row in enumerate(payload['alternatives'],1)]
    criterion_ids={row['code']:row['id'] for row in criteria}
    alternative_ids={row['code']:row['id'] for row in alternatives}
    scores=[dict(row,id=index,alternative_id=alternative_ids[row['alternative_code']],
                 criterion_id=criterion_ids[row['criterion_code']],prediction_id=None,override_reason=None)
            for index,row in enumerate(payload['scores'],1)]
    reference=pd.read_csv(ROOT/'results/decision/alternatives_with_predictions.csv').set_index('alternative_id')
    predictions=[]
    for alternative in alternatives:
        value=float(reference.loc[alternative['code'],'performance_score'])
        prediction_id=100+alternative['id']
        predictions.append(dict(id=prediction_id,model_id=1,alternative_id=alternative['id'],
            criterion_id=criterion_ids['performance_score'],value=value,
            features_snapshot={name:alternative['features'][name] for name in FEATURES}))
        scores.append(dict(id=200+alternative['id'],alternative_id=alternative['id'],criterion_id=criterion_ids['performance_score'],
            value=value,source='ml',prediction_id=prediction_id,override_reason=None))
    return dict(schema_version=1,project={'id':1,'budget':100000.0,'revision':1},criteria=criteria,
                alternatives=alternatives,scores=scores,predictions=predictions,
                models=[{'id':1,'dataset_id':1,'is_active':True,'feature_names':FEATURES}],
                datasets=[{'id':1,'origin':'synthetic'}],parameters={'weight_method':'merec','v':0.5})


def test_matches_existing_control_results(snapshot):
    output=compute(snapshot)
    for method in ('marcos','vikor'):
        expected=pd.read_csv(ROOT/f'results/decision/{method}_ranking.csv',index_col=0)
        actual=pd.DataFrame(output[method]['ranking']).set_index('alternative_code')
        assert list(actual.index)==list(expected.index)
        np.testing.assert_allclose(actual[expected.columns].to_numpy(float),expected.to_numpy(float),rtol=0,atol=1e-12)
    assert output['marcos']['ranking'][0]['alternative_code']=='A4'
    assert output['vikor']['conditions']['compromise_set']==['A2','A3','A5']
    expected=pd.read_csv(ROOT/'results/decision/merec_weights.csv').set_index('criterion')
    actual=pd.DataFrame(output['merec']['weights']).set_index('criterion')
    np.testing.assert_allclose(actual.to_numpy(float),expected.to_numpy(float),rtol=0,atol=1e-12)
    assert sum(x['weight'] for x in output['merec']['weights'])==pytest.approx(1,abs=1e-12)


@pytest.mark.parametrize('v',[-0.1,1.1,float('nan'),float('inf'),True])
def test_invalid_v_rejected(snapshot,v):
    snapshot['parameters']['v']=v
    with pytest.raises(ValueError,match='Параметр v'):
        compute(snapshot)


def test_budget_filter_and_missing_excluded_score(snapshot):
    snapshot['project']['budget']=60000
    a4=next(row['id'] for row in snapshot['alternatives'] if row['code']=='A4')
    snapshot['scores']=[row for row in snapshot['scores'] if not (row['alternative_id']==a4 and row['source']=='ml')]
    output=compute(snapshot)
    assert output['excluded_codes']==['A3','A4']
    assert {row['alternative_code'] for row in output['marcos']['ranking']}=={'A1','A2','A5'}


def test_missing_eligible_score_rejected(snapshot):
    snapshot['scores']=[row for row in snapshot['scores'] if row['id']!=201]
    with pytest.raises(ValueError,match='Не заполнена'):
        compute(snapshot)


def test_ml_value_disagreement_rejected(snapshot):
    next(row for row in snapshot['scores'] if row['source']=='ml')['value']+=10
    with pytest.raises(ValueError,match='не совпадает'):
        compute(snapshot)


def test_stale_prediction_features_rejected(snapshot):
    snapshot['alternatives'][0]['features']['ram_gb']=64
    with pytest.raises(ValueError,match='прогноз устарел'):
        compute(snapshot)


def test_changed_direction_rejected(snapshot):
    snapshot['criteria'][0]['direction']='max'
    with pytest.raises(ValueError,match='Направления'):
        compute(snapshot)


def test_tampered_snapshot_matrix_rejected(snapshot):
    snapshot['matrix']={'rows':['changed'],'columns':[],'values':[]}
    with pytest.raises(ValueError,match='Матрица снимка'):
        compute(snapshot)


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'),reason='Needs isolated PostgreSQL test connection')
def test_database_run_replay_change_and_rollback(monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from scripts.register_ml import load_bundle,register
    url=os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    schema='calculation_test_'+uuid.uuid4().hex
    bundle=load_bundle(ROOT)
    with psycopg.connect(url,autocommit=True,row_factory=dict_row) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection.execute((ROOT/'db_migrations/versions/0001_initial.sql').read_text(encoding='utf-8'),prepare=False)
            connection.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
            connection.execute("INSERT INTO alembic_version VALUES('0001')")
            with connection.transaction():
                project=seed(connection,load_payload(ROOT))
            spec=importlib.util.spec_from_file_location('migration_for_calculations',ROOT/'db_migrations/versions/0002_model_registration.py')
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            connection.execute(migration.UP_SQL,prepare=False)
            connection.execute("UPDATE alembic_version SET version_num='0002'")
            with connection.transaction():register(connection,bundle)
            project_id=project['project_id']
            with monkeypatch.context() as patch:
                def forbidden(*args,**kwargs):
                    raise AssertionError('Calculation must read PostgreSQL, not CSV')
                patch.setattr(pd,'read_csv',forbidden)
                with connection.transaction():first=run_calculation(connection,project_id)
                with connection.transaction():second=run_calculation(connection,project_id)
            assert first['created'] and not second['created']
            assert first['result_ids']==second['result_ids'] and first['weight_set_id']==second['weight_set_id']
            assert first['marcos_leader']=='A4' and first['vikor_compromise']==['A2','A3','A5']
            assert connection.execute('SELECT count(*) AS n FROM results').fetchone()['n']==2
            assert connection.execute('SELECT count(*) AS n FROM weight_sets').fetchone()['n']==1
            assert connection.execute('SELECT count(*) AS n FROM weights').fetchone()['n']==5
            for result_id in first['result_ids'].values():
                assert replay_result(connection,result_id,project_id)['verified']
            # A current value changes, while the historical snapshot still replays.
            actor=connection.execute('SELECT owner_id FROM projects WHERE id=%s',(project_id,)).fetchone()['owner_id']
            connection.execute("""UPDATE scores SET value=value+1000,expert_id=%s,override_reason='Updated fictional price'
                WHERE id=(SELECT s.id FROM scores s JOIN criteria c ON c.id=s.criterion_id
                JOIN alternatives a ON a.id=s.alternative_id WHERE c.code='price_rub' AND a.code='A1')""",(actor,))
            old=replay_result(connection,first['result_ids']['marcos'],project_id)
            assert old['verified'] and old['is_stale'] and old['ranking'][0]=='A4'
            with connection.transaction():third=run_calculation(connection,project_id)
            assert third['created'] and third['result_ids']!=first['result_ids']
            assert connection.execute('SELECT count(*) AS n FROM results').fetchone()['n']==4
            # Failure during the second INSERT rolls back the first result and weights too.
            connection.execute("""CREATE FUNCTION fail_vikor_insert() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.method='vikor' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END $$;
                CREATE TRIGGER test_fail BEFORE INSERT ON results FOR EACH ROW EXECUTE FUNCTION fail_vikor_insert();""",prepare=False)
            with pytest.raises(psycopg.Error):
                with connection.transaction():run_calculation(connection,project_id,v=0.7)
            assert connection.execute('SELECT count(*) AS n FROM results').fetchone()['n']==4
            assert connection.execute('SELECT count(*) AS n FROM weight_sets').fetchone()['n']==2
            connection.execute('DROP TRIGGER test_fail ON results')
            connection.execute('UPDATE projects SET budget=40000 WHERE id=%s',(project_id,))
            with pytest.raises(ValueError,match='меньше двух'):
                with connection.transaction():run_calculation(connection,project_id)
            assert connection.execute('SELECT count(*) AS n FROM results').fetchone()['n']==4
            with monkeypatch.context() as patch:
                patch.setattr('decision_store.calculations.core_version',lambda:'different')
                with pytest.raises(ValueError,match='Версия ядра'):
                    replay_result(connection,first['result_ids']['marcos'],project_id)
        finally:
            connection.execute('SET search_path TO public')
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
