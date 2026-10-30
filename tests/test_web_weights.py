"""Weights: normalization, endpoint parity, identity, provenance and atomic history."""
import copy
import pytest
pytest.importorskip('fastapi')
from test_database_calculations import snapshot
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst,edit
from test_web_dematel import calculate,source_id,revision
from decision_store.calculations import matrix_from_snapshot,table,compute,digest,assert_close
from web_app.weight_math import manual_weights,alpha_value,compute_weighted,VERSION,dematel_weights

ITEMS=[{'id':91,'name':'Цена'},{'id':7,'name':'Производительность'},{'id':120,'name':'Масса'}]


@pytest.mark.parametrize('bad',['','NaN','Infinity','-1','abc','1e999999','1000000001'])
def test_invalid_manual_number(bad):
    with pytest.raises(ValueError):manual_weights({'91':bad,'7':'1','120':'2'},ITEMS)


def test_id_alignment_normalization_and_decimal_comma():
    result=manual_weights({'120':'2,5','7':'5','91':'2.5'},ITEMS)
    assert result=={'91':.25,'7':.5,'120':.25}
    assert manual_weights({'91':'0','7':'2','120':'0'},ITEMS)=={'91':0,'7':1,'120':0}
    for values in [{'91':'1','7':'1'},{'91':'0','7':'0','120':'0'},{'1':'1','2':'1','3':'1'}]:
        with pytest.raises(ValueError):manual_weights(values,ITEMS)


@pytest.mark.parametrize('raw',['','NaN','Infinity','-0.1','1.01','0.12345678'])
def test_alpha_rejected(raw):
    with pytest.raises(ValueError):alpha_value(raw)


def prepare(snapshot,method='manual',**options):
    snapshot=copy.deepcopy(snapshot);snapshot['matrix']=table(matrix_from_snapshot(snapshot)[0])
    snapshot['parameters']['weight_method']=method
    selection={'version':VERSION,'method':method,'reason':'Контрольный эксперимент',
        'raw_manual':{str(c['id']):str(i+1) for i,c in enumerate(snapshot['criteria'])},**options}
    snapshot['weight_selection']=selection
    if method=='combined':snapshot['parameters']['alpha']=alpha_value(selection['alpha'])
    return snapshot


def test_manual_exact_matches_existing_methods(snapshot):
    import pandas as pd
    from decision_core.marcos import calculate_marcos
    from decision_core.vikor import calculate_vikor
    from decision_store.calculations import ranking
    s=prepare(snapshot);result=compute_weighted(s);matrix=matrix_from_snapshot(s)[0]
    weights=pd.Series({c['code']:(i+1)/15 for i,c in enumerate(s['criteria'])})
    assert_close(result['marcos']['ranking'],ranking(calculate_marcos(matrix,weights)))
    expected,conditions=calculate_vikor(matrix,weights,.5)
    assert_close(result['vikor']['ranking'],ranking(expected));assert result['vikor']['conditions']==conditions


@pytest.mark.parametrize('alpha',['0','0.5','1'])
def test_combination_endpoints_and_midpoint(snapshot,alpha):
    s=prepare(snapshot,'combined',subjective_source='manual',alpha=alpha)
    result=compute_weighted(s);base=compute(snapshot)
    objective={r['criterion']:r['weight'] for r in base['merec']['weights']}
    subjective=manual_weights(s['weight_selection']['raw_manual'],s['criteria']);a=float(alpha)
    for r in result['weighting']['weights']:
        assert r['weight']==pytest.approx(a*subjective[str(r['criterion_id'])]+(1-a)*objective[r['criterion']],abs=1e-12)
    if a==0:
        for method in ['marcos','vikor']:assert_close(result[method],base[method])
    if a==1:
        direct=compute_weighted(prepare(snapshot))
        for method in ['marcos','vikor']:assert_close(result[method],direct[method])


def test_manual_works_when_objective_merec_is_undefined(snapshot):
    s=copy.deepcopy(snapshot);s['predictions']=[];s['models']=[];s['datasets']=[]
    for r in s['scores']:r.update(value=100,source='manual',prediction_id=None)
    result=compute_weighted(prepare(s))
    assert result['merec'] is None
    assert all(r['rank']==1 for r in result['marcos']['ranking'])
    with pytest.raises(ValueError,match='MEREC'):compute_weighted(prepare(s,'combined',subjective_source='manual',alpha='1'))


@pytest.fixture(scope='module')
def weights_env(edits):return edits


def post(browser,pid,env,method='manual',source='manual',**extra):
    items=env['admin'].execute('SELECT id FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(pid,)).fetchall()
    fields={'method':method,'v':'.5','revision':str(revision(browser,pid)),'csrf':csrf(browser)}
    if method!='merec':fields['reason']='Учебная проверка важности критериев'
    if method=='manual' or method=='combined' and source=='manual':fields.update({f"weight_{c['id']}":str(i+1) for i,c in enumerate(items)})
    if method=='combined':fields.update(subjective_source=source,alpha='.5')
    return browser.post(f'/api/projects/{pid}/weights',data={**fields,**extra})


def test_manual_save_repeat_real_actor_and_replay(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        result=post(browser,pid,env);assert result.status_code==201,result.text
        data=result.json();rid=data['result_ids']['marcos']
        assert post(browser,pid,env).json()['created'] is False
        replay=browser.get(f'/api/projects/{pid}/calculations/{rid}');assert replay.status_code==200,replay.text
        saved=replay.json();assert saved['verified'] and not saved['is_stale']
        assert saved['results']['marcos']['created_by']==user['id']
        assert saved['weighting']['method']=='manual' and saved['calculated']['merec'] is None
        assert saved['results']['marcos']['params']['weight_set_id']==saved['results']['vikor']['params']['weight_set_id']
        assert browser.get(f'/projects/{pid}/calculations/{rid}').status_code==200
        assert browser.post(f'/api/projects/{pid}/calculations/{rid}/replay',data={'csrf':csrf(browser)}).json()['verified']
        for chart in ['weights','ranks']:
            image=browser.get(f'/projects/{pid}/calculations/{rid}/{chart}.png');assert image.status_code==200 and image.content.startswith(b'\x89PNG')
        download=browser.get(f'/projects/{pid}/calculations/{rid}/snapshot.json').json()
        assert download['input']['weight_selection']['raw_manual'] and download['output']['weighting']['method']=='manual'


def test_dematel_and_combined_saved_provenance_and_fixed_weights(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        r=post(browser,pid,env,method='dematel',dematel_id=str(did));assert r.status_code==201,r.text
        rid=r.json()['result_ids']['marcos']
        saved=browser.get(f'/api/projects/{pid}/calculations/{rid}').json()
        assert [r['weight'] for r in saved['weighting']['weights']]==pytest.approx([.25,.4,.1,.15,.1])
        assert saved['snapshot']['weight_selection']['dematel']['structure_id']==did
        assert saved['snapshot']['weight_selection']['dematel']['snapshot']['experts'][0]['is_demo']
        for source in ['manual','dematel']:
            for alpha in ['0','.5','1']:
                extra={'dematel_id':str(did)} if source=='dematel' else {}
                combined=post(browser,pid,env,method='combined',source=source,alpha=alpha,**extra)
                assert combined.status_code==201,combined.text
                crid=combined.json()['result_ids']['marcos'];data=browser.get(f'/api/projects/{pid}/calculations/{crid}').json()
                assert data['weighting']['alpha']==float(alpha) and data['weight_set']['alpha']==float(alpha)
                assert browser.get(f'/projects/{pid}/calculations/{crid}').status_code==200


def test_merec_choice_preserves_existing_identity(weights_env):
    from test_web_calculations import run
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        legacy=run(browser,pid).json();chosen=post(browser,pid,env,method='merec').json()
        assert chosen['result_ids']==legacy['result_ids'] and chosen['created'] is False
        saved=browser.get(f"/api/projects/{pid}/calculations/{legacy['result_ids']['marcos']}").json()
        assert saved['weight_label']=='MEREC' and 'weight_selection' not in saved['snapshot']


def test_access_csrf_criterion_injection_and_validation(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser,client(env) as expert,client(env) as lpr,client(env) as outsider:
        e=signup(expert);l=signup(lpr);signup(outsider)
        for u,role in [(e,'expert'),(l,'decision_maker')]:browser.post(f'/api/projects/{pid}/members',data={'csrf':csrf(browser),'email':u['email'],'role':role})
        for who in [expert,lpr]:
            assert who.get(f'/projects/{pid}/weights').status_code==200
            assert post(who,pid,env).status_code==403
        assert outsider.post(f'/api/projects/{pid}/weights',data={'csrf':csrf(outsider),'revision':'1','method':'manual'}).status_code==404
        assert post(browser,pid,env,csrf='bad').status_code==403
        assert post(browser,pid,env,weight_999999='2').status_code==422
        assert post(browser,pid,env,alpha='0.5').status_code==422
        assert post(browser,pid,env,reason='').status_code==422
        assert post(browser,pid,env,method='unknown').status_code==422
        assert post(browser,pid,env,method='combined',alpha='NaN').status_code==422
        result=post(browser,pid,env).json();rid=result['result_ids']['marcos']
        assert outsider.get(f'/projects/{pid}/calculations/{rid}').status_code==404
        other=new_project(browser)
        assert browser.get(f'/api/projects/{other}/calculations/{rid}').status_code==404
        assert browser.get(f'/projects/{pid}/weights?method=invalid').status_code==422


def test_stale_dematel_rejected_but_embedded_history_survives(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        result=post(browser,pid,env,method='dematel',dematel_id=str(did)).json();rid=result['result_ids']['marcos']
        original=browser.get(f'/api/projects/{pid}/calculations/{rid}').json();oldrev=revision(browser,pid)
        env['admin'].execute('UPDATE projects SET goal=goal WHERE id=%s',(pid,))
        assert post(browser,pid,env,revision=str(oldrev)).status_code==409
        assert post(browser,pid,env,method='dematel',dematel_id=str(did)).status_code==422
        historical=browser.get(f'/api/projects/{pid}/calculations/{rid}').json()
        assert historical['verified'] and historical['is_stale'] and historical['calculated']==original['calculated']
        # Even removing the source record as administrator cannot replace its embedded snapshot.
        env['admin'].execute('DELETE FROM structure_models WHERE id=%s',(did,))
        assert browser.get(f'/api/projects/{pid}/calculations/{rid}').json()['verified']


def test_dematel_id_scoping_and_criteria_identity(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        other=new_project(browser)
        response=post(browser,other,env,method='dematel',dematel_id=str(did));assert response.status_code==422
        data=browser.get(f'/api/projects/{pid}/dematel/results/{did}').json()
        reference={'snapshot':data['snapshot'],'output':data['output'],'input_fingerprint':data['record']['input']['input_fingerprint']}
        items=copy.deepcopy(data['snapshot']['criteria']);items[0]['id']+=10000
        with pytest.raises(ValueError,match='Критерии'):dematel_weights(reference,items,data['snapshot']['project'])


def test_rollback_after_weight_insert(weights_env,monkeypatch):
    import web_app.weight_store as store
    env=weights_env;browser,user=analyst(env);pid=env['demo_id'];original=store.run
    def broken(*args,**kwargs):original(*args,**kwargs);raise ValueError('Тестовая ошибка после вставки')
    def counts():return tuple(env['admin'].execute(f'SELECT count(*) AS n FROM {t} WHERE project_id=%s',(pid,)).fetchone()['n'] for t in ['weight_sets','weights','results','audit_events'])
    with browser:
        before=counts();monkeypatch.setattr(store,'run',broken)
        assert post(browser,pid,env).status_code==422 and counts()==before


def test_replay_detects_tampered_alpha_or_weight(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    with browser:
        result=post(browser,pid,env,method='combined').json();rid=result['result_ids']['marcos'];wid=result['weight_set_id']
        admin.execute('UPDATE weight_sets SET alpha=.2 WHERE id=%s',(wid,))
        assert browser.get(f'/api/projects/{pid}/calculations/{rid}').status_code==422
        admin.execute('UPDATE weight_sets SET alpha=.5 WHERE id=%s',(wid,))
        assert browser.get(f'/api/projects/{pid}/calculations/{rid}').json()['verified']


def test_html_error_retains_input_and_dematel_matrix_heading(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        items=env['admin'].execute('SELECT id FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(pid,)).fetchall()
        fields={f"weight_{c['id']}":'1' for c in items};fields[f"weight_{items[0]['id']}"]='bad'
        response=browser.post(f'/projects/{pid}/weights',data={'csrf':csrf(browser),'revision':revision(browser,pid),'method':'manual','v':'.5','reason':'Учебная проверка',**fields})
        assert response.status_code==422 and 'value="bad"' in response.text and 'Учебная проверка' in response.text
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        page=browser.get(f'/projects/{pid}/dematel/results/{did}').text
        assert '<th>Ноутбук</th>' not in page and page.count('<th>Критерий</th>')>=4


def test_budget_precedes_objective_weights_and_rankings(weights_env):
    env=weights_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    original=admin.execute('SELECT budget FROM projects WHERE id=%s',(pid,)).fetchone()['budget']
    with browser:
        try:
            admin.execute('UPDATE projects SET budget=60000 WHERE id=%s',(pid,))
            response=post(browser,pid,env,method='combined',alpha='0');assert response.status_code==201,response.text
            data=browser.get(f"/api/projects/{pid}/calculations/{response.json()['result_ids']['marcos']}").json()
            assert data['calculated']['excluded_codes']==['A3','A4']
            base=compute(data['snapshot'])
            assert_close(data['calculated']['merec'],base['merec'])
            for method in ['marcos','vikor']:assert_close(data['calculated'][method],base[method])
            admin.execute('UPDATE projects SET budget=1 WHERE id=%s',(pid,))
            assert post(browser,pid,env).status_code==422
        finally:admin.execute('UPDATE projects SET budget=%s WHERE id=%s',(original,pid))


def test_duplicate_fields_and_corrupt_embedded_dematel_rejected(weights_env):
    from urllib.parse import urlencode
    env=weights_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        response=browser.post(f'/api/projects/{pid}/weights',content=urlencode([
            ('csrf',csrf(browser)),('revision',str(revision(browser,pid))),('method','manual'),('method','dematel')]),
            headers={'content-type':'application/x-www-form-urlencoded'})
        assert response.status_code==422
        did=calculate(browser,pid,[source_id(env)]).json()['structure_id']
        response=post(browser,pid,env,method='dematel',dematel_id=str(did));assert response.status_code==201
        saved=browser.get(f"/api/projects/{pid}/calculations/{response.json()['result_ids']['marcos']}").json()
        snapshot=saved['snapshot'];snapshot['weight_selection']['dematel']['output']['indicators'][0]['weight']=.9
        with pytest.raises(ValueError):compute_weighted(snapshot)
