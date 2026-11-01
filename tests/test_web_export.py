"""Snapshot-faithful XLSX, safe literal text, scoped downloads and historical decisions."""
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import pytest
pytest.importorskip('fastapi')
from test_web_alternatives import edits,web_env,client,signup,csrf,new_project,analyst
from test_web_decision_choice import setup,post as choose
from test_web_calculations import run as calculate
from web_app.export_rows import stamp
from web_app.export_xlsx import cell,S,C
from web_app.export_routes import MIME
NS={'s':S,'c':C}


def workbook(raw):
    z=ZipFile(BytesIO(raw));assert z.testzip() is None
    names=[e.attrib['name'] for e in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS)]
    sheets={}
    for i,name in enumerate(names,1):
        root=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));values={}
        assert not root.findall('.//s:f',NS)
        for c in root.findall('.//s:sheetData/s:row/s:c',NS):
            if c.attrib.get('t')=='inlineStr':value=''.join(c.itertext())
            else:
                node=c.find('s:v',NS);value=float(node.text) if node is not None else None
            values[c.attrib['r']]=value
        sheets[name]=values
    return z,sheets


@pytest.mark.parametrize('literal',['=HYPERLINK("https://example.com","x")','+SUM(1,2)','-1+2','@SUM(A1:A2)','\t=cmd|x!A1'])
def test_formula_like_text_is_literal(literal):
    c=ET.fromstring('<root xmlns="'+S+'">'+cell(literal,'A1','0')+'</root>')[0]
    assert c.attrib['t']=='inlineStr' and c.find('s:f',NS) is None
    assert ''.join(c.itertext())==literal


@pytest.mark.parametrize('value',['bad\x00text','bad\ud800text','a'*32768,float('nan'),float('inf')], ids=['null-character','surrogate','too-long','nan','infinity'])
def test_invalid_excel_values_rejected(value):
    with pytest.raises(ValueError):cell(value,'A1','0')


def test_omsk_date_conversion_and_unknown_training_date():
    assert stamp('2026-11-01T12:30:00+00:00')==datetime(2026,11,1,18,30)
    assert stamp(None)=='Не зафиксирована'
    c=ET.fromstring('<root xmlns="'+S+'">'+cell(stamp('2026-11-01T12:30:00Z'),'A1','0')+'</root>')[0]
    assert c.attrib.get('t') is None and float(c.find('s:v',NS).text)>46000


@pytest.fixture(scope='module')
def exports(edits):return edits


def get(browser,pid,rid,kind='calculations'):
    response=browser.get(f'/api/projects/{pid}/{kind}/{rid}/export.xlsx')
    assert response.status_code==200,response.text
    assert response.headers['content-type']==MIME
    assert response.headers['cache-control']=='no-store'
    assert 'attachment; filename="laptop_project_' in response.headers['content-disposition']
    return response,workbook(response.content)


def test_snapshot_report_numeric_metrics_diagram_and_no_database_writes(exports):
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    def counts():return [db.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] for t in ['results','weight_sets','decision_choices','audit_events']]
    with a,l:
        before=counts();r,(z,s)=get(a,pid,ids['marcos']);assert counts()==before
        assert list(s)==['Отчёт','Рейтинги','Матрица','Веса','Промежуточные','ML и источники','Решение ЛПР']
        assert s['Рейтинги']['A5']=='A4' and s['Рейтинги']['D5']==pytest.approx(.6398967456876088)
        assert s['Рейтинги']['A6']=='A2' and s['Рейтинги']['E6']==1
        assert s['Матрица']['C5']==35000
        assert sum(s['Веса'][f'E{i}'] for i in range(5,10))==pytest.approx(1)
        assert 'ещё не сохранено' in s['Отчёт']['B20']
        metrics={s['ML и источники'].get(f'B{i}'):s['ML и источники'].get(f'C{i}') for i in range(5,200)}
        assert metrics['metrics.test_mae']==pytest.approx(40.12060001406467)
        assert metrics['trained_at']=='Не зафиксирована'
        assert metrics['row_count']==2000 and metrics['split.train_rows']==1608 and metrics['split.test_rows']==392
        chart=ET.fromstring(z.read('xl/drawings/charts/chart1.xml'))
        for ser,col in zip(chart.findall('.//c:ser',NS),['C','E']):
            assert ser.find('c:val/c:numRef/c:f',NS).text==f"'Рейтинги'!${col}$5:${col}$9"
            assert int(ser.find('c:val/c:numRef/c:numCache/c:ptCount',NS).attrib['val'])==5
        assert not any('vbaProject' in n or 'externalLink' in n for n in z.namelist())
        for n in z.namelist():
            if n.endswith('.rels'):assert 'TargetMode="External"' not in z.read(n).decode()
        get(a,pid,ids['vikor'])
        assert 'Скачать отчёт Excel' in a.get(f'/projects/{pid}/calculations/{ids["marcos"]}').text


def test_choice_report_and_related_calculation_only(exports):
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id']
    with a,l:
        cid=choose(l,pid,ids['marcos']).json()['choice_id']
        _,(_,s)=get(l,pid,cid,'choices')
        assert s['Решение ЛПР']['B5']==cid and s['Решение ЛПР']['B6']==lu['name']
        assert s['Решение ЛПР']['B10']=='A2' and s['Решение ЛПР']['B13']==2 and s['Решение ЛПР']['B14']==1
        _,(_,paired)=get(a,pid,ids['marcos']);assert paired['Решение ЛПР']['B5']==cid
        another=calculate(a,pid,v='.25').json()['result_ids']['marcos']
        _,(_,other)=get(a,pid,another);assert 'не сохранено' in other['Решение ЛПР']['B5']
        assert 'Скачать отчёт Excel с решением ЛПР' in l.get(f'/projects/{pid}/choices/{cid}').text


def test_analyst_expert_lpr_read_access_and_cross_project_rejection(exports):
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id']
    with a,l,client(env) as e,client(env) as outsider,client(env) as anonymous:
        u=signup(e);signup(outsider)
        a.post(f'/api/projects/{pid}/members',data={'csrf':csrf(a),'email':u['email'],'role':'expert'})
        for who in [a,l,e]:get(who,pid,ids['marcos'])
        path=f'/api/projects/{pid}/calculations/{ids["marcos"]}/export.xlsx'
        assert outsider.get(path).status_code==404 and anonymous.get(path).status_code==401
        other=new_project(a)
        assert a.get(f'/api/projects/{other}/calculations/{ids["marcos"]}/export.xlsx').status_code==404
        assert a.get(f'/api/projects/{pid}/choices/999999/export.xlsx').status_code==404


def test_history_uses_original_values_after_live_change_and_source_removal(exports):
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    with a,l:
        cid=choose(l,pid,ids['marcos']).json()['choice_id'];_,(_,old)=get(l,pid,cid,'choices')
        db.execute("UPDATE projects SET goal='Новая текущая цель' WHERE id=%s",(pid,))
        db.execute("UPDATE alternatives SET name='Изменённое текущее имя' WHERE project_id=%s AND code='A2'",(pid,))
        _,(_,historic)=get(l,pid,cid,'choices')
        assert historic['Отчёт']['B6']==old['Отчёт']['B6']
        assert historic['Решение ЛПР']['B11']==old['Решение ЛПР']['B11']
        assert historic['Матрица']==old['Матрица'] and historic['Рейтинги']==old['Рейтинги']
        assert 'Данные изменились' in historic['Отчёт']['B14']
        db.execute('DELETE FROM results WHERE id=%s',(ids['vikor'],))
        _,(_,independent)=get(l,pid,cid,'choices');assert independent['Рейтинги']==old['Рейтинги']


def test_budget_exclusion_changes_chart_range_and_preserves_excluded_scores(exports):
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    before=db.execute('SELECT budget FROM projects WHERE id=%s',(pid,)).fetchone()['budget']
    with a,l:
        try:
            db.execute('UPDATE projects SET budget=60000 WHERE id=%s',(pid,))
            rid=calculate(a,pid).json()['result_ids']['marcos'];_,(z,s)=get(a,pid,rid)
            chart=ET.fromstring(z.read('xl/drawings/charts/chart1.xml'))
            assert chart.find('.//c:cat/c:strRef/c:f',NS).text=="'Рейтинги'!$A$5:$A$7"
            assert chart.find('.//c:cat/c:strRef/c:strCache/c:ptCount',NS).attrib['val']=='3'
            assert 'A4' in s['Матрица'].values() and 'A3' in s['Матрица'].values()
            assert set(s['Рейтинги'].get(f'A{i}') for i in range(5,8))=={'A1','A2','A5'}
        finally:db.execute('UPDATE projects SET budget=%s WHERE id=%s',(before,pid))


def test_export_all_weight_methods_and_text_formula_injection(exports):
    from test_web_weights import post as weighted
    from test_web_dematel import calculate as dematel,source_id
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    with a,l:
        db.execute('UPDATE projects SET title=%s WHERE id=%s',('=HYPERLINK("https://example.com","test")',pid))
        did=dematel(a,pid,[source_id(env)]).json()['structure_id']
        for method in ['merec','manual','dematel','combined']:
            extra={'dematel_id':str(did)} if method=='dematel' else {}
            r=weighted(a,pid,env,method=method,**extra);assert r.status_code in (200,201),r.text
            _,(_,s)=get(a,pid,r.json()['result_ids']['marcos'])
            assert s['Отчёт']['B5'].startswith('=HYPERLINK(')
            assert s['Веса']['E5'] is not None
            if method=='manual':assert not any('merec / normalized'==v for v in s['Промежуточные'].values())


def test_corrupt_choice_rejected_before_export(exports):
    from psycopg.types.json import Jsonb
    env=exports;a,l,au,lu,ids=setup(env);pid=env['demo_id'];db=env['admin']
    with a,l:
        cid=choose(l,pid,ids['marcos']).json()['choice_id']
        old=db.execute('SELECT alternative_snapshot FROM decision_choices WHERE id=%s',(cid,)).fetchone()['alternative_snapshot']
        bad=dict(old);bad['input_fingerprint']='wrong'
        db.execute('UPDATE decision_choices SET alternative_snapshot=%s WHERE id=%s',(Jsonb(bad),cid))
        try:
            assert l.get(f'/api/projects/{pid}/choices/{cid}/export.xlsx').status_code==422
            assert a.get(f'/api/projects/{pid}/calculations/{ids["marcos"]}/export.xlsx').status_code==422
        finally:db.execute('UPDATE decision_choices SET alternative_snapshot=%s WHERE id=%s',(Jsonb(old),cid))
