"""Real core parity, web actor attribution, transaction safety and scoped replay."""
from io import BytesIO
import time
import pytest
pytest.importorskip('fastapi')
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,create,edit,analyst
from web_app.calculation_store import parse_v


@pytest.mark.parametrize('value',['','NaN','Infinity','-0.01','1.01','true','1e9999'])
def test_invalid_v(value):
    with pytest.raises(ValueError):parse_v(value)


@pytest.mark.parametrize('value,expected',[('0',0),('1',1),('0,5',.5),('0.25',.25)])
def test_v_boundaries(value,expected):assert parse_v(value)==expected


@pytest.fixture(scope='module')
def calculations(edits):
    from decision_store.calculations import run_calculation
    with edits['admin'].transaction():legacy=run_calculation(edits['admin'],edits['demo_id'],.5)
    return {**edits,'legacy':legacy}


def revision(browser,pid):return browser.get(f'/api/projects/{pid}').json()['project']['revision']


def run(browser,pid,**changes):
    return browser.post(f'/api/projects/{pid}/calculations',data={'v':'0.5','csrf':csrf(browser),'revision':str(revision(browser,pid)),**changes})


def test_control_results_real_user_and_repeat(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id']
    with browser:
        response=run(browser,pid)
        assert response.status_code==201,response.text
        result=response.json();assert result['created']
        assert result['marcos_leader']=='A4' and result['vikor_leader']=='A2' and result['compromise_set']==['A2','A3','A5']
        stored=calculations['admin'].execute('SELECT created_by,input_snapshot FROM results WHERE id=%s',(result['result_ids']['marcos'],)).fetchone()
        assert stored['created_by']==user['id'] and stored['input_snapshot']['producer']['requested_by']==user['id']
        assert stored['created_by']!=stored['input_snapshot']['project']['owner_id']
        response=run(browser,pid)
        assert response.status_code==200 and not response.json()['created']
        assert response.json()['result_ids']==result['result_ids']
        rid=result['result_ids']['marcos']
        data=browser.get(f'/api/projects/{pid}/calculations/{rid}').json()
        assert data['verified'] and not data['is_stale'] and len(data['comparison'])==5
        assert sum(w['weight'] for w in data['calculated']['merec']['weights'])==pytest.approx(1)
        assert browser.get(f'/projects/{pid}/calculations/{rid}').status_code==200
        assert browser.get(f'/projects/{pid}/calculations').status_code==200
        assert browser.get(f'/api/projects/{pid}/calculations').json()['runs']


def test_legacy_cli_pair_readable_and_replay_unchanged(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id'];rid=calculations['legacy']['result_ids']['marcos']
    with browser:
        response=browser.get(f'/projects/{pid}/calculations/{rid}')
        assert response.status_code==200,response.text
        assert 'Командная строка' in response.text and 'A2, A3, A5' in response.text
        replay=browser.post(f'/api/projects/{pid}/calculations/{rid}/replay',data={'csrf':csrf(browser)})
        assert replay.status_code==200 and replay.json()['verified']
        assert browser.get(f'/api/projects/{pid}/calculations/{rid}').json()['results']['marcos']['params'].get('entrypoint') is None


def test_roles_csrf_and_other_project_ids(calculations):
    browser,owner=analyst(calculations);pid=calculations['demo_id']
    with browser,client(calculations) as expert,client(calculations) as lpr,client(calculations) as outsider:
        e=signup(expert);l=signup(lpr);signup(outsider)
        for user,role in [(e,'expert'),(l,'decision_maker')]:
            browser.post(f'/api/projects/{pid}/members',data={'email':user['email'],'role':role,'csrf':csrf(browser)})
        rid=run(browser,pid).json()['result_ids']['marcos']
        assert run(expert,pid).status_code==403 and run(lpr,pid).status_code==403
        assert browser.post(f'/api/projects/{pid}/calculations',data={'v':'.5','revision':revision(browser,pid)}).status_code==403
        for who in [expert,lpr]:
            assert who.get(f'/projects/{pid}/calculations/{rid}').status_code==200
            assert who.get(f'/projects/{pid}/calculations/{rid}/snapshot.json').status_code==200
        for suffix in ['', '/weights.png','/snapshot.json']:
            assert outsider.get(f'/projects/{pid}/calculations/{rid}'+suffix).status_code==404
        other=new_project(browser)
        assert browser.get(f'/api/projects/{other}/calculations/{rid}').status_code==404
        assert browser.get(f'/projects/{other}/calculations/{rid}/ranks.png').status_code==404
        assert run(browser,pid,csrf='ошибка').status_code==403


def test_budget_filter_and_v_extremes(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id']
    with browser:
        admin=calculations['admin'];old=admin.execute('SELECT budget FROM projects WHERE id=%s',(pid,)).fetchone()['budget']
        try:
            admin.execute('UPDATE projects SET budget=60000 WHERE id=%s',(pid,))
            for v in ['0','1']:
                response=run(browser,pid,v=v);assert response.status_code==201,response.text
                result=response.json();assert result['excluded']==['A3','A4']
                data=browser.get(f"/api/projects/{pid}/calculations/{result['result_ids']['marcos']}").json()
                assert len(data['comparison'])==3 and data['snapshot']['parameters']['v']==float(v)
            admin.execute('UPDATE projects SET budget=1 WHERE id=%s',(pid,))
            response=run(browser,pid);assert response.status_code==422 and 'меньше двух' in response.json()['detail']
        finally:admin.execute('UPDATE projects SET budget=%s WHERE id=%s',(old,pid))


def test_missing_data_stale_revision_and_validation_html(calculations):
    with client(calculations) as browser:
        signup(browser);pid=new_project(browser)
        assert run(browser,pid).status_code==422
        original=revision(browser,pid);create(browser,pid)
        assert run(browser,pid,revision=str(original)).status_code==409
        create(browser,pid,code='X2')
        response=run(browser,pid);assert response.status_code==422 and 'performance_score' in response.json()['detail']
        response=browser.post(f'/projects/{pid}/calculations',data={'v':'abc','revision':revision(browser,pid),'csrf':csrf(browser)})
        assert response.status_code==422 and 'value="abc"' in response.text and 'Расчёт не сохранён' in response.text
        assert browser.get(f'/api/projects/{pid}/calculations').json()['runs']==[]


def test_full_transaction_rollback_after_insert(calculations,monkeypatch):
    import web_app.calculation_routes as routes
    original=routes.run_for_user
    def failed(*args,**kwargs):
        original(*args,**kwargs)
        raise ValueError('Тестовая ошибка после записи')
    browser,user=analyst(calculations);pid=calculations['demo_id'];admin=calculations['admin']
    def counts():return tuple(admin.execute(f'SELECT count(*) AS n FROM {table} WHERE project_id=%s',(pid,)).fetchone()['n'] for table in ['results','weight_sets','weights','audit_events'])
    with browser:
        before=counts();monkeypatch.setattr(routes,'run_for_user',failed)
        response=run(browser,pid,v='.333')
        assert response.status_code==422 and counts()==before


def test_change_marks_history_stale_but_preserves_replay(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id']
    with browser:
        result=run(browser,pid).json();rid=result['result_ids']['marcos']
        aid=calculations['admin'].execute("SELECT id FROM alternatives WHERE project_id=%s AND code='A1'",(pid,)).fetchone()['id']
        old=calculations['admin'].execute('SELECT value FROM scores s JOIN criteria c ON c.id=s.criterion_id WHERE s.alternative_id=%s AND c.code=%s',(aid,'price_rub')).fetchone()['value']
        assert edit(browser,pid,aid,price_rub=str(old+1)).status_code==200
        response=browser.get(f'/api/projects/{pid}/calculations/{rid}')
        assert response.status_code==200 and response.json()['is_stale']
        assert response.json()['snapshot']['project']['revision']==result['project_revision']
        assert browser.post(f'/api/projects/{pid}/calculations/{rid}/replay',data={'csrf':csrf(browser)}).json()['verified']
        assert run(browser,pid).json()['result_ids']['marcos']!=rid


def test_charts_snapshot_and_append_only_results(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id']
    with browser:
        rid=run(browser,pid).json()['result_ids']['marcos']
        for chart in ['weights','ranks']:
            image=browser.get(f'/projects/{pid}/calculations/{rid}/{chart}.png')
            assert image.status_code==200 and image.headers['content-type']=='image/png' and image.content.startswith(b'\x89PNG')
            assert image.headers['cache-control']=='no-store'
            from PIL import Image
            assert Image.open(BytesIO(image.content)).size==(1248,663)
        assert browser.get(f'/projects/{pid}/calculations/{rid}/unknown.png').status_code==404
        response=browser.get(f'/projects/{pid}/calculations/{rid}/snapshot.json')
        assert response.status_code==200 and 'attachment' in response.headers['content-disposition']
        assert response.json()['input']['producer']['requested_by']==user['id']
        import psycopg
        for statement in ["UPDATE results SET output='{}'",'DELETE FROM results','DELETE FROM weights','UPDATE weight_sets SET created_by=created_by']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with calculations['database'].connect() as con:con.execute(statement)


def test_distinct_analysts_have_real_authorship(calculations):
    a,u1=analyst(calculations);b,u2=analyst(calculations);pid=calculations['demo_id']
    with a,b:
        r1=run(a,pid).json();r2=run(b,pid).json()
        assert r1['result_ids']!=r2['result_ids']
        for user,result in [(u1,r1),(u2,r2)]:
            row=calculations['admin'].execute('SELECT created_by FROM results WHERE id=%s',(result['result_ids']['marcos'],)).fetchone()
            assert row['created_by']==user['id']


def test_history_pagination_and_invalid_query(calculations):
    browser,user=analyst(calculations);pid=calculations['demo_id']
    with browser:
        for i in range(21):assert run(browser,pid,v=str(i/20)).status_code==201
        first=browser.get(f'/api/projects/{pid}/calculations?page=1').json()
        second=browser.get(f'/api/projects/{pid}/calculations?page=2').json()
        assert len(first['runs'])==20 and first['has_next'] and second['runs']
        assert {r['result_id'] for r in first['runs']}.isdisjoint({r['result_id'] for r in second['runs']})
        assert browser.get(f'/projects/{pid}/calculations?page=0').status_code==422
