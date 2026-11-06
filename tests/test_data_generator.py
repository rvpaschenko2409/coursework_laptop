"""Reproducible generation, declared defects and protected atomic persistence."""
from collections import Counter
import csv
from io import StringIO
from pathlib import Path
import time

import pytest

from web_app import data_generator as g
from web_app.training_data_routes import PreviewCache
from test_web_auth import web_env,client,signup,csrf,new_project
from web_app.database import grant_criteria_permissions


def test_zero_defects_exactly_reproduces_original_seed42_records():
    original=list(csv.DictReader(StringIO((Path(__file__).resolve().parents[1]/'data/synthetic/laptops_synthetic.csv').read_text())))
    review=g.build(g.DEFAULTS)
    assert review['valid']
    assert [r['data'] for r in review['raw']]==original
    assert review['quality']['staging_rows']==2000 and review['quality']['configurations']==100
    assert review['generation']['noise']=={'distribution':'lognormal','log_mean':-0.0012500000000000002,'log_std':0.05}
    assert all(r['provenance']['origin']=='synthetic' and r['provenance']['seed']==42 and r['provenance']['defect'] is None for r in review['raw'])


def test_reproducibility_parameters_and_title_do_not_create_new_dataset():
    a=g.build(dict(g.DEFAULTS,n_rows='300',missing_pct='5',outlier_pct='5',duplicate_pct='10'))
    b=g.build(dict(g.DEFAULTS,n_rows='300',missing_pct='5',outlier_pct='5',duplicate_pct='10',name='Иное название',reason='Другая цель'))
    c=g.build(dict(g.DEFAULTS,n_rows='300',seed='43',missing_pct='5',outlier_pct='5',duplicate_pct='10'))
    assert a['file_sha256']==b['file_sha256']!=c['file_sha256']
    assert a['raw']==b['raw'] and a['staging']==b['staging']
    assert a['generation']['rng']=='PCG64' and len(a['generation']['generator_code_sha256'])==64


@pytest.mark.parametrize('n,missing,outlier,duplicate,expected',[
    ('2000','5','5','10',{'missing':100,'outlier':100,'duplicate':200}),
    ('101','0.5','0.5','0.5',{'missing':1,'outlier':1,'duplicate':1}),
    ('100','30','30','0',{'missing':30,'outlier':30,'duplicate':0}),
    ('100','0','30','30',{'missing':0,'outlier':30,'duplicate':30}),
],ids=['normal_defects','half_up_rounding','sixty_percent','thirty_percent_duplicates'])
def test_disjoint_defects_raw_and_clean_layers(n,missing,outlier,duplicate,expected):
    r=g.build(dict(g.DEFAULTS,n_rows=n,missing_pct=missing,outlier_pct=outlier,duplicate_pct=duplicate))
    q=r['quality'];assert r['valid']
    assert q['raw_rows']==int(n) and q['staging_rows']==int(n)-sum(expected.values())
    assert q['excluded_rows']==sum(expected.values()) and q['duplicate_rows']==expected['duplicate']
    assert q['injected_defects']==expected
    assert sum(q['missing'].values())==expected['missing']
    kinds=Counter(row['provenance']['defect']['kind'] for row in r['raw'] if row['provenance']['defect'])
    assert {k:kinds[k] for k in expected}==expected
    assert all(row['provenance']['defect'] is None for row in r['staging'])
    assert all(0.1<=float(row['data']['cpu_frequency_ghz'])<=10 for row in r['staging'])
    for row in r['raw']:
        defect=row['provenance']['defect']
        if defect and defect['kind']=='outlier':assert float(row['data']['cpu_frequency_ghz'])>10
        if defect and defect['kind']=='duplicate':assert row['data']==r['raw'][defect['source_row_number']-1]['data']
    # Source row numbers and duplicate sample IDs are preserved, not regenerated during cleanup.
    assert all(row['data']['sample_id']==str(row['provenance']['source_sample_id']) for row in r['staging'])


@pytest.mark.parametrize('changes',[
    {'n_rows':'99'},{'n_rows':'10001'},{'n_rows':'100.5'},
    {'seed':'-1'},{'seed':'2147483648'},{'seed':'1.1'},
    {'missing_pct':'31'},{'outlier_pct':'-1'},{'duplicate_pct':'NaN'},
    {'missing_pct':'30','outlier_pct':'30','duplicate_pct':'1'},
    {'noise_sigma':'Infinity'},{'noise_sigma':'0.6'},{'name':'x'},{'reason':''},
],ids=['small_count','large_count','fraction_count','negative_seed','large_seed','fraction_seed',
        'large_missing','negative_outlier','nan_duplicate','sum_limit','infinite_noise','large_noise','short_name','no_reason'])
def test_bad_parameters(changes):
    with pytest.raises(ValueError):g.build(dict(g.DEFAULTS,**changes))


def test_zero_noise_same_configuration_same_target_and_group():
    r=g.build(dict(g.DEFAULTS,n_rows='400',noise_sigma='0'))
    observed={}
    for row in r['staging']:
        data=row['data'];group=data['configuration_id'];value=data['performance_score']
        assert observed.setdefault(group,value)==value


def test_maximum_volume_fits_bounded_preview():
    r=g.build(dict(g.DEFAULTS,n_rows='10000',missing_pct='5',outlier_pct='5',duplicate_pct='10'))
    assert r['valid'] and r['quality']['raw_rows']==10000 and r['quality']['staging_rows']==8000
    cache=PreviewCache();token=cache.put(1,2,3,r)
    assert cache.get(token,1,2)[1]['file_sha256']==r['file_sha256']


@pytest.fixture(scope='module')
def generator_env(web_env):
    admin=web_env['admin'];schema=admin.execute('SELECT current_schema() AS s').fetchone()['s']
    grant_criteria_permissions(admin,schema,web_env['database'].expected_role)
    return web_env


def preview(browser,pid,**fields):
    revision=browser.get(f'/api/projects/{pid}').json()['project']['revision']
    return browser.post(f'/api/projects/{pid}/training-data/generate/preview',data={
        **g.DEFAULTS,'n_rows':'300','csrf':csrf(browser),'revision':str(revision),**fields})


def confirm(browser,pid,token,**fields):
    return browser.post(f'/api/projects/{pid}/training-data/confirm',data={'csrf':csrf(browser),'token':token,**fields})


def test_generate_preview_confirm_idempotent_provenance_and_no_training(generator_env):
    env=generator_env
    with client(env) as browser:
        signup(browser);pid=new_project(browser)
        assert browser.get(f'/projects/{pid}/training-data/generate').status_code==200
        r=preview(browser,pid,missing_pct='5',outlier_pct='5',duplicate_pct='10')
        assert r.status_code==200,r.text
        q=r.json()['quality'];assert q['raw_rows']==300 and q['staging_rows']==240
        assert browser.get(f'/api/projects/{pid}/training-data').json()['datasets']==[]
        result=confirm(browser,pid,r.json()['token']);assert result.status_code==201,result.text
        did=result.json()['dataset_id']
        listing=browser.get(f'/api/projects/{pid}/training-data').json()['datasets']
        assert len(listing)==1 and listing[0]['id']==did
        params=listing[0]['generation_params'];assert params['generation']['defect_counts']=={'missing':15,'outlier':15,'duplicate':30}
        assert listing[0]['generator_version']==g.VERSION and listing[0]['origin']=='synthetic'
        admin=env['admin']
        rows=admin.execute('SELECT layer::text,provenance FROM dataset_rows WHERE dataset_id=%s',(did,)).fetchall()
        assert Counter(r['layer'] for r in rows)=={'raw':300,'staging':240}
        assert all(r['provenance']['generator_version']==g.VERSION and r['provenance']['seed']==42 for r in rows)
        assert sum(r['layer']=='raw' and r['provenance']['defect'] is not None for r in rows)==60
        assert admin.execute('SELECT count(*) AS n FROM ml_models WHERE project_id=%s',(pid,)).fetchone()['n']==0
        assert admin.execute('SELECT count(*) AS n FROM dataset_groups WHERE dataset_id=%s',(did,)).fetchone()['n']==0
        assert confirm(browser,pid,r.json()['token']).status_code==200
        second=preview(browser,pid,missing_pct='5',outlier_pct='5',duplicate_pct='10',name='Переименованный набор')
        assert confirm(browser,pid,second.json()['token']).json()['dataset_id']==did
        assert len(browser.get(f'/api/projects/{pid}/training-data').json()['datasets'])==1
        assert browser.get(f'/projects/{pid}/training-data').status_code==200


def test_access_csrf_revision_and_shared_cache_actor(generator_env):
    with client(generator_env) as owner,client(generator_env) as expert,client(generator_env) as stranger:
        signup(owner);user=signup(expert);signup(stranger);pid=new_project(owner)
        owner.post(f'/api/projects/{pid}/members',data={'email':user['email'],'role':'expert','csrf':csrf(owner)})
        assert expert.get(f'/projects/{pid}/training-data/generate').status_code==403
        assert stranger.get(f'/projects/{pid}/training-data/generate').status_code==404
        assert preview(owner,pid,csrf='bad').status_code==403
        assert preview(expert,pid).status_code==403
        token=preview(owner,pid).json()['token']
        owner.post(f'/api/projects/{pid}/members',data={'email':user['email'],'role':'analyst','csrf':csrf(owner)})
        assert confirm(expert,pid,token).status_code==409
        assert confirm(owner,pid,token,csrf='bad').status_code==403
        token=preview(owner,pid).json()['token']
        generator_env['admin'].execute('UPDATE projects SET title=title||%s WHERE id=%s',(' changed',pid))
        assert confirm(owner,pid,token).status_code==409
        assert browser_dataset_count(owner,pid)==0


def browser_dataset_count(browser,pid):return len(browser.get(f'/api/projects/{pid}/training-data').json()['datasets'])


def test_bad_input_html_escaping_and_body_limit(generator_env):
    with client(generator_env) as browser:
        signup(browser);pid=new_project(browser)
        assert preview(browser,pid,missing_pct='31').status_code==422
        assert preview(browser,pid,extra='unknown').status_code==422
        assert preview(browser,pid,csrf=csrf(browser),reason='x'*9000).status_code==413
        revision=browser.get(f'/api/projects/{pid}').json()['project']['revision']
        data={**g.DEFAULTS,'n_rows':'100','name':'<script>alert(1)</script>','csrf':csrf(browser),'revision':str(revision)}
        result=browser.post(f'/projects/{pid}/training-data/generate/preview',data=data)
        assert result.status_code==200,result.text
        assert '<script>alert(1)</script>' not in result.text and '&lt;script&gt;' in result.text
        assert 'Внесено:' in result.text and 'laptop-toy-score-v1' in result.text


def test_generated_save_failure_rolls_back_and_can_retry(generator_env,monkeypatch):
    from web_app import training_data_store as store
    original=store.audit
    def fail(*args,**kwargs):raise RuntimeError('simulated generated dataset persistence failure')
    with client(generator_env) as browser:
        signup(browser);pid=new_project(browser);token=preview(browser,pid).json()['token']
        monkeypatch.setattr(store,'audit',fail)
        with pytest.raises(RuntimeError):confirm(browser,pid,token)
        assert browser_dataset_count(browser,pid)==0
        monkeypatch.setattr(store,'audit',original)
        assert confirm(browser,pid,token).status_code==201
