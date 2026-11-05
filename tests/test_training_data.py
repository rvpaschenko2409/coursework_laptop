"""Quality/provenance and atomic training upload using the limited PostgreSQL role."""
from pathlib import Path
import json
import pytest

from web_app import training_data_store as store
from web_app.training_data_routes import PreviewCache
from web_app.alternative_import_files import csv_bytes, read_table
from fastapi import HTTPException
from test_alternative_import import xlsx_rows
from test_web_auth import web_env, client, signup, csrf, new_project
from web_app.database import grant_criteria_permissions

META={'name':'Учебные измерения','origin':'manual','source_url':'','license':'Собственные учебные данные',
      'seed':'','generator_version':'','reason':'Проверка загрузки набора'}
HEADERS=store.HEADERS


def records(n=30):
    return [dict(cpu_cores='4',cpu_threads='8',cpu_frequency_ghz=str(2+i*.01),ram_gb='16',
                 storage_gb='512',storage_type='SSD',performance_score=str(1000+i)) for i in range(n)]


def file(rows=None,headers=None):
    headers=headers or HEADERS
    return csv_bytes(headers,[[r.get(h,'') for h in headers] for r in (records() if rows is None else rows)])


def test_quality_layers_duplicates_invalid_ranges_and_no_invented_target():
    rows=records();rows.extend([dict(rows[0]),dict(rows[1],ram_gb=''),dict(rows[2],performance_score='NaN')])
    r=store.inspect(file(rows),'data.csv',META);q=r['quality']
    assert r['valid'] and q['raw_rows']==33 and q['staging_rows']==30 and q['excluded_rows']==3
    assert q['missing']['ram_gb']==1 and q['duplicate_rows']==1 and q['invalid_cells']['performance_score']==1
    assert q['ranges']['performance_score']['max']==1029 and q['configurations']==30
    assert r['raw'][31]['data']['ram_gb']==''
    assert r['staging'][0]['data']['configuration_id']==r['staging'][0]['provenance']['configuration_id']
    json.dumps(r,allow_nan=False)


def test_legacy_synthetic_csv_and_group_identity_ignore_supplied_id():
    r=store.inspect((Path(__file__).resolve().parents[1]/'data/synthetic/laptops_synthetic.csv').read_bytes(),'data.csv',
                    dict(META,origin='synthetic',seed='42',generator_version='generate_synthetic.py'))
    assert r['valid'] and len(r['raw'])==2000 and len(r['staging'])==2000
    assert r['quality']['configurations']==100 and r['quality']['source_target']=='synthetic_score'
    assert r['quality']['origin']=='synthetic' and not r['quality']['real_world_accuracy_established']
    rows=records();rows[1]=dict(rows[0],performance_score='1001',configuration_id='different')
    rows[0]['configuration_id']='one'
    r=store.inspect(file(rows,HEADERS+['configuration_id']),'x.csv',META)
    assert r['staging'][0]['data']['configuration_id']==r['staging'][1]['data']['configuration_id']


@pytest.mark.parametrize('change',[
    {'origin':'open'},{'origin':'open','source_url':'file:///x'},
    {'origin':'synthetic','seed':'-1','generator_version':'x'},
    {'origin':'synthetic','seed':'42','generator_version':''},
    {'seed':'42'},{'license':''},{'source_url':'https://user:password@example.com'},{'origin':'unknown'}],
    ids=['open_source_required','no_file_url','negative_seed','generator_required','no_false_seed','license_required','no_url_password','known_origin'])
def test_passport_validation(change):
    with pytest.raises(ValueError):store.inspect(file(),'data.csv',dict(META,**change))


def test_xlsx_over_256_rows_and_alternative_limit_preserved():
    rows=records(300)
    content=xlsx_rows(rows,HEADERS)
    r=store.inspect(content,'data.xlsx',META)
    assert r['valid'] and len(r['staging'])==300
    with pytest.raises(ValueError):read_table(file(records(16)),'x.csv',HEADERS)


def test_bounds_headers_ambiguous_target_and_sample_ids():
    with pytest.raises(ValueError):store.inspect(file(records(10001)),'x.csv',META)
    with pytest.raises(ValueError):store.inspect(file(headers=HEADERS+['synthetic_score']),'x.csv',META)
    r=store.inspect(file(records(19)),'x.csv',META)
    assert not r['valid']
    rows=records();rows=[dict(r,sample_id='same') for r in rows]
    r=store.inspect(file(rows,HEADERS+['sample_id']),'x.csv',META)
    assert not r['valid'] and r['quality']['repeated_sample_ids']==29
    with pytest.raises(ValueError):store.inspect(file(headers=HEADERS+['data_origin']),'x.csv',META)


def test_preview_ownership_expiry_capacity(monkeypatch):
    cache=PreviewCache();r=store.inspect(file(),'x.csv',META)
    token=cache.put(1,2,3,r)
    with pytest.raises(HTTPException):cache.get(token,1,9)
    assert cache.get(token,1,2)[0]==3
    for _ in range(4):cache.put(1,2,3,r)
    with pytest.raises(HTTPException):cache.get(token,1,2)
    cache=PreviewCache();token=cache.put(1,2,3,r)
    import time
    moment=time.monotonic()
    monkeypatch.setattr('web_app.training_data_routes.time.monotonic',lambda:moment+901)
    with pytest.raises(HTTPException):cache.get(token,1,2)


@pytest.fixture(scope='module')
def training(web_env):
    admin=web_env['admin'];schema=admin.execute('SELECT current_schema() AS s').fetchone()['s']
    grant_criteria_permissions(admin,schema,web_env['database'].expected_role)
    return web_env


def preview(browser,pid,content=None,**changes):
    data={**META,'csrf':csrf(browser),'revision':str(browser.get(f'/api/projects/{pid}').json()['project']['revision']),**changes}
    return browser.post(f'/api/projects/{pid}/training-data/preview',data=data,files={'file':('data.csv',file() if content is None else content)})


def confirm(browser,pid,token,**changes):
    return browser.post(f'/api/projects/{pid}/training-data/confirm',data={'csrf':csrf(browser),'token':token,**changes})


def test_upload_confirm_atomic_idempotent_and_models_untouched(training):
    with client(training) as browser:
        user=signup(browser);pid=new_project(browser)
        assert browser.get(f'/projects/{pid}/training-data').status_code==200
        review=preview(browser,pid);assert review.status_code==200,review.text
        assert browser.get(f'/api/projects/{pid}/training-data').json()['datasets']==[]
        result=confirm(browser,pid,review.json()['token']);assert result.status_code==201,result.text
        did=result.json()['dataset_id']
        listing=browser.get(f'/api/projects/{pid}/training-data').json()['datasets']
        assert len(listing)==1 and listing[0]['id']==did
        admin=training['admin']
        layers=admin.execute('SELECT layer::text,count(*) AS n FROM dataset_rows WHERE dataset_id=%s GROUP BY layer',(did,)).fetchall()
        assert {r['layer']:r['n'] for r in layers}=={'raw':30,'staging':30}
        assert admin.execute('SELECT count(*) AS n FROM ml_models WHERE project_id=%s',(pid,)).fetchone()['n']==0
        assert admin.execute('SELECT count(*) AS n FROM dataset_groups WHERE dataset_id=%s',(did,)).fetchone()['n']==0
        assert confirm(browser,pid,review.json()['token']).status_code==200
        assert browser.get(f'/projects/{pid}/training-data').status_code==200
        event=admin.execute("SELECT details FROM audit_events WHERE project_id=%s AND action='web_training_data_upload'",(pid,)).fetchone()['details']
        assert event['dataset_id']==did and event['model_trained'] is False


def test_upload_csrf_scope_expert_and_preview_actor(training):
    with client(training) as owner,client(training) as expert,client(training) as stranger:
        signup(owner);u=signup(expert);signup(stranger);pid=new_project(owner)
        owner.post(f'/api/projects/{pid}/members',data={'email':u['email'],'role':'expert','csrf':csrf(owner)})
        assert expert.get(f'/projects/{pid}/training-data').status_code==403
        assert stranger.get(f'/projects/{pid}/training-data').status_code==404
        assert preview(owner,pid,csrf='bad').status_code==403
        token=preview(owner,pid).json()['token']
        owner.post(f'/api/projects/{pid}/members',data={'email':u['email'],'role':'analyst','csrf':csrf(owner)})
        assert confirm(expert,pid,token).status_code==409


def test_revision_and_bad_confirmation_do_not_write(training):
    with client(training) as browser:
        signup(browser);pid=new_project(browser);token=preview(browser,pid).json()['token']
        training['admin'].execute('UPDATE projects SET title=title||%s WHERE id=%s',(' changed',pid))
        assert confirm(browser,pid,token).status_code==409
        assert confirm(browser,pid,'bad').status_code==409
        assert browser.get(f'/api/projects/{pid}/training-data').json()['datasets']==[]
        assert preview(browser,pid,file(records(19))).status_code==422


def test_database_failure_rolls_back_all_layers(training,monkeypatch):
    original=store.audit
    def fail(*args,**kwargs):raise RuntimeError('simulated failure after row inserts')
    with client(training) as browser:
        signup(browser);pid=new_project(browser);token=preview(browser,pid).json()['token']
        monkeypatch.setattr(store,'audit',fail)
        with pytest.raises(RuntimeError):confirm(browser,pid,token)
        assert browser.get(f'/api/projects/{pid}/training-data').json()['datasets']==[]
        monkeypatch.setattr(store,'audit',original)
        assert confirm(browser,pid,token).status_code==201


def test_html_escaping_and_templates(training):
    with client(training) as browser:
        signup(browser);pid=new_project(browser)
        for extension in ('csv','xlsx'):
            result=browser.get(f'/projects/{pid}/training-data/template.{extension}')
            assert result.status_code==200 and result.content
        data={**META,'name':'<script>alert(1)</script>','csrf':csrf(browser),'revision':str(browser.get(f'/api/projects/{pid}').json()['project']['revision'])}
        result=browser.post(f'/projects/{pid}/training-data/preview',data=data,files={'file':('data.csv',file())})
        assert result.status_code==200,result.text
        assert '<script>alert(1)</script>' not in result.text and '&lt;script&gt;' in result.text
