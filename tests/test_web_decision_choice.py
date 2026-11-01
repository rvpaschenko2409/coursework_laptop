"""Deliberate LPR choice: permissions, frozen provenance, retries and revision conflicts."""
import copy
import pytest
pytest.importorskip('fastapi')
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst
from test_web_calculations import run as calculation,revision
from web_app.decision_choice_logic import reason,previous_id,verified_context
from decision_store.calculations import digest


@pytest.mark.parametrize('value',['','a'*19,'a'*2001,'Достаточное обоснование\x00','Текст с символом\x1f для проверки'])
def test_reason_rejects_bad_input(value):
    with pytest.raises(ValueError):reason(value)


def test_reason_unicode_and_bounds():
    assert reason('  '+ 'а'*20 +'  ')=='а'*20
    assert reason('ё'*2000)=='ё'*2000
    assert reason('е\u0308'*20)=='ё'*20
    assert '\n' in reason('Подробное обоснование\nвыбора для работы')


@pytest.mark.parametrize('value',['','-1','1.0','NaN','１２','1'*19])
def test_previous_anchor_validation(value):
    with pytest.raises(ValueError):previous_id(value)


def test_previous_anchor_zero():assert previous_id('0')==0


@pytest.fixture(scope='module')
def choices(edits):return edits


def setup(env):
    a,u=analyst(env);l=client(env);v=signup(l);pid=env['demo_id']
    r=a.post(f'/api/projects/{pid}/members',data={'csrf':csrf(a),'email':v['email'],'role':'decision_maker'})
    assert r.status_code==200,r.text
    result=calculation(a,pid);assert result.status_code in (200,201),result.text
    return a,l,u,v,result.json()['result_ids']


def fields(browser,pid,**extra):
    latest=browser.get(f'/api/projects/{pid}/choices').json()['latest_choice']
    return {'csrf':csrf(browser),'revision':str(revision(browser,pid)),'alternative_code':'A2',
        'reason':'Выбрана сбалансированная конфигурация для офисных задач в пределах бюджета.',
        'previous_choice_id':str(latest['id'] if latest else 0),'acknowledge':'yes',**extra}


def post(browser,pid,rid,form=None,**extra):
    return browser.post(f'/api/projects/{pid}/calculations/{rid}/choice',data=form or fields(browser,pid,**extra))


def test_real_lpr_snapshot_retry_pair_identity_and_views(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id']
    with a,l:
        initial=revision(l,pid);f=fields(l,pid)
        response=post(l,pid,ids['vikor'],f);assert response.status_code==201,response.text
        cid=response.json()['choice_id'];assert response.json()['source_result_id']==ids['marcos']
        for rid in ids.values():
            retry=post(l,pid,rid,f);assert retry.status_code==200 and retry.json()['choice_id']==cid
            assert retry.json()['created'] is False
        assert revision(l,pid)==initial
        data=l.get(f'/api/projects/{pid}/choices/{cid}').json()
        assert data['verified'] and data['is_latest'] and not data['is_stale']
        assert data['record']['decided_by']==lu['id']!=au['id']
        assert data['context']['choice']['role_at_decision']=='decision_maker'
        assert data['selected']['marcos']['rank']==2 and data['selected']['vikor']['rank']==1
        assert data['selected']['in_compromise'] and len(data['selected']['scores'])==5
        assert data['context']['source']['outputs']['vikor']['conditions']['compromise_set']==['A2','A3','A5']
        for who in [a,l]:
            for path in [f'/projects/{pid}/choices',f'/projects/{pid}/choices/{cid}',f'/projects/{pid}/calculations/{ids["marcos"]}/choice']:
                page=who.get(path);assert page.status_code==200,page.text
            assert who.post(f'/api/projects/{pid}/choices/{cid}/replay',data={'csrf':csrf(who)}).json()['verified']
        assert 'только участник с ролью' in a.get(f'/projects/{pid}/calculations/{ids["marcos"]}/choice').text
        download=l.get(f'/projects/{pid}/choices/{cid}/snapshot.json')
        assert 'attachment;' in download.headers['content-disposition'] and download.json()['context']==data['context']
        assert env['admin'].execute("SELECT count(*) AS n FROM audit_events WHERE action='web_decision_choice' AND entity_id=%s",(str(cid),)).fetchone()['n']==1


def test_permissions_scope_csrf_and_injected_fields(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos']
    with a,l,client(env) as expert,client(env) as outsider:
        eu=signup(expert);signup(outsider)
        a.post(f'/api/projects/{pid}/members',data={'csrf':csrf(a),'email':eu['email'],'role':'expert'})
        ids=calculation(a,pid).json()['result_ids'];rid=ids['marcos']
        for who in [a,expert]:assert post(who,pid,rid).status_code==403
        assert post(l,pid,rid,csrf='bad').status_code==403
        assert post(l,pid,rid,decided_by=str(au['id'])).status_code==422
        assert outsider.get(f'/api/projects/{pid}/choices').status_code==404
        payload=fields(l,pid);payload['csrf']=csrf(outsider)
        assert post(outsider,pid,rid,payload).status_code==404
        other=new_project(a)
        assert a.get(f'/projects/{other}/calculations/{rid}/choice').status_code==404
        assert a.get(f'/api/projects/{other}/choices/1').status_code==404
        # Creator remains an analyst; no self-promotion shortcut.
        assert a.post(f'/api/projects/{pid}/members',data={'csrf':csrf(a),'email':au['email'],'role':'decision_maker'}).status_code==403


def test_required_confirmation_validation_and_html_escape(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos']
    with a,l:
        for extra in [{'reason':''},{'acknowledge':''},{'previous_choice_id':'bad'},{'alternative_code':'foreign'}]:
            r=post(l,pid,rid,**extra);assert r.status_code==422,r.text
        text='<script>alert("x")</script> содержательное обоснование'
        f=fields(l,pid,reason=text,alternative_code='A4')
        response=l.post(f'/projects/{pid}/calculations/{rid}/choice',data={**f,'acknowledge':''})
        assert response.status_code==422 and '&lt;script&gt;' in response.text
        assert 'value="A4" selected' in response.text
        r=post(l,pid,rid,f);assert r.status_code==201,r.text
        page=l.get(f'/projects/{pid}/choices/{r.json()["choice_id"]}')
        assert '&lt;script&gt;' in page.text and '<script>' not in page.text
        assert 'вне компромиссного набора' in page.text
        # 2,000 Cyrillic characters fit the bounded URL-encoded request.
        assert post(l,pid,rid,reason='а'*2000).status_code==201


def test_explicit_reconsideration_and_two_lpr_conflict(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos']
    with a,l:
        first=fields(l,pid);second={**first,'alternative_code':'A3','reason':'Другой выбор после сравнения автономности и рабочих требований.'}
        c1=post(l,pid,rid,first);assert c1.status_code==201,c1.text
        assert post(l,pid,rid,second).status_code==409
        c2=post(l,pid,rid,alternative_code='A3',reason=second['reason']);assert c2.status_code==201,c2.text
        old=l.get(f'/api/projects/{pid}/choices/{c1.json()["choice_id"]}').json()
        new=l.get(f'/api/projects/{pid}/choices/{c2.json()["choice_id"]}').json()
        assert not old['is_latest'] and new['is_latest']
        assert new['context']['choice']['previous_choice_id']==old['record']['id']
        assert post(l,pid,rid,first).json()['created'] is False


def test_stale_input_and_archived_project_block_new_choice_but_history_survives(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos'];db=env['admin']
    with a,l:
        f=fields(l,pid);r=post(l,pid,rid,f);assert r.status_code==201
        cid=r.json()['choice_id'];old=l.get(f'/api/projects/{pid}/choices/{cid}').json()
        aid=old['selected']['alternative']['id']
        db.execute('UPDATE alternatives SET name=name||%s WHERE id=%s',(' изменён',aid))
        assert post(l,pid,rid,f).status_code==409
        assert post(l,pid,rid).status_code==422
        historical=l.get(f'/api/projects/{pid}/choices/{cid}').json()
        assert historical['verified'] and historical['is_stale'] and historical['selected']==old['selected']
        assert 'Показаны прежние' in l.get(f'/projects/{pid}/choices/{cid}').text
        # Replay depends on embedded pair, not a live VIKOR row.
        db.execute('DELETE FROM results WHERE id=%s',(ids['vikor'],))
        assert l.get(f'/api/projects/{pid}/choices/{cid}').json()['verified']
        db.execute("UPDATE projects SET status='archived' WHERE id=%s",(pid,))
        try:assert post(l,pid,rid).status_code==422
        finally:db.execute("UPDATE projects SET status='active' WHERE id=%s",(pid,))


def test_budget_exclusion_and_revoked_role(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    original=db.execute('SELECT budget FROM projects WHERE id=%s',(pid,)).fetchone()['budget']
    with a,l:
        try:
            db.execute('UPDATE projects SET budget=60000 WHERE id=%s',(pid,))
            rid=calculation(a,pid).json()['result_ids']['marcos']
            assert post(l,pid,rid,alternative_code='A4').status_code==422
            r=post(l,pid,rid);assert r.status_code==201,r.text
            f=fields(l,pid);db.execute("UPDATE project_members SET role='expert' WHERE project_id=%s AND user_id=%s",(pid,lu['id']))
            assert post(l,pid,rid,f).status_code==403
        finally:db.execute('UPDATE projects SET budget=%s WHERE id=%s',(original,pid))


def test_tampered_saved_context_detected_and_transaction_rollback(choices,monkeypatch):
    from psycopg.types.json import Jsonb
    import web_app.decision_choice_store as store
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos'];db=env['admin']
    with a,l:
        r=post(l,pid,rid);assert r.status_code==201,r.text
        cid=r.json()['choice_id'];saved=db.execute('SELECT alternative_snapshot FROM decision_choices WHERE id=%s',(cid,)).fetchone()['alternative_snapshot']
        broken=copy.deepcopy(saved);broken['context']['selected']['marcos']['rank']=1
        db.execute('UPDATE decision_choices SET alternative_snapshot=%s WHERE id=%s',(Jsonb(broken),cid))
        assert l.get(f'/api/projects/{pid}/choices/{cid}').status_code==422
        broken['input_fingerprint']=digest(broken['context'])
        db.execute('UPDATE decision_choices SET alternative_snapshot=%s WHERE id=%s',(Jsonb(broken),cid))
        assert l.get(f'/api/projects/{pid}/choices/{cid}').status_code==422
        db.execute('UPDATE decision_choices SET alternative_snapshot=%s WHERE id=%s',(Jsonb(saved),cid))
        original=store.run
        def fail(*args,**kwargs):original(*args,**kwargs);raise ValueError('Контрольный откат после вставки')
        def counts():return tuple(db.execute(f'SELECT count(*) AS n FROM {t} WHERE project_id=%s',(pid,)).fetchone()['n'] for t in ['decision_choices','audit_events'])
        before=counts();monkeypatch.setattr(store,'run',fail)
        assert post(l,pid,rid,reason='Новое подробное обоснование для проверки отката транзакции.').status_code==422
        assert counts()==before


def test_all_weight_sources_preserve_source_and_choice(choices):
    from test_web_weights import post as weighted
    from test_web_dematel import calculate,source_id
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id']
    with a,l:
        did=calculate(a,pid,[source_id(env)]).json()['structure_id']
        for method in ['merec','manual','dematel','combined']:
            r=weighted(a,pid,env,method=method,**({'dematel_id':str(did)} if method=='dematel' else {}))
            assert r.status_code in (200,201),r.text
            rid=r.json()['result_ids']['marcos'];c=post(l,pid,rid);assert c.status_code==201,c.text
            data=l.get(f'/api/projects/{pid}/choices/{c.json()["choice_id"]}').json()
            assert data['verified'] and data['context']['source']['weighting']['method']==method
            assert 'Выбор зафиксирован' in l.get(f'/projects/{pid}/choices/{c.json()["choice_id"]}').text


def test_duplicate_fields_and_request_limit(choices):
    from urllib.parse import urlencode
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos']
    with a,l:
        pairs=list(fields(l,pid).items())+[('alternative_code','A1')]
        r=l.post(f'/api/projects/{pid}/calculations/{rid}/choice',content=urlencode(pairs),headers={'content-type':'application/x-www-form-urlencoded'})
        assert r.status_code==422
        assert post(l,pid,rid,reason='a'*33000).status_code==413


def test_application_cannot_modify_or_delete_choices_and_fk_retains_source(choices):
    import psycopg
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id']
    with a,l:
        cid=post(l,pid,ids['marcos']).json()['choice_id']
        for statement in ['UPDATE decision_choices SET reason=reason WHERE id=%s','DELETE FROM decision_choices WHERE id=%s']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with env['database'].connect() as connection:connection.execute(statement,(cid,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with env['admin'].transaction():env['admin'].execute('DELETE FROM results WHERE id=%s',(ids['marcos'],))
        assert l.get(f'/api/projects/{pid}/choices/{cid}').json()['verified']


def test_saved_actor_and_frozen_choice_are_independent_of_current_names(choices):
    env=choices;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    with a,l:
        cid=post(l,pid,ids['marcos']).json()['choice_id']
        data=l.get(f'/api/projects/{pid}/choices/{cid}').json()
        db.execute("UPDATE users SET name='Переименованный участник' WHERE id=%s",(lu['id'],))
        current=l.get(f'/api/projects/{pid}/choices/{cid}').json()
        assert current['verified'] and current['context']['choice']['actor_name']==data['context']['choice']['actor_name']
        assert current['context']['choice']['role_at_decision']=='decision_maker'
        assert l.get(f'/api/projects/{pid}/choices?page=0').status_code==422
        assert l.get(f'/projects/{pid}/choices?page=2').status_code==200
