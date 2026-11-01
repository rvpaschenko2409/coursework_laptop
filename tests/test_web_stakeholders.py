"""Mitchell classification, scoped CRUD, provenance, no-op edits and replay safety."""
from itertools import product
import pytest
pytest.importorskip('fastapi')
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst
from test_web_calculations import run as calculate
from test_web_dematel import revision
from web_app.stakeholder_validation import scores,validate,normalized_name,CLASSES
from web_app.alternative_validation import InvalidForm

BASE={'name':'Учебный представитель поставщика','stakeholder_role':'Источник предложений',
    'interests':'Предложить доступные модели с подходящими условиями поставки.',
    'power':'2','legitimacy':'3','urgency':'4','reason':'Учебное допущение для проверки реестра'}


def test_all_125_attribute_combinations_use_count_not_average():
    for power,legitimacy,urgency in product(range(1,6),repeat=3):
        values={'power':power,'legitimacy':legitimacy,'urgency':urgency};result=scores(values)
        count=sum(v>=3 for v in values.values())
        assert result['expressed_count']==count and result['class_label']==CLASSES[count]
        assert result['salience']==pytest.approx(round(sum(values.values())/3,3))
    a=scores({'power':5,'legitimacy':5,'urgency':1});b=scores({'power':3,'legitimacy':4,'urgency':4})
    assert a['salience']==b['salience'] and a['class_label']=='Ожидающий' and b['class_label']=='Определяющий'


@pytest.mark.parametrize('field,value',[
    ('name',''),('name','a'),('name','X'*201),('name','bad\x00name'),('name','bad\nname'),
    ('stakeholder_role',''),('stakeholder_role','X'*301),('interests',''),('interests','X'*1001),
    ('reason',''),('reason','X'*501),('interests','bad\x7ftext'),
    ('power','0'),('power','6'),('power','1.0'),('power','NaN'),('power','2,5'),('legitimacy',''),('urgency','-1')])
def test_invalid_fields_are_reported(field,value):
    with pytest.raises(InvalidForm) as exc:validate({**BASE,field:value})
    assert field in exc.value.errors


def test_clean_text_and_no_implicit_default_scores():
    result=validate({**BASE,'name':'  Учебный   представитель  '})
    assert result['name']=='Учебный представитель' and result['power']==2
    assert normalized_name('  ИТ-ЭКСПЕРТ  ')==normalized_name('ит-эксперт')
    assert normalized_name('Ｅｘｐｅｒｔ')==normalized_name('expert')
    for field in ['power','legitimacy','urgency']:
        form=dict(BASE);form.pop(field)
        with pytest.raises(InvalidForm):validate(form)


@pytest.fixture(scope='module')
def stakeholders_env(edits):return edits


def create(browser,pid,**changes):
    return browser.post(f'/api/projects/{pid}/stakeholders',data={**BASE,'csrf':csrf(browser),'revision':str(revision(browser,pid)),**changes})


def update(browser,pid,sid,**changes):
    data=browser.get(f'/api/projects/{pid}/stakeholders/{sid}').json()['stakeholder']
    values={key:str(data[key]) for key in BASE if key!='reason'}
    return browser.post(f'/api/projects/{pid}/stakeholders/{sid}',data={**values,'reason':'Уточнение учебной оценки','csrf':csrf(browser),'revision':str(revision(browser,pid)),**changes})


def remove(browser,pid,sid,name,**extra):
    return browser.post(f'/api/projects/{pid}/stakeholders/{sid}/delete',data={'csrf':csrf(browser),'revision':str(revision(browser,pid)),
        'confirm_name':name,'reason':'Участие в учебном сценарии завершено',**extra})


def test_existing_four_demo_rows_have_expected_scores_and_classes(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        response=browser.get(f'/api/projects/{pid}/stakeholders');assert response.status_code==200
        data=response.json();assert data['counts']['total']==4 and len(data['rows'])==4
        mapping={r['name']:r for r in data['rows']}
        for name,value,label in [('Руководитель',4,'Ожидающий'),('Офисные сотрудники',2.667,'Латентный'),('ИТ-специалист',3.333,'Ожидающий'),('Специалист по закупкам',3.333,'Ожидающий')]:
            assert mapping[name]['salience']==value and mapping[name]['class_label']==label
        assert browser.get(f'/projects/{pid}/stakeholders').status_code==200
        assert 'Заинтересованные стороны' in browser.get(f'/projects/{pid}').text


def test_create_update_delete_keep_full_history_and_real_author(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        members=env['admin'].execute('SELECT count(*) AS n FROM project_members WHERE project_id=%s',(pid,)).fetchone()['n']
        original_revision=revision(browser,pid)
        response=create(browser,pid);assert response.status_code==201,response.text
        sid=response.json()['stakeholder_id'];assert revision(browser,pid)==original_revision+1
        first=browser.get(f'/api/projects/{pid}/stakeholders/{sid}').json()['stakeholder']
        assert first['salience']==3 and first['class_label']=='Ожидающий'
        assert update(browser,pid,sid,power='4').status_code==200
        second=browser.get(f'/api/projects/{pid}/stakeholders/{sid}').json()['stakeholder']
        assert second['salience']==3.667 and second['class_label']=='Определяющий'
        assert remove(browser,pid,sid,BASE['name']).status_code==200
        assert browser.get(f'/api/projects/{pid}/stakeholders/{sid}').status_code==404
        events=[r for r in browser.get(f'/api/projects/{pid}/stakeholders/history').json()['rows'] if r['entity_id']==str(sid)]
        assert len(events)==3 and all(r['actor_id']==user['id'] for r in events)
        assert events[0]['details']['before']==second and events[0]['details']['after'] is None
        assert events[1]['details']['before']==first and events[1]['details']['after']==second
        assert events[2]['details']['before'] is None and events[2]['details']['after']==first
        assert all(r['details']['reason'] and r['details']['rule']['attribute_threshold']==3 for r in events)
        assert browser.get(f'/projects/{pid}/stakeholders/history').status_code==200
        assert env['admin'].execute('SELECT count(*) AS n FROM project_members WHERE project_id=%s',(pid,)).fetchone()['n']==members


def test_noop_and_duplicate_names_do_not_change_revision(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        sid=create(browser,pid,name='Уникальный учебный эксперт').json()['stakeholder_id']
        rev=revision(browser,pid)
        assert update(browser,pid,sid).json()['changed'] is False and revision(browser,pid)==rev
        response=create(browser,pid,name='  УНИКАЛЬНЫЙ   УЧЕБНЫЙ ЭКСПЕРТ  ')
        assert response.status_code==422 and 'name' in response.json()['errors'] and revision(browser,pid)==rev
        assert update(browser,pid,sid,name='Руководитель').status_code==422 and revision(browser,pid)==rev


def test_access_csrf_foreign_ids_and_mass_assignment(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser,client(env) as expert,client(env) as lpr,client(env) as outsider:
        e=signup(expert);l=signup(lpr);signup(outsider)
        for u,role in [(e,'expert'),(l,'decision_maker')]:browser.post(f'/api/projects/{pid}/members',data={'csrf':csrf(browser),'email':u['email'],'role':role})
        sid=create(browser,pid,name='Проверка доступа').json()['stakeholder_id']
        for who in [expert,lpr]:
            assert who.get(f'/api/projects/{pid}/stakeholders').status_code==200
            assert who.get(f'/projects/{pid}/stakeholders/history').status_code==200
            assert create(who,pid).status_code==403
            assert update(who,pid,sid).status_code==403
            assert remove(who,pid,sid,'Проверка доступа').status_code==403
            assert who.get(f'/projects/{pid}/stakeholders/new').status_code==403
            assert who.get(f'/projects/{pid}/stakeholders/{sid}/edit').status_code==403
        for path in ['stakeholders','stakeholders/history',f'stakeholders/{sid}']:
            assert outsider.get(f'/api/projects/{pid}/'+path).status_code==404
        assert create(browser,pid,csrf='bad').status_code==403
        for field in ['project_id','created_by','salience','class_label']:
            assert create(browser,pid,**{field:'1'}).status_code==422
        other=new_project(browser)
        assert browser.get(f'/api/projects/{other}/stakeholders/{sid}').status_code==404
        assert browser.post(f'/api/projects/{other}/stakeholders/{sid}',data={**BASE,'csrf':csrf(browser),'revision':revision(browser,other)}).status_code==404
        assert browser.post(f'/api/projects/{other}/stakeholders/{sid}/delete',data={'csrf':csrf(browser),'revision':revision(browser,other)}).status_code==404


def test_conflict_delete_confirmation_and_archived_project(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    with browser:
        old=revision(browser,pid);sid=create(browser,pid,name='Проверка удаления').json()['stakeholder_id']
        assert update(browser,pid,sid,revision=str(old)).status_code==409
        assert remove(browser,pid,sid,'Другая сторона').status_code==422
        assert remove(browser,pid,sid,'Проверка удаления',reason='').status_code==422
        assert browser.get(f'/api/projects/{pid}/stakeholders/{sid}').status_code==200
        admin.execute("UPDATE projects SET status='archived' WHERE id=%s",(pid,))
        try:
            assert create(browser,pid).status_code==422
            assert update(browser,pid,sid,power='1').status_code==422
            assert remove(browser,pid,sid,'Проверка удаления').status_code==422
            assert browser.get(f'/projects/{pid}/stakeholders').status_code==200
        finally:admin.execute("UPDATE projects SET status='draft' WHERE id=%s",(pid,))


def test_rollback_includes_registry_revision_and_audit(stakeholders_env,monkeypatch):
    import web_app.stakeholder_store as store
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id'];original=store.save;original_remove=store.remove
    def counts():return tuple(env['admin'].execute(f'SELECT count(*) AS n FROM {table} WHERE project_id=%s',(pid,)).fetchone()['n'] for table in ['stakeholders','audit_events'])+(revision(browser,pid),)
    def failed(*args,**kwargs):original(*args,**kwargs);raise InvalidForm({'name':'Тестовая ошибка после записи'})
    def failed_remove(*args,**kwargs):original_remove(*args,**kwargs);raise InvalidForm({'reason':'Тестовая ошибка после удаления'})
    with browser:
        sid=create(browser,pid,name='Проверка отката').json()['stakeholder_id'];before=counts()
        monkeypatch.setattr(store,'save',failed)
        assert update(browser,pid,sid,power='1').status_code==422 and counts()==before
        assert create(browser,pid,name='Несохранённая сторона').status_code==422 and counts()==before
        monkeypatch.setattr(store,'remove',failed_remove)
        assert remove(browser,pid,sid,'Проверка отката').status_code==422 and counts()==before
        assert browser.get(f'/api/projects/{pid}/stakeholders/{sid}').json()['stakeholder']['power']==2


def test_weights_and_historical_calculations_are_preserved(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id'];admin=env['admin']
    with browser:
        rid=calculate(browser,pid).json()['result_ids']['marcos']
        original=browser.get(f'/api/projects/{pid}/calculations/{rid}').json()['calculated']
        oldweights=admin.execute('SELECT * FROM weights WHERE project_id=%s ORDER BY weight_set_id,criterion_id',(pid,)).fetchall()
        assert create(browser,pid,name='Проверка сохранности расчётов').status_code==201
        data=browser.get(f'/api/projects/{pid}/calculations/{rid}').json()
        assert data['verified'] and data['is_stale'] and data['calculated']==original
        assert admin.execute('SELECT * FROM weights WHERE project_id=%s ORDER BY weight_set_id,criterion_id',(pid,)).fetchall()==oldweights


def test_unicode_maximum_form_and_unchanged_other_body_limits(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        response=create(browser,pid,name='Я'*200,stakeholder_role='Я'*300,interests='Я'*1000,reason='Я'*500)
        assert response.status_code==201,response.text
        assert create(browser,pid,name='A'*40000).status_code==413
        assert browser.post('/api/projects',content=b'x'*9000,headers={'content-type':'application/x-www-form-urlencoded'}).status_code==413


def test_xss_validation_retention_and_duplicate_fields(stakeholders_env):
    from urllib.parse import urlencode
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        name='<img src=x onerror=alert(1)>'
        sid=create(browser,pid,name=name).json()['stakeholder_id']
        for path in ['stakeholders',f'stakeholders/{sid}/edit','stakeholders/history']:
            page=browser.get(f'/projects/{pid}/'+path);assert page.status_code==200
            assert '<img src=x' not in page.text and '&lt;img' in page.text
        bad=browser.post(f'/projects/{pid}/stakeholders',data={**BASE,'name':'Название для проверки','power':'bad','csrf':csrf(browser),'revision':revision(browser,pid)})
        assert bad.status_code==422 and 'Название для проверки' in bad.text and 'id="error-power"' in bad.text
        assert browser.post(f'/api/projects/{pid}/stakeholders',content=urlencode([('csrf',csrf(browser)),('name','Первое'),('name','Второе')]),headers={'content-type':'application/x-www-form-urlencoded'}).status_code==422


def test_registry_and_history_pagination(stakeholders_env):
    env=stakeholders_env;browser,user=analyst(env);pid=env['demo_id']
    with browser:
        for i in range(21):assert create(browser,pid,name=f'Сторона пагинации {i}').status_code==201
        for suffix in ['stakeholders','stakeholders/history']:
            first=browser.get(f'/api/projects/{pid}/{suffix}?page=1').json();second=browser.get(f'/api/projects/{pid}/{suffix}?page=2').json()
            assert len(first['rows'])==20 and first['has_next'] and second['rows']
            assert {r['id'] for r in first['rows']}.isdisjoint({r['id'] for r in second['rows']})
            assert browser.get(f'/projects/{pid}/{suffix}?page=2').status_code==200
            assert browser.get(f'/projects/{pid}/{suffix}?page=0').status_code==422


def test_generated_average_and_project_identity_privileges(stakeholders_env):
    import psycopg
    env=stakeholders_env
    for statement in ['UPDATE stakeholders SET project_id=project_id','UPDATE stakeholders SET id=id',
        'DELETE FROM audit_events','UPDATE audit_events SET details=details']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with env['database'].connect() as connection:connection.execute(statement)
