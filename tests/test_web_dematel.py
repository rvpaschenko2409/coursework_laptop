"""DEMATEL control, identities, aggregation, convergence and historical preservation."""
import copy
import pytest
pytest.importorskip('fastapi')
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst
from web_app.dematel_store import compute_snapshot,threshold_value,matrix_form,CATWOE

CODES=['price_rub','performance_score','mass_kg','battery_hours','warranty_months']
A=[[0,0,0,0,0],[3,0,2,3,0],[0,0,0,0,0],[0,0,0,0,0],[2,0,0,0,0]]


def snapshot(matrix=A):
    size=len(matrix);items=[{'id':i+1,'code':CODES[i]} for i in range(size)]
    return {'schema_version':1,'criteria':items,'experts':[{'id':99,'cells':[{'criterion_i':i+1,'criterion_j':j+1,'value':matrix[i][j]} for i in range(size) for j in range(size)]}],
        'parameters':{'aggregation':'arithmetic_mean_equal_weights','threshold':None}}


def test_original_control_and_weight_sum():
    result=compute_snapshot(snapshot())
    assert result['divisor']==8 and result['spectral_radius']==0
    assert [r['D'] for r in result['indicators']]==pytest.approx([0,1,0,0,.25])
    assert [r['R'] for r in result['indicators']]==pytest.approx([.625,0,.25,.375,0])
    assert result['total']==result['normalized'] and result['sum_D']==result['sum_R']
    assert sum(r['weight'] for r in result['indicators'])==pytest.approx(1)
    assert [r['group'] for r in result['indicators']]==['effect','cause','effect','effect','cause']


@pytest.mark.parametrize('matrix',[[[0,0],[0,0]],[[0,4],[4,0]],[[0,1,1],[1,0,1],[1,1,0]],[[1,0],[0,0]],[[0,5],[0,0]],[[0,.5],[0,0]]])
def test_bad_or_nonconvergent_matrix(matrix):
    with pytest.raises(ValueError):compute_snapshot(snapshot(matrix))


@pytest.mark.parametrize('raw',['-1','NaN','Infinity','a','1e15'])
def test_invalid_graph_threshold(raw):
    with pytest.raises(ValueError):threshold_value(raw)


def test_average_and_threshold_change_only_edges():
    s=snapshot();other=copy.deepcopy(s['experts'][0]);other['id']=100
    other['cells'][5]['value']=1;s['experts'].append(other)
    result=compute_snapshot(s);assert result['direct']['values'][1][0]==2
    s['parameters']['threshold']=1e9;high=compute_snapshot(s)
    assert high['edges']==[] and high['indicators']==result['indicators']
    assert threshold_value('0,1')==.1 and threshold_value('') is None


@pytest.fixture(scope='module')
def dematel(edits):return edits


def revision(browser,pid):return browser.get(f'/api/projects/{pid}').json()['project']['revision']


def source_id(env):
    return env['admin'].execute('SELECT DISTINCT expert_id FROM comparisons WHERE project_id=%s',(env['demo_id'],)).fetchone()['expert_id']


def calculate(browser,pid,sources,**extra):
    payload={'revision':str(revision(browser,pid)),'csrf':csrf(browser),'expert_ids':[str(i) for i in sources],'threshold':''}
    return browser.post(f'/api/projects/{pid}/dematel',data={**payload,**extra})


def own_matrix(browser,pid,env,matrix=A,**extra):
    items=env['admin'].execute('SELECT id FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(pid,)).fetchall()
    fields={f'cell_{i["id"]}_{j["id"]}':str(matrix[a][b]) for a,i in enumerate(items) for b,j in enumerate(items)}
    return browser.post(f'/api/projects/{pid}/dematel/matrix',data={**fields,'revision':str(revision(browser,pid)),'csrf':csrf(browser),'reason':'Учебная экспертная оценка',**extra})


def test_demo_run_repeat_sources_and_historical_snapshot(dematel):
    browser,user=analyst(dematel);pid=dematel['demo_id'];source=source_id(dematel)
    with browser:
        response=calculate(browser,pid,[source]);assert response.status_code==201,response.text
        rid=response.json()['structure_id']
        again=calculate(browser,pid,[source]);assert again.status_code==200 and not again.json()['created']
        data=browser.get(f'/api/projects/{pid}/dematel/results/{rid}').json()
        assert data['verified'] and not data['is_stale'] and data['record']['created_by']==user['id']
        assert data['snapshot']['experts'][0]['is_demo'] and not data['snapshot']['experts'][0]['is_active']
        assert data['output']['indicators'][1]['D']==1
        assert browser.get(f'/projects/{pid}/dematel/results/{rid}').status_code==200
        assert browser.get(f'/projects/{pid}/dematel').status_code==200
        assert own_matrix(browser,pid,dematel).status_code==200
        assert browser.get(f'/api/projects/{pid}/dematel/results/{rid}').json()['is_stale']
        assert browser.get(f'/api/projects/{pid}/dematel/results/{rid}').json()['output']==data['output']
        before=revision(browser,pid);again=own_matrix(browser,pid,dematel)
        assert again.json()['changed'] is False and revision(browser,pid)==before
        selected=calculate(browser,pid,[source,user['id']]);assert selected.status_code==201


def test_expert_only_own_matrix_and_lpr_read_only(dematel):
    owner,ou=analyst(dematel);pid=dematel['demo_id']
    with owner,client(dematel) as expert,client(dematel) as lpr,client(dematel) as stranger:
        e=signup(expert);l=signup(lpr);signup(stranger)
        for u,role in [(e,'expert'),(l,'decision_maker')]:owner.post(f'/api/projects/{pid}/members',data={'email':u['email'],'role':role,'csrf':csrf(owner)})
        assert own_matrix(expert,pid,dematel).status_code==200
        assert own_matrix(expert,pid,dematel,expert_id=str(ou['id'])).status_code==422
        assert own_matrix(lpr,pid,dematel).status_code==403
        assert calculate(expert,pid,[e['id']]).status_code==403
        assert expert.post(f'/api/projects/{pid}/dematel/definition',data={'csrf':csrf(expert),'revision':revision(expert,pid)}).status_code==403
        rid=calculate(owner,pid,[e['id']]).json()['structure_id']
        assert lpr.get(f'/projects/{pid}/dematel/results/{rid}').status_code==200
        assert lpr.get(f'/projects/{pid}/dematel/definition').status_code==200
        assert stranger.get(f'/projects/{pid}/dematel/results/{rid}').status_code==404
        assert stranger.get(f'/projects/{pid}/dematel/results/{rid}/map.png').status_code==404
        other=new_project(owner)
        assert owner.get(f'/api/projects/{other}/dematel/results/{rid}').status_code==404
        assert calculate(owner,other,[e['id']]).status_code==422


def test_matrix_validation_csrf_and_conflict(dematel):
    browser,user=analyst(dematel);pid=dematel['demo_id']
    with browser:
        items=dematel['admin'].execute('SELECT id FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(pid,)).fetchall()
        first=items[0]['id'];key=f'cell_{first}_{first}'
        before=revision(browser,pid)
        assert own_matrix(browser,pid,dematel,**{key:'1'}).status_code==422
        assert revision(browser,pid)==before
        assert own_matrix(browser,pid,dematel,csrf='bad').status_code==403
        assert own_matrix(browser,pid,dematel).status_code==200
        assert own_matrix(browser,pid,dematel,revision=str(before)).status_code==409
        assert calculate(browser,pid,[]).status_code==422
        assert calculate(browser,pid,[user['id'],user['id']]).status_code==422
        assert calculate(browser,pid,[user['id']],threshold='NaN').status_code==422


def test_catwoe_saved_audited_noop_and_large_cyrillic_form(dematel):
    browser,user=analyst(dematel);pid=dematel['demo_id']
    with browser:
        fields={k:'Описание '+k for k in CATWOE};fields.update(root_definition='Учебная система преобразует сведения в обоснованную рекомендацию',reason='Уточнение предметной модели')
        path=f'/api/projects/{pid}/dematel/definition'
        def save(**extra):return browser.post(path,data={**fields,'revision':str(revision(browser,pid)),'csrf':csrf(browser),**extra})
        assert save(C='').status_code==422
        assert save().status_code==200
        before=revision(browser,pid);assert save().json()['changed'] is False and revision(browser,pid)==before
        assert browser.get(f'/projects/{pid}/dematel/definition').status_code==200
        assert save(**{k:'Я'*450 for k in CATWOE},root_definition='Я'*1400).status_code==200
        assert save(root_definition='bad\x00text').status_code==422


def test_zero_matrix_stored_but_not_calculated(dematel):
    with client(dematel) as browser:
        user=signup(browser);pid=new_project(browser)
        assert own_matrix(browser,pid,dematel,[[0]*5 for _ in range(5)]).status_code==200
        response=calculate(browser,pid,[user['id']]);assert response.status_code==422 and 'равны нулю' in response.json()['detail']
        assert dematel['admin'].execute('SELECT count(*) AS n FROM structure_models WHERE project_id=%s',(pid,)).fetchone()['n']==0


def test_charts_snapshot_and_immutable_grants(dematel):
    browser,user=analyst(dematel);pid=dematel['demo_id']
    with browser:
        rid=calculate(browser,pid,[source_id(dematel)],threshold='9999').json()['structure_id']
        for kind in ['map','network']:
            image=browser.get(f'/projects/{pid}/dematel/results/{rid}/{kind}.png')
            assert image.status_code==200 and image.content.startswith(b'\x89PNG') and image.headers['cache-control']=='no-store'
        saved=browser.get(f'/projects/{pid}/dematel/results/{rid}/snapshot.json')
        assert saved.status_code==200 and 'attachment' in saved.headers['content-disposition'] and saved.json()['output']['edges']==[]
        import psycopg
        for statement in ["UPDATE structure_models SET output='{}'",'DELETE FROM structure_models','DELETE FROM comparisons']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with dematel['database'].connect() as con:con.execute(statement)


def test_rollback_after_structure_insert(dematel,monkeypatch):
    import web_app.dematel_store as store
    original=store.run
    def broken(*args,**kwargs):original(*args,**kwargs);raise ValueError('Ошибка после вставки')
    browser,user=analyst(dematel);pid=dematel['demo_id']
    with browser:
        before=dematel['admin'].execute('SELECT count(*) AS n FROM structure_models WHERE project_id=%s',(pid,)).fetchone()['n']
        monkeypatch.setattr(store,'run',broken)
        assert calculate(browser,pid,[source_id(dematel)],threshold='.123').status_code==422
        assert dematel['admin'].execute('SELECT count(*) AS n FROM structure_models WHERE project_id=%s',(pid,)).fetchone()['n']==before
