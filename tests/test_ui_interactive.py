"""Safe local presentation: data projection, theme assets, CSP and historical chart parity."""
from html.parser import HTMLParser
from pathlib import Path
import json
import pytest
pytest.importorskip('fastapi')
from jinja2 import Environment,FileSystemLoader,select_autoescape
from test_web_alternatives import edits,web_env,client,signup,csrf,analyst
from test_web_decision_choice import setup
from test_web_calculations import run as calculate

ROOT=Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self,source):super().__init__();self.elements=[];self.feed(source)
    def handle_starttag(self,tag,attrs):self.elements.append((tag,dict(attrs)))


def template():return Environment(loader=FileSystemLoader(ROOT/'web_app/templates'),autoescape=select_autoescape(['html'])).get_template('ui_interactive.html').module


def test_chart_json_cannot_escape_html_attribute_or_create_script():
    text='"><script>alert(1)</script> & «Критерий»'
    points=[{'label':text,'series':'Вес','value':.25,'detail':text}]
    parsed=Elements(template().bars(points,1,'Диаграмма'))
    assert len(parsed.elements)==1 and parsed.elements[0][0]=='div'
    attrs=parsed.elements[0][1];assert json.loads(attrs['data-points'])==points and 'hidden' in attrs


def test_filter_attribute_escaping_and_unknown_values():
    a={'id':9,'code':'A9','name':'"><img src=x onerror=alert(1)>','features':{},'cells':[None]}
    html=f"<article {template().laptop_attrs(a,[{'code':'price_rub'}])}>X</article>"
    parsed=Elements(html);assert len(parsed.elements)==1
    attrs=parsed.elements[0][1]
    assert attrs['data-search']=='A9 '+a['name'] and attrs['data-price']==attrs['data-ram']==attrs['data-storage']==''
    assert 'onerror' not in attrs


@pytest.fixture(scope='module')
def interactive(edits):return edits


def test_local_assets_and_csp_allow_self_scripts_without_inline_or_eval(interactive):
    with client(interactive) as browser:
        js=browser.get('/static/interactive.js');css=browser.get('/static/interactive.css')
        assert js.status_code==css.status_code==200
        assert 'javascript' in js.headers['content-type'] and 'text/css' in css.headers['content-type']
        policy=js.headers['content-security-policy']
        assert "script-src 'self'" in policy and "object-src 'none'" in policy
        assert 'unsafe-inline' not in policy and 'unsafe-eval' not in policy
        assert 'fetch(' not in js.text and 'innerHTML' not in js.text and 'eval(' not in js.text


def test_cards_and_matrix_have_matching_filter_metadata(interactive):
    env=interactive;a,u=analyst(env);pid=env['demo_id']
    with a:
        page=a.get(f'/projects/{pid}');assert page.status_code==200
        parsed=Elements(page.text)
        items=[(tag,d) for tag,d in parsed.elements if 'data-laptop-id' in d]
        cards={d['data-laptop-id']:d for t,d in items if t=='article'}
        rows={d['data-laptop-id']:d for t,d in items if t=='tr'}
        assert len(cards)==len(rows)==5
        for key,card in cards.items():
            for field in ['data-search','data-price','data-ram','data-storage']:assert card[field]==rows[key][field]
        assert sorted(float(c['data-price']) for c in cards.values())==[35000,48000,55000,70000,95000]
        controls=next(d for t,d in parsed.elements if 'data-laptop-filters' in d)
        assert 'hidden' in controls
        assert any('data-theme-toggle' in d and 'hidden' in d for t,d in parsed.elements)


def test_chart_payloads_match_verified_pair_and_do_not_write_results(interactive):
    env=interactive;a,l,au,lu,ids=setup(env);pid=env['demo_id'];rid=ids['marcos'];db=env['admin']
    with a,l:
        expected=a.get(f'/api/projects/{pid}/calculations/{rid}').json()
        before=db.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
        page=a.get(f'/projects/{pid}/calculations/{rid}');assert page.status_code==200,page.text
        points=[json.loads(d['data-points']) for _,d in Elements(page.text).elements if 'data-interactive-bars' in d]
        assert len(points)==2
        assert [p['value'] for p in points[0]]==[w['weight'] for w in expected['weighting']['weights']]
        bycode={r['code']:r for r in expected['comparison']}
        for point in points[1]:assert point['value']==bycode[point['label']][point['series'].lower()]['rank']
        assert db.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']==before
        assert l.get(f'/projects/{pid}/calculations/{rid}').status_code==200
        assert calculate(l,pid).status_code==403
        # The chart projection remains bound to the old pair after a live edit.
        db.execute("UPDATE projects SET goal='Новое текущее описание' WHERE id=%s",(pid,))
        old=a.get(f'/projects/{pid}/calculations/{rid}')
        again=[json.loads(d['data-points']) for _,d in Elements(old.text).elements if 'data-interactive-bars' in d]
        assert points==again and 'исторический результат' in old.text
