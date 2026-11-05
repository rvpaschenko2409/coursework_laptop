"""Bounded file parsing and atomic role-protected laptop import against PostgreSQL."""
from io import BytesIO
import csv
import json
import time
import zipfile
from pathlib import Path
import pytest
from fastapi import HTTPException
from web_app.alternative_import_files import read_table,csv_bytes,xlsx_bytes,MAX_FILE
from web_app.alternative_import_store import encode,decode
from test_web_auth import web_env,client,signup,csrf,new_project
from test_web_alternatives import edits,analyst,BASE
from test_web_calculations import revision
from web_app.database import grant_criteria_permissions

HEADERS=['code','name','description','cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_gb','storage_type','price_rub','mass_kg','battery_hours','warranty_months']


def payload(**changes):
    return {k:str(v) for k,v in {**BASE,**changes}.items() if k in HEADERS}


def file_bytes(rows=None,headers=None):
    headers=headers or HEADERS
    return csv_bytes(headers,[[r.get(h,'') for h in headers] for r in (rows or [payload()])])


def xlsx_rows(rows,headers=None):
    headers=headers or HEADERS
    content=xlsx_bytes(headers)
    from xml.etree import ElementTree as ET
    namespace='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    original=zipfile.ZipFile(BytesIO(content));output=BytesIO()
    root=ET.fromstring(original.read('xl/worksheets/sheet1.xml'));table=root.find('{'+namespace+'}sheetData')
    for index,row in enumerate(rows,2):
        rr=ET.SubElement(table,'{'+namespace+'}row',r=str(index))
        for j,header in enumerate(headers):
            c=ET.SubElement(rr,'{'+namespace+'}c',r=f'{chr(65+j)}{index}',t='inlineStr')
            ET.SubElement(ET.SubElement(c,'{'+namespace+'}is'),'{'+namespace+'}t').text=row.get(header,'')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for n in original.namelist():z.writestr(n,ET.tostring(root) if n=='xl/worksheets/sheet1.xml' else original.read(n))
    return output.getvalue()


def modify_zip(data,path,replacement):
    source=zipfile.ZipFile(BytesIO(data));out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for n in source.namelist():z.writestr(n,replacement(source.read(n)) if n==path else source.read(n))
    return out.getvalue()


def test_csv_utf8_and_windows1251_decimal_comma():
    source=file_bytes([payload(mass_kg='1,75')])
    for content in (source,source.decode('utf-8-sig').encode('cp1251')):
        rows,label=read_table(content,'data.csv',[h for h in HEADERS if h!='description'])
        assert rows[0][1]['mass_kg']=='1,75' and rows[0][1]['name']=='Новый учебный ноутбук'


def test_xlsx_text_and_numeric_cells():
    content=xlsx_rows([payload()])
    rows,label=read_table(content,'data.xlsx',[h for h in HEADERS if h!='description'])
    assert rows[0][1]['name']=='Новый учебный ноутбук' and label=='Excel XLSX'
    assert rows[0][0]==2


@pytest.mark.parametrize('name,content', ids=['old_xls','empty','broken_xlsx','oversized','duplicate_headers'], argvalues=[('x.xls',b'old'),('x.csv',b''),('x.xlsx',b'broken'),('x.csv',b'x'*(MAX_FILE+1)),('x.csv',b'code;code\nA;B')])
def test_bad_files(name,content):
    with pytest.raises(ValueError):read_table(content,name,HEADERS)


def test_headers_extra_cells_and_row_limits():
    with pytest.raises(ValueError):read_table(file_bytes(headers=HEADERS+['performance_score']),'a.csv',HEADERS)
    with pytest.raises(ValueError):read_table(file_bytes([payload(code=f'X{i}') for i in range(16)]),'a.csv',HEADERS)
    with pytest.raises(ValueError):read_table(file_bytes()+b';extra;too;many;values;for;the;headers;more;extra;extra;extra;extra;extra;extra;extra;extra\n','a.csv',HEADERS)


def test_formula_external_links_and_xml_entities_rejected():
    content=xlsx_rows([payload()])
    formula=modify_zip(content,'xl/worksheets/sheet1.xml',lambda s:s.replace(b'<ns0:t>X1</ns0:t>',b'<ns0:t>X1</ns0:t>').replace(b'</ns0:c>',b'<ns0:f>1+1</ns0:f></ns0:c>',1))
    with pytest.raises(ValueError,match='формулы'):read_table(formula,'x.xlsx',HEADERS)
    external=modify_zip(content,'xl/_rels/workbook.xml.rels',lambda s:s.replace(b'Target="styles.xml"',b'TargetMode="External" Target="https://example.invalid/test"'))
    with pytest.raises(ValueError,match='Внешние'):read_table(external,'x.xlsx',HEADERS)
    entity=modify_zip(content,'xl/workbook.xml',lambda s:s.replace(b'<workbook',b'<!DOCTYPE x [<!ENTITY x "hello">]><workbook'))
    with pytest.raises(ValueError,match='DTD'):read_table(entity,'x.xlsx',HEADERS)


def test_signed_preview_rejects_tampering():
    key=b'x'*32;token=encode({'version':1,'rows':[]},key)
    assert decode(token,key)['rows']==[]
    for bad in ('',token+'x',token.replace(token[0],'!',1)):
        with pytest.raises(HTTPException):decode(bad,key)


@pytest.fixture(scope='module')
def imports(edits):
    schema=edits['admin'].execute('SELECT current_schema() AS s').fetchone()['s']
    grant_criteria_permissions(edits['admin'],schema,edits['database'].expected_role)
    return edits


def preview(browser,pid,content=None,name='laptops.csv',**extra):
    data={'csrf':csrf(browser),'revision':str(revision(browser,pid)),'reason':'Импорт учебных предложений',**extra}
    return browser.post(f'/api/projects/{pid}/alternatives/import/preview',data=data,files={'file':(name,file_bytes() if content is None else content)})


def confirm(browser,pid,token,**changes):
    return browser.post(f'/api/projects/{pid}/alternatives/import/confirm',data={'csrf':csrf(browser),'token':token,**changes})


def test_draft_preview_confirm_retry_and_templates(imports):
    with client(imports) as browser:
        signup(browser);pid=new_project(browser)
        assert browser.get(f'/projects/{pid}/alternatives/import').status_code==200
        for ext in ['csv','xlsx']:
            template=browser.get(f'/projects/{pid}/alternatives/import/template.{ext}')
            assert template.status_code==200
            assert template.headers['cache-control']=='no-store'
        before=revision(browser,pid)
        checked=preview(browser,pid,file_bytes([payload(code='X1'),payload(code='X2')]))
        assert checked.status_code==200,checked.text
        assert checked.json()['valid'] and all(r['warnings'] for r in checked.json()['rows'])
        assert revision(browser,pid)==before
        assert not browser.get(f'/api/projects/{pid}').json()['alternatives']
        token=checked.json()['token'];result=confirm(browser,pid,token)
        assert result.status_code==201,result.text
        assert len(result.json()['alternative_ids'])==2
        repeat=confirm(browser,pid,token);assert repeat.status_code==200,repeat.text
        assert repeat.json()['alternative_ids']==result.json()['alternative_ids']
        data=browser.get(f'/api/projects/{pid}').json()
        assert len(data['alternatives'])==2
        assert len(data['alternatives'][0]['cells'])==5
        assert any(c is None for c in data['alternatives'][0]['cells'])


def test_xlsx_real_model_predictions_and_dynamic_criterion(imports):
    browser,user=analyst(imports);pid=imports['demo_id']
    with browser:
        alternatives=browser.get(f'/api/projects/{pid}').json()['alternatives']
        fields=dict(code='screen',name='Экран',unit='дюйм',direction='max',reason='Добавляем новый критерий',csrf=csrf(browser),revision=str(revision(browser,pid)))
        fields.update({f"score_{a['id']}":'14' for a in alternatives})
        added=browser.post(f'/api/projects/{pid}/criteria',data=fields);assert added.status_code==201,added.text
        # XLSX of the original columns is rejected when a current criterion is missing.
        assert preview(browser,pid,xlsx_rows([payload(code='X7')]),'data.xlsx').status_code==422
        content=file_bytes([payload(code='X7')|{'screen':'15.6'}],HEADERS+['screen'])
        checked=preview(browser,pid,content);assert checked.status_code==200,checked.text
        assert checked.json()['rows'][0]['prediction']>0
        result=confirm(browser,pid,checked.json()['token']);assert result.status_code==201,result.text
        aid=result.json()['alternative_ids'][0]
        data=browser.get(f'/api/projects/{pid}/alternatives/{aid}').json()
        scores={s['code']:s for s in data['scores']}
        assert scores['screen']['value']==15.6
        assert scores['performance_score']['source']=='ml' and scores['performance_score']['prediction_id']
        assert scores['performance_score']['value']==pytest.approx(checked.json()['rows'][0]['prediction'])


def test_all_errors_no_partial_write_and_conflict(imports):
    with client(imports) as browser:
        signup(browser);pid=new_project(browser);before=revision(browser,pid)
        checked=preview(browser,pid,file_bytes([payload(code='X1'),payload(code='X2',mass_kg='NaN')]))
        assert checked.status_code==422 and not checked.json()['valid']
        assert checked.json()['rows'][1]['errors']['mass_kg']
        assert checked.json()['token'] is None and revision(browser,pid)==before
        duplicates=preview(browser,pid,file_bytes([payload(code='X1'),payload(code='x1')]))
        assert duplicates.status_code==422 and all(r['errors']['code'] for r in duplicates.json()['rows'])
        good=preview(browser,pid).json()['token']
        browser.post(f'/api/projects/{pid}/edit',data={'csrf':csrf(browser),'revision':str(revision(browser,pid)),'title':'Изменённый проект','goal':'','budget':'100000'})
        assert confirm(browser,pid,good).status_code==409
        assert not browser.get(f'/api/projects/{pid}').json()['alternatives']


def test_access_csrf_upload_limits_and_actor_binding(imports):
    with client(imports) as owner,client(imports) as expert,client(imports) as stranger:
        signup(owner);user=signup(expert);signup(stranger);pid=new_project(owner)
        owner.post(f'/api/projects/{pid}/members',data={'csrf':csrf(owner),'email':user['email'],'role':'expert'})
        assert preview(expert,pid).status_code==403
        assert stranger.get(f'/projects/{pid}/alternatives/import').status_code==404
        assert preview(owner,pid,csrf='bad').status_code==403
        assert preview(owner,pid,b'x'*(MAX_FILE+10000)).status_code==422
        assert preview(owner,pid,b'x'*(MAX_FILE+100000)).status_code==413
        assert confirm(owner,pid,'bad').status_code==422
        token=preview(owner,pid).json()['token']
        owner.post(f'/api/projects/{pid}/members',data={'csrf':csrf(owner),'email':user['email'],'role':'analyst'})
        assert confirm(expert,pid,token).status_code==403
        assert confirm(owner,new_project(owner),token).status_code==403


def test_rollback_after_first_saved_row(imports,monkeypatch):
    from web_app import alternative_store
    original=alternative_store.save
    count=0
    def failing(*args,**kwargs):
        nonlocal count
        result=original(*args,**kwargs);count+=1
        if count==2:raise ValueError('Проверка отката всей таблицы')
        return result
    with client(imports) as browser:
        signup(browser);pid=new_project(browser)
        token=preview(browser,pid,file_bytes([payload(code='X1'),payload(code='X2')])).json()['token']
        before=revision(browser,pid);monkeypatch.setattr(alternative_store,'save',failing)
        result=confirm(browser,pid,token)
        assert result.status_code==422 and revision(browser,pid)==before
        assert browser.get(f'/api/projects/{pid}').json()['alternatives']==[]
        assert imports['admin'].execute('SELECT count(*) AS n FROM audit_events WHERE project_id=%s AND action=%s',(pid,'web_alternative_import')).fetchone()['n']==0


def test_xlsx_confirmation_with_active_model_and_expiry(imports,monkeypatch):
    import web_app.alternative_import_store as store
    browser,_=analyst(imports);pid=imports['demo_id']
    with browser:
        headers=HEADERS+['screen']
        source=xlsx_rows([payload(code='X8')|{'screen':'14'}],headers)
        checked=preview(browser,pid,source,'laptops.xlsx');assert checked.status_code==200,checked.text
        token=checked.json()['token']
        before=revision(browser,pid)
        original_time=store.time.time
        monkeypatch.setattr(store.time,'time',lambda:original_time()+1000)
        assert confirm(browser,pid,token).status_code==409
        monkeypatch.setattr(store.time,'time',original_time)
        assert revision(browser,pid)==before
        result=confirm(browser,pid,token);assert result.status_code==201,result.text
        original_model=store.active_model
        token=preview(browser,pid,xlsx_rows([payload(code='X9')|{'screen':'14'}],headers),'new.xlsx').json()['token']
        monkeypatch.setattr(store,'active_model',lambda *a:None)
        assert confirm(browser,pid,token).status_code==409
        monkeypatch.setattr(store,'active_model',original_model)


def test_limits_existing_codes_and_html_escaping(imports):
    browser,_=analyst(imports);pid=imports['demo_id']
    with browser:
        source=file_bytes([payload(code='A1')|{'screen':'14'}],HEADERS+['screen'])
        checked=preview(browser,pid,source)
        assert checked.status_code==422 and 'уже есть' in checked.json()['rows'][0]['errors']['code']
        source=file_bytes([payload(code=f'T{i}',name='<script>alert(1)</script>')|{'screen':'14'} for i in range(15)],HEADERS+['screen'])
        checked=browser.post(f'/projects/{pid}/alternatives/import/preview',data={'csrf':csrf(browser),'revision':str(revision(browser,pid)),'reason':'Проверка экранирования'},files={'file':('table.csv',source)})
        assert checked.status_code==422
        assert '<script>alert(1)</script>' not in checked.text
        assert '&lt;script&gt;' in checked.text
