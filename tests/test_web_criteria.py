"""Variable criteria, source semantics, authorization and historical replay on PostgreSQL."""
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from decision_core.merec import calculate_merec
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_core.normalization import normalize_ratio
from web_app.criterion_store import validate, positive
from test_web_auth import web_env, client, signup, csrf, new_project
from test_web_alternatives import edits, analyst
from test_web_calculations import run, revision
from web_app.database import grant_criteria_permissions


@pytest.mark.parametrize('n', [2,3,6,10])
def test_explicit_directions_and_column_permutation(n):
    frame=pd.DataFrame({f'c{i}': [1+i,3+i,2+i] for i in range(n)},index=['a','b','c'])
    frame.attrs['directions']={f'c{i}': 'min' if i%2 else 'max' for i in range(n)}
    weights=calculate_merec(frame).set_index('criterion')['weight']
    assert weights.sum()==pytest.approx(1)
    before=calculate_marcos(frame,weights).sort_index()
    other=frame[frame.columns[::-1]]
    np.testing.assert_allclose(before['utility'],calculate_marcos(other,weights).sort_index()['utility'],atol=1e-12)
    v1,c1=calculate_vikor(frame,weights)
    v2,c2=calculate_vikor(other,weights)
    np.testing.assert_allclose(v1.sort_index().Q,v2.sort_index().Q,atol=1e-12)
    assert c1['compromise_set']==c2['compromise_set']


def test_cost_direction_controls_preference_and_hand_values():
    frame=pd.DataFrame({'cost':[1,2,4],'benefit':[1,4,2]})
    frame.attrs['directions']={'cost':'min','benefit':'max'}
    np.testing.assert_allclose(normalize_ratio(frame),[[1,.25],[.5,1],[.25,.5]])
    weights=pd.Series({'cost':1.,'benefit':0.})
    assert calculate_marcos(frame,weights).index[0]==0
    frame.attrs['directions']['cost']='max'
    assert calculate_marcos(frame,weights).index[0]==2


@pytest.mark.parametrize('value',['NaN','Infinity','0','-1','1e999',''])
def test_invalid_score(value):
    with pytest.raises(HTTPException):positive(value)


def test_metadata_rejects_code_mutation_and_bad_direction():
    form=dict(code='screen',name='Экран',unit='дюйм',direction='max',reason='По спецификации')
    assert validate(form)[0]['code']=='screen'
    with pytest.raises(HTTPException):validate({**form,'direction':'unknown'})
    with pytest.raises(HTTPException):validate(form,{'code':'old'})


@pytest.fixture(scope='module')
def criteria_env(edits):
    schema=edits['admin'].execute('SELECT current_schema() AS s').fetchone()['s']
    grant_criteria_permissions(edits['admin'],schema,edits['database'].expected_role)
    return edits


def form(browser,pid,**values):
    return dict(csrf=csrf(browser),revision=str(revision(browser,pid)),**values)


def copy_demo(env,browser):
    import secrets
    pid=env['demo_id']
    response=browser.post(f'/api/projects/{pid}/copy',data=form(browser,pid,title='Проверка критериев',goal='Проверка',budget='100000',request_key=secrets.token_urlsafe(20)))
    assert response.status_code==201,response.text
    return response.json()['project_id']


def test_lifecycle_replay_offline_export_and_copy(criteria_env):
    browser,_=analyst(criteria_env)
    with browser:
        pid=copy_demo(criteria_env,browser)
        initial=run(browser,pid).json()['result_ids']['marcos']
        data=browser.get(f'/api/projects/{pid}').json()
        values={f"score_{a['id']}":str(13+i) for i,a in enumerate(data['alternatives'])}
        added=browser.post(f'/api/projects/{pid}/criteria',data=form(browser,pid,code='screen_inches',name='Диагональ экрана',unit='дюйм',direction='max',reason='Учитываем размер экрана',**values))
        assert added.status_code==201,added.text
        cid=added.json()['criterion_id']
        assert len(browser.get(f'/api/projects/{pid}').json()['criteria'])==6
        assert browser.get(f'/projects/{pid}/criteria').status_code==200
        assert browser.get(f'/projects/{pid}/criteria/{cid}/edit').status_code==200
        current=run(browser,pid);assert current.status_code==201,current.text
        rid=current.json()['result_ids']['marcos']
        pair=browser.get(f'/api/projects/{pid}/calculations/{rid}');assert pair.status_code==200,pair.text
        assert len(pair.json()['weighting']['weights'])==6
        preview=browser.get(f'/api/projects/{pid}/calculations/{rid}/preview').json()
        assert preview['directions']['screen_inches']=='max'
        assert browser.get(f'/api/projects/{pid}/offline').json()['directions']==preview['directions']
        trial=browser.post(f'/api/projects/{pid}/calculations/{rid}/preview',data=dict(csrf=csrf(browser),criterion='screen_inches',change='25',v='.7'))
        assert trial.status_code==200,trial.text
        export=browser.get(f'/projects/{pid}/calculations/{rid}/export.xlsx')
        assert export.status_code==200,export.text[:100]
        old=browser.get(f'/api/projects/{pid}/calculations/{initial}')
        assert old.status_code==200 and old.json()['is_stale']
        # Delete from current comparisons, preserve historical weight foreign keys.
        deleted=browser.post(f'/api/projects/{pid}/criteria/{cid}/delete',data=form(browser,pid,confirmation='screen_inches',reason='Проверка удаления критерия'))
        assert deleted.status_code==200,deleted.text
        assert len(browser.get(f'/api/projects/{pid}').json()['criteria'])==5
        assert browser.get(f'/api/projects/{pid}/calculations/{rid}').status_code==200
        assert run(browser,pid).status_code==201
        copied=browser.post(f'/api/projects/{pid}/copy',data=form(browser,pid,title='Копия без экрана',goal='',budget='100000',request_key='copy-with-archived-criteria'))
        assert copied.status_code==201,copied.text
        assert len(browser.get(f"/api/projects/{copied.json()['project_id']}").json()['criteria'])==5
        restored=browser.post(f'/api/projects/{pid}/criteria/{cid}/restore',data=form(browser,pid,confirmation='screen_inches',reason='Проверка восстановления критерия'))
        assert restored.status_code==200,restored.text
        assert len(browser.get(f'/api/projects/{pid}').json()['criteria'])==6
        assert run(browser,pid).status_code==201


def test_roles_csrf_stale_fields_and_no_partial_write(criteria_env):
    browser,_=analyst(criteria_env)
    with browser,client(criteria_env) as expert,client(criteria_env) as stranger:
        pid=copy_demo(criteria_env,browser);u=signup(expert);signup(stranger)
        browser.post(f'/api/projects/{pid}/members',data=dict(csrf=csrf(browser),email=u['email'],role='expert'))
        assert expert.get(f'/projects/{pid}/criteria/new').status_code==403
        assert stranger.get(f'/projects/{pid}/criteria').status_code==404
        data=browser.get(f'/api/projects/{pid}').json();c=data['criteria'][0]
        assert browser.post(f"/api/projects/{pid}/criteria/{c['id']}/delete",data=form(browser,pid,confirmation=c['code'],reason='Нельзя удалить цену')).status_code==422
        baseline=revision(browser,pid)
        bad=form(browser,pid,code='screen',name='Экран',unit='дюйм',direction='max',reason='Отсутствуют оценки')
        assert browser.post(f'/api/projects/{pid}/criteria',data=bad).status_code==422
        assert revision(browser,pid)==baseline
        assert browser.post(f'/api/projects/{pid}/criteria',data={**bad,'csrf':'bad'}).status_code==403
        assert browser.post(f'/api/projects/{pid}/criteria',data={**bad,'revision':'0'}).status_code==409
        assert browser.post(f'/api/projects/{pid}/criteria',data={**bad,'score_999999':'10'}).status_code==422


def test_two_to_ten_criteria_and_dynamic_laptop_edit(criteria_env):
    from test_web_alternatives import edit as edit_laptop
    browser,_=analyst(criteria_env)
    with browser:
        pid=copy_demo(criteria_env,browser)
        data=browser.get(f'/api/projects/{pid}').json()
        # Remove three optional template criteria: two required criteria remain.
        for c in data['criteria']:
            if c['code'] not in ('price_rub','performance_score'):
                response=browser.post(f"/api/projects/{pid}/criteria/{c['id']}/delete",data=form(browser,pid,confirmation=c['code'],reason='Проверка двух критериев'))
                assert response.status_code==200,response.text
        assert run(browser,pid).status_code==201
        values={f"score_{a['id']}":str(10+i) for i,a in enumerate(data['alternatives'])}
        for i in range(8):
            result=browser.post(f'/api/projects/{pid}/criteria',data=form(browser,pid,code=f'custom_{i}',name=f'Критерий {i}',unit='балл',direction='min' if i%2 else 'max',reason='Проверка десяти критериев',**values))
            assert result.status_code==201,result.text
        before=revision(browser,pid)
        result=browser.post(f'/api/projects/{pid}/criteria',data=form(browser,pid,code='eleventh',name='Одиннадцатый',unit='',direction='max',reason='Лишний критерий',**values))
        assert result.status_code==422 and revision(browser,pid)==before
        result=run(browser,pid);assert result.status_code==201,result.text
        rid=result.json()['result_ids']['marcos']
        assert len(browser.get(f'/api/projects/{pid}/calculations/{rid}').json()['weighting']['weights'])==10
        assert browser.get(f'/projects/{pid}/alternatives/{data["alternatives"][0]["id"]}/edit').text.count('name="custom_')==8
        # Exercise the ordinary laptop route with the current fields only.
        aid=data['alternatives'][0]['id'];a=data['alternatives'][0]
        body={k:str(v) for k,v in a['features'].items() if k in ('cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_gb','storage_type')}
        body.update(code=a['code'],name=a['name'],description='',performance_mode='auto',change_reason='Уточнение новых критериев',price_rub='35000')
        body.update({f'custom_{i}':str(12+i) for i in range(8)})
        saved=browser.post(f'/api/projects/{pid}/alternatives/{aid}',data=form(browser,pid,**body))
        assert saved.status_code==200,saved.text
        assert run(browser,pid).status_code==201


def test_metadata_edit_changes_direction_and_scores(criteria_env):
    browser,_=analyst(criteria_env)
    with browser:
        pid=copy_demo(criteria_env,browser);data=browser.get(f'/api/projects/{pid}').json()
        values={f"score_{a['id']}":str(10+i) for i,a in enumerate(data['alternatives'])}
        fields=dict(code='screen',name='Экран',unit='дюйм',direction='max',reason='Добавляем экран',**values)
        cid=browser.post(f'/api/projects/{pid}/criteria',data=form(browser,pid,**fields)).json()['criterion_id']
        initial=run(browser,pid).json()['result_ids']['marcos']
        response=browser.post(f'/api/projects/{pid}/criteria/{cid}/edit',data=form(browser,pid,**{**fields,'name':'Диагональ','direction':'min','reason':'Предпочтение компактному экрану'}))
        assert response.status_code==200,response.text
        output=run(browser,pid);assert output.status_code==201,output.text
        old=browser.get(f'/api/projects/{pid}/calculations/{initial}')
        assert old.status_code==200 and old.json()['is_stale']
        assert old.json()['criteria']['screen']['direction']=='max'
        # A late invalid score must roll back metadata and every preceding score.
        rev=revision(browser,pid)
        invalid={**fields,'name':'Ошибка изменения',next(iter(values)):'NaN'}
        failed=browser.post(f'/api/projects/{pid}/criteria/{cid}/edit',data=form(browser,pid,**invalid))
        assert failed.status_code==422 and revision(browser,pid)==rev
        assert next(c for c in browser.get(f'/api/projects/{pid}').json()['criteria'] if c['id']==cid)['name']=='Диагональ'


def test_predecessor_hash_replay(criteria_env):
    from uuid import uuid5,NAMESPACE_URL
    from psycopg.types.json import Jsonb
    from decision_core.compatibility import LEGACY_CORE
    from decision_store.calculations import digest
    browser,_=analyst(criteria_env)
    with browser:
        pid=copy_demo(criteria_env,browser)
        ids=run(browser,pid).json()['result_ids'];admin=criteria_env['admin']
        row=admin.execute('SELECT * FROM results WHERE id=%s',(ids['marcos'],)).fetchone()
        snapshot=deepcopy(row['input_snapshot']);snapshot['producer']['core_version']=LEGACY_CORE
        fingerprint=digest(snapshot);batch=str(uuid5(NAMESPACE_URL,'coursework:laptop:webcalc:v1:'+fingerprint))
        stored=admin.execute('SELECT input_snapshot FROM weight_sets WHERE id=%s',(row['params']['weight_set_id'],)).fetchone()['input_snapshot']
        stored=deepcopy(stored);stored['input']=snapshot
        historical={}
        with admin.transaction():
            wid=admin.execute("INSERT INTO weight_sets(project_id,method,input_snapshot,project_revision,created_by) VALUES(%s,'merec',%s,%s,%s) RETURNING id",
                (pid,Jsonb(stored),row['project_revision'],row['created_by'])).fetchone()['id']
            admin.execute('INSERT INTO weights(project_id,weight_set_id,criterion_id,value) SELECT project_id,%s,criterion_id,value FROM weights WHERE weight_set_id=%s',
                (wid,row['params']['weight_set_id']))
            for method,rid in ids.items():
                original=admin.execute('SELECT * FROM results WHERE id=%s',(rid,)).fetchone()
                params={**original['params'],'input_fingerprint':fingerprint,'batch_key':batch,'weight_set_id':wid}
                historical[method]=admin.execute("INSERT INTO results(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at) VALUES(%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,now()) RETURNING id",
                    (pid,row['created_by'],uuid5(NAMESPACE_URL,batch+':'+method),row['project_revision'],method,Jsonb(params),Jsonb(snapshot),Jsonb(original['output']),LEGACY_CORE)).fetchone()['id']
        response=browser.get(f"/api/projects/{pid}/calculations/{historical['marcos']}")
        assert response.status_code==200,response.text
        assert response.json()['verified']


def test_six_criteria_all_weight_methods_sensitivity_and_browser_worker(criteria_env,monkeypatch):
    from pathlib import Path
    import json
    from test_web_dematel import calculate as dematel_calculate
    from web_app.sensitivity_store import source
    from web_app.calculation_store import load_pair
    from web_app.sensitivity_math import verify_source
    from decision_core.compatibility import LEGACY_WEIGHTS,LEGACY_CORE
    browser,user=analyst(criteria_env)
    with browser:
        pid=copy_demo(criteria_env,browser);data=browser.get(f'/api/projects/{pid}').json()
        values={f"score_{a['id']}":str(13+i) for i,a in enumerate(data['alternatives'])}
        added=browser.post(f'/api/projects/{pid}/criteria',data=form(browser,pid,code='screen',name='Экран',unit='дюйм',direction='min',reason='Проверка направлений',**values))
        assert added.status_code==201,added.text
        items=browser.get(f'/api/projects/{pid}').json()['criteria']
        cells={f"cell_{a['id']}_{b['id']}":str(int(i<j)) for i,a in enumerate(items) for j,b in enumerate(items)}
        matrix=browser.post(f'/api/projects/{pid}/dematel/matrix',data=form(browser,pid,reason='Матрица для шести критериев',**cells))
        assert matrix.status_code==200,matrix.text
        dm=dematel_calculate(browser,pid,[user['id']]);assert dm.status_code==201,dm.text
        for method,subjective in [('manual','manual'),('dematel','dematel'),('combined','manual'),('combined','dematel')]:
            fields={'method':method,'v':'.5','reason':'Проверка новых критериев'}
            if method=='combined':fields.update(alpha='.5',subjective_source=subjective)
            if subjective=='manual':fields.update({f"weight_{c['id']}":str(i+1) for i,c in enumerate(items)})
            else:fields['dematel_id']=str(dm.json()['structure_id'])
            response=browser.post(f'/api/projects/{pid}/weights',data=form(browser,pid,**fields))
            assert response.status_code==201,response.text
            rid=response.json()['result_ids']['marcos']
            pair=browser.get(f'/api/projects/{pid}/calculations/{rid}')
            assert pair.status_code==200,pair.text
            assert len(pair.json()['weighting']['weights'])==6
            sensitivity=browser.post(f'/api/projects/{pid}/calculations/{rid}/sensitivity',data=form(browser,pid,step_percent='10'))
            assert sensitivity.status_code==201,sensitivity.text
            assert browser.get(f"/api/projects/{pid}/sensitivity/{sensitivity.json()['result_id']}").status_code==200
            payload=browser.get(f'/api/projects/{pid}/calculations/{rid}/preview').json()
            worker=(Path(__file__).resolve().parents[1]/'web_app/static/browser_preview_worker.js').read_text()
            python_source=worker.split('python.runPython(`',1)[1].split('`);',1)[0]
            import sys,types
            preview_module=types.ModuleType('decision_core.preview')
            exec(payload['sources']['preview.py'],preview_module.__dict__)
            monkeypatch.setitem(sys.modules,'decision_core.preview',preview_module)
            namespace={'input_json':json.dumps(payload)}
            previous_path=sys.path.copy()
            try:exec(python_source,namespace)
            finally:sys.path[:]=previous_path
            actual=json.loads(namespace['preview'](json.dumps(['screen',25,.7])))
            expected=browser.post(f'/api/projects/{pid}/calculations/{rid}/preview',data=dict(csrf=csrf(browser),criterion='screen',change='25',v='.7')).json()
            assert actual==expected
            # Legacy adapter recognizes the predecessor only, verifies all outputs.
            saved=source(load_pair(criteria_env['admin'],pid,rid))
            saved['input']['producer'].update(core_version=LEGACY_CORE,weight_adapter_version=LEGACY_WEIGHTS)
            for r in saved['records']:r['core_version']=LEGACY_WEIGHTS
            assert verify_source(saved)


@pytest.mark.parametrize('code',['csrf','cpu_cores','ram_gb','score_123','weight_12','cell_1_2'])
def test_reserved_criterion_names(code):
    with pytest.raises(HTTPException):
        validate(dict(code=code,name='Критерий',unit='балл',direction='max',reason='Проверка кода'))


def test_calculated_criterion_lifecycle(criteria_env):
    from test_alternative_import import confirm, file_bytes, payload, preview
    from test_web_alternatives import edit
    from web_app.criterion_store import CPU_CLOCK

    browser, _ = analyst(criteria_env)
    with browser:
        pid = copy_demo(criteria_env, browser)
        path = f'/api/projects/{pid}'
        alternatives = browser.get(path).json()['alternatives']
        aid = alternatives[0]['id']

        def score(alternative_id):
            rows = browser.get(f'{path}/alternatives/{alternative_id}').json()['scores']
            return next((row for row in rows if row['code'] == CPU_CLOCK), None)

        page = browser.get(f'/projects/{pid}/criteria/new?calculated=true')
        assert page.status_code == 200 and 'name="source" value="calc"' in page.text
        fields = dict(code=CPU_CLOCK, name='Суммарная базовая частота ядер',
                      unit='ГГц', direction='max', source='calc', reason='Проверка расчётного критерия')
        invalid = browser.post(f'{path}/criteria', data=form(browser, pid, **fields, **{f'score_{aid}': '1'}))
        assert invalid.status_code == 422
        added = browser.post(f'{path}/criteria', data=form(browser, pid, **fields))
        assert added.status_code == 201, added.text
        cid = added.json()['criterion_id']
        for row in alternatives:
            saved = score(row['id'])
            features = row['features']
            assert saved['value'] == pytest.approx(features['cpu_cores'] * features['cpu_frequency_ghz'])
            assert saved['source'] == 'calc' and saved['prediction_id'] is None

        template = browser.get(f'/projects/{pid}/alternatives/import/template.csv')
        assert template.status_code == 200 and CPU_CLOCK not in template.text
        checked = preview(browser, pid, file_bytes([payload(code='X7')]))
        assert checked.status_code == 200, checked.text
        result = confirm(browser, pid, checked.json()['token'])
        assert result.status_code == 201, result.text
        imported = result.json()['alternative_ids'][0]
        assert score(imported)['source'] == 'calc'

        result = edit(browser, pid, aid, cpu_cores='8', cpu_threads='16', cpu_frequency_ghz='3.1',
                      change_reason='Уточнение характеристик')
        assert result.status_code == 200, result.text
        assert score(aid)['value'] == pytest.approx(24.8)
        assert run(browser, pid).status_code == 201

        confirmation = dict(confirmation=CPU_CLOCK, reason='Проверка удаления и восстановления')
        for action in ('delete', 'restore'):
            result = browser.post(f'{path}/criteria/{cid}/{action}', data=form(browser, pid, **confirmation))
            assert result.status_code == 200, result.text
            if action == 'delete':
                result = edit(browser, pid, aid, cpu_frequency_ghz='2.5', change_reason='Уточнение частоты')
                assert result.status_code == 200, result.text
                assert score(aid)['value'] == pytest.approx(24.8)
        assert score(aid)['value'] == pytest.approx(20)
