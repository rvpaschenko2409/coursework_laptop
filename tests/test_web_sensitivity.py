"""Finite scenarios, one-at-a-time semantics, ties and immutable database history."""
import copy
import pytest
pytest.importorskip('fastapi')
from test_database_calculations import snapshot
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst
from test_web_calculations import run as base_run
from test_web_weights import post as weighted_run,prepare
from test_web_dematel import calculate,source_id,revision
from decision_store.calculations import compute as original_compute,matrix_from_snapshot,table,core_version,assert_close
from web_app import weight_math
from web_app.sensitivity_math import compute,step_value,perturb,compare,RULE


def experiment(base,weighted=False,step=10):
    base=copy.deepcopy(base);base['matrix']=table(matrix_from_snapshot(base)[0]);base.setdefault('limitations',[])
    base['producer']={'core_version':core_version()}
    if weighted:base['producer']['weight_adapter_version']=weight_math.version()
    output=weight_math.compute_weighted(base) if weighted else original_compute(base)
    weighting=output.get('weighting') or {'method':'merec','alpha':None,'weights':output['merec']['weights']}
    version=weight_math.version() if weighted else core_version()
    source={'input':base,'weighting':weighting,'outputs':{m:output[m] for m in ('marcos','vikor')},
        'weight_set':{'id':3,'method':weighting['method'],'alpha':weighting['alpha'],'project_revision':base['project']['revision']},
        'records':[{'id':i,'project_id':base['project']['id'],'created_by':1,'status':'succeeded','project_revision':base['project']['revision'],
            'method':m,'core_version':version,'params':{'weight_set_id':3}} for i,m in enumerate(('marcos','vikor'),11)]}
    return {'schema_version':1,'project':base['project'],'source':source,'parameters':{'rule':RULE,'step_percent':step}}


@pytest.mark.parametrize('raw',['','0','-1','25.01','NaN','Infinity','true','0.001','1e999999'])
def test_invalid_step(raw):
    with pytest.raises(ValueError):step_value(raw)


@pytest.mark.parametrize('raw,expected',[('0,01',.01),('10',10),('25',25)])
def test_valid_step_boundaries(raw,expected):assert step_value(raw)==expected


def test_exact_perturbation_and_ratio_preservation():
    original={9:.2,21:.3,100:.5}
    changed=perturb(original,21,.2)
    assert changed==pytest.approx({9:.2/1.06,21:.36/1.06,100:.5/1.06})
    assert changed[9]/changed[100]==pytest.approx(original[9]/original[100])
    assert sum(changed.values())==pytest.approx(1)
    assert original=={9:.2,21:.3,100:.5}
    assert perturb({1:0,2:1},1,.2)=={1:0,2:1}
    assert perturb({1:0,2:1},2,-.2)=={1:0,2:1}


def test_scenario_counts_v_invariance_and_original_control(snapshot):
    source=experiment(snapshot);result=compute(source)
    assert len(result['scenarios'])==32 and result['summary']['weight']['count']==20 and result['summary']['v']['count']==11
    assert len({s['id'] for s in result['scenarios']})==32
    baseline=result['scenarios'][0]
    assert baseline['marcos']['ranking'][0]['alternative_code']=='A4'
    assert baseline['vikor']['conditions']['compromise_set']==['A2','A3','A5']
    for s in result['scenarios']:
        assert sum(r['weight'] for r in s['weights'])==pytest.approx(1)
        if s['kind']=='v':
            assert s['weights']==baseline['weights'] and s['marcos']['ranking']==baseline['marcos']['ranking']
            assert s['marcos']['comparison']['changed_ranks']==0
        if s['kind']=='weight':assert s['v']==.5


def test_each_scenario_uses_the_existing_two_core_functions(snapshot):
    import pandas as pd
    from decision_core.marcos import calculate_marcos
    from decision_core.vikor import calculate_vikor
    from decision_store.calculations import ranking
    source=experiment(snapshot);matrix=matrix_from_snapshot(snapshot)[0];result=compute(source)
    for s in result['scenarios']:
        weights=pd.Series({r['criterion']:r['weight'] for r in s['weights']})
        assert_close(s['marcos']['ranking'],ranking(calculate_marcos(matrix,weights)))
        vikor,conditions=calculate_vikor(matrix,weights,s['v'])
        assert_close(s['vikor']['ranking'],ranking(vikor));assert s['vikor']['conditions']==conditions


def test_non_grid_original_v_is_included(snapshot):
    snapshot['parameters']['v']=.35
    result=compute(experiment(snapshot))
    assert .35 in result['v_grid'] and len(result['scenarios'])==33
    assert next(s for s in result['scenarios'] if s['kind']=='v' and s['v']==.35)['vikor']['comparison']['changed_ranks']==0


def test_manual_zero_weights_and_all_ties(snapshot):
    base=copy.deepcopy(snapshot);base['predictions']=[];base['models']=[];base['datasets']=[]
    for score in base['scores']:score.update(value=100,source='manual',prediction_id=None)
    base=prepare(base,raw_manual={str(c['id']):'1' if i==0 else '0' for i,c in enumerate(base['criteria'])})
    result=compute(experiment(base,weighted=True))
    for s in result['scenarios']:
        assert len(s['marcos']['comparison']['leaders'])==5 and len(s['vikor']['comparison']['leaders'])==5
        assert not s['marcos']['comparison']['leaders_changed'] and not s['vikor']['comparison']['leaders_changed']
        assert all(r['rank']==1 for r in s['vikor']['ranking'])


def test_leader_comparison_respects_ties_and_delta_sign():
    base=[{'alternative_code':'A1','rank':1},{'alternative_code':'A2','rank':1},{'alternative_code':'A3','rank':3}]
    new=[{'alternative_code':'A1','rank':1},{'alternative_code':'A3','rank':2},{'alternative_code':'A2','rank':3}]
    result=compare(new,base)
    assert result['leaders_changed'] and result['leaders']==['A1'] and result['changed_ranks']==2
    assert result['rank_deltas']=={'A1':0,'A2':2,'A3':-1}


def test_source_or_snapshot_tampering_rejected(snapshot):
    s=experiment(snapshot);s['source']['outputs']['marcos']['ranking'][0]['utility']+=.1
    with pytest.raises(ValueError):compute(s)
    s=experiment(snapshot);s['source']['weighting']['weights'][0]['weight']+=.1
    with pytest.raises(ValueError):compute(s)
    s=experiment(snapshot);s['parameters']['rule']='add_percentage_points'
    with pytest.raises(ValueError):compute(s)


@pytest.fixture(scope='module')
def sensitivity_env(edits):return edits


def launch(browser,pid,rid,**extra):
    return browser.post(f'/api/projects/{pid}/calculations/{rid}/sensitivity',data={'csrf':csrf(browser),'revision':str(revision(browser,pid)),'step_percent':'10',**extra})


def test_save_repeat_canonical_source_and_true_author(sensitivity_env):
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        source=base_run(browser,pid).json()['result_ids']
        response=launch(browser,pid,source['marcos']);assert response.status_code==201,response.text
        result=response.json();assert result['created'] and result['scenario_count']==32
        again=launch(browser,pid,source['vikor']);assert again.status_code==200 and again.json()['result_id']==result['result_id']
        rid=result['result_id'];saved=browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json()
        assert saved['verified'] and not saved['is_stale'] and saved['record']['created_by']==user['id']
        assert browser.get(f'/projects/{pid}/sensitivity/{rid}').status_code==200
        assert browser.get(f'/projects/{pid}/sensitivity').status_code==200
        assert browser.get(f'/projects/{pid}/calculations/{source["marcos"]}/sensitivity').status_code==200
        assert browser.post(f'/api/projects/{pid}/sensitivity/{rid}/replay',data={'csrf':csrf(browser)}).json()['verified']
        assert browser.get(f'/api/projects/{pid}/sensitivity').json()['runs'][0]['id']==rid


def test_all_four_weight_methods_and_embedded_dematel(sensitivity_env):
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        for method in ['merec','manual','dematel','combined']:
            extra={'dematel_id':str(did)} if method=='dematel' else {}
            source=weighted_run(browser,pid,env,method=method,**extra).json()['result_ids']['marcos']
            response=launch(browser,pid,source);assert response.status_code==201,response.text
            saved=browser.get(f"/api/projects/{pid}/sensitivity/{response.json()['result_id']}").json()
            assert saved['snapshot']['source']['weighting']['method']==method
            assert saved['output']['scenarios'][0]['marcos']['comparison']['changed_ranks']==0
            assert browser.get(f"/projects/{pid}/sensitivity/{response.json()['result_id']}").status_code==200


def test_roles_csrf_and_other_project_ids(sensitivity_env):
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id']
    with browser,client(env) as expert,client(env) as lpr,client(env) as stranger:
        e=signup(expert);l=signup(lpr);signup(stranger)
        for u,role in [(e,'expert'),(l,'decision_maker')]:browser.post(f'/api/projects/{pid}/members',data={'csrf':csrf(browser),'email':u['email'],'role':role})
        source=base_run(browser,pid).json()['result_ids']['marcos'];rid=launch(browser,pid,source).json()['result_id']
        for who in [expert,lpr]:
            assert launch(who,pid,source).status_code==403
            assert who.get(f'/projects/{pid}/sensitivity/{rid}').status_code==200
            assert who.get(f'/projects/{pid}/calculations/{source}/sensitivity').status_code==200
        assert launch(browser,pid,source,csrf='bad').status_code==403
        assert stranger.get(f'/projects/{pid}/sensitivity/{rid}').status_code==404
        assert stranger.get(f'/projects/{pid}/sensitivity/{rid}/v.png').status_code==404
        other=new_project(browser)
        assert browser.get(f'/api/projects/{other}/sensitivity/{rid}').status_code==404
        assert launch(browser,other,source).status_code==422
        assert browser.get(f'/projects/{other}/calculations/{source}/sensitivity').status_code==404


def test_historical_source_changes_and_source_deletion(sensitivity_env):
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    with browser:
        source=base_run(browser,pid).json()['result_ids'];rid=launch(browser,pid,source['marcos']).json()['result_id']
        saved=browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json();oldrev=revision(browser,pid)
        admin.execute('UPDATE projects SET goal=goal WHERE id=%s',(pid,))
        assert launch(browser,pid,source['marcos'],revision=str(oldrev)).status_code==409
        assert browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json()['is_stale']
        # An explicit new analysis can use an old snapshot and is labelled historical.
        historical=launch(browser,pid,source['marcos'],step_percent='5');assert historical.status_code==201
        second=historical.json()['result_id'];assert browser.get(f'/api/projects/{pid}/sensitivity/{second}').json()['is_stale']
        admin.execute('DELETE FROM results WHERE id IN(%s,%s)',(source['marcos'],source['vikor']))
        replay=browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json()
        assert replay['verified'] and replay['output']==saved['output']


def test_transaction_rollback_preserves_source_and_weights(sensitivity_env,monkeypatch):
    import web_app.sensitivity_store as store
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id'];original=store.run
    def broken(*args,**kwargs):original(*args,**kwargs);raise ValueError('Тестовая ошибка после вставки')
    def counts():return tuple(env['admin'].execute(f'SELECT count(*) AS n FROM {t} WHERE project_id=%s',(pid,)).fetchone()['n'] for t in ['results','weights','weight_sets','audit_events'])
    with browser:
        source=base_run(browser,pid).json()['result_ids']['marcos'];before=counts()
        monkeypatch.setattr(store,'run',broken)
        assert launch(browser,pid,source).status_code==422 and counts()==before


def test_charts_json_selected_scenario_and_private_cache(sensitivity_env):
    from PIL import Image
    from io import BytesIO
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        source=base_run(browser,pid).json()['result_ids']['marcos'];rid=launch(browser,pid,source).json()['result_id']
        for kind,dimensions in [('weights',(1560,910)),('v',(1248,806))]:
            image=browser.get(f'/projects/{pid}/sensitivity/{rid}/{kind}.png')
            assert image.status_code==200 and image.headers['cache-control']=='no-store'
            assert Image.open(BytesIO(image.content)).size==dimensions
        saved=browser.get(f'/projects/{pid}/sensitivity/{rid}/snapshot.json');assert 'attachment' in saved.headers['content-disposition']
        sid=saved.json()['output']['scenarios'][1]['id']
        assert browser.get(f'/projects/{pid}/sensitivity/{rid}?scenario={sid}').status_code==200
        assert browser.get(f'/projects/{pid}/sensitivity/{rid}?scenario=foreign').status_code==404
        assert browser.get(f'/projects/{pid}/sensitivity/{rid}/unknown.png').status_code==404
        assert browser.get(f'/projects/{pid}/sensitivity?page=0').status_code==422


def test_validation_retains_html_and_duplicate_fields_rejected(sensitivity_env):
    from urllib.parse import urlencode
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        source=base_run(browser,pid).json()['result_ids']['marcos']
        bad=browser.post(f'/projects/{pid}/calculations/{source}/sensitivity',data={'csrf':csrf(browser),'revision':revision(browser,pid),'step_percent':'bad'})
        assert bad.status_code==422 and 'value="bad"' in bad.text and 'Анализ не сохранён' in bad.text
        assert launch(browser,pid,source,arbitrary_weight='1').status_code==422
        assert browser.post(f'/api/projects/{pid}/calculations/{source}/sensitivity',content=urlencode([('csrf',csrf(browser)),('step_percent','10'),('step_percent','5')]),headers={'content-type':'application/x-www-form-urlencoded'}).status_code==422


def test_archived_project_rejects_new_analysis_and_output_tampering(sensitivity_env):
    from psycopg.types.json import Jsonb
    env=sensitivity_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    with browser:
        source=base_run(browser,pid).json()['result_ids']['marcos'];rid=launch(browser,pid,source).json()['result_id']
        admin.execute("UPDATE projects SET status='archived' WHERE id=%s",(pid,))
        try:
            assert launch(browser,pid,source,step_percent='5').status_code==422
            assert browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json()['verified']
        finally:admin.execute("UPDATE projects SET status='draft' WHERE id=%s",(pid,))
        original=admin.execute('SELECT output FROM results WHERE id=%s',(rid,)).fetchone()['output']
        changed=copy.deepcopy(original);changed['scenarios'][0]['marcos']['ranking'][0]['utility']+=.1
        import psycopg
        with pytest.raises(psycopg.errors.RaiseException,match='immutable'):
            admin.execute('UPDATE results SET output=%s WHERE id=%s',(Jsonb(changed),rid))
        assert browser.get(f'/api/projects/{pid}/sensitivity/{rid}').json()['verified']
