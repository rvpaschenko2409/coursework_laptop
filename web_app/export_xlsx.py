"""Fill a bundled styled OOXML template using only the Python standard library."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from math import ceil,isfinite
import re
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from web_app.export_rows import make_report

S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
C='http://schemas.openxmlformats.org/drawingml/2006/chart'
NS={'s':S,'c':C}
TEMPLATE=Path(__file__).resolve().parent/'assets/report_template.xlsx'
MAX_ROWS=100000


def letter(index):
    value=''
    while index:index,n=divmod(index-1,26);value=chr(65+n)+value
    return value


def text(value):
    value=str(value)
    if len(value)>32767 or any(not (c in '\t\n\r' or 0x20<=ord(c)<=0xD7FF or 0xE000<=ord(c)<=0xFFFD or 0x10000<=ord(c)<=0x10FFFF) for c in value):raise ValueError('Текст отчёта содержит недопустимый символ XML или превышает предел ячейки Excel.')
    return '<is><t xml:space="preserve">'+escape(value)+'</t></is>'


def styles(archive):
    root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    cells={c.attrib['r']:c.attrib.get('s','0') for c in root.findall('.//s:c',NS)}
    return {'title':cells['A2'],'header':cells['A4'],'text':cells['D30'],'number':cells['E30'],'integer':cells['F30'],'date':cells['G30']}


def cell(value,address,style):
    if value is None:return f'<c r="{address}" s="{style}"/>'
    if isinstance(value,bool):return f'<c r="{address}" s="{style}" t="inlineStr">{text("Да" if value else "Нет")}</c>'
    if isinstance(value,datetime):
        if value.tzinfo is not None:raise ValueError('Для Excel дата должна быть явно переведена в местное время.')
        serial=(value-datetime(1899,12,30)).total_seconds()/86400
        return f'<c r="{address}" s="{style}"><v>{serial!r}</v></c>'
    if isinstance(value,(int,float)):
        if not isfinite(value):raise ValueError('В отчёте обнаружено неконечное число.')
        return f'<c r="{address}" s="{style}"><v>{value!r}</v></c>'
    return f'<c r="{address}" s="{style}" t="inlineStr">{text(value)}</c>'


def worksheet(original,spec,catalog):
    if len(spec['rows'])>MAX_ROWS:raise ValueError('Слишком много строк для выгрузки. Уменьшите число альтернатив.')
    rows=[]
    for index,r in enumerate(spec['rows'],1):
        values=[];height=24
        for col,value in enumerate(r['values'],1):
            fmt=r['formats'][col-1] if col<=len(r['formats']) else None
            style=r['kind'] if r['kind'] in ('title','header') else ('date' if isinstance(value,datetime) else fmt if fmt in catalog else 'integer' if isinstance(value,int) or isinstance(value,float) and value.is_integer() else 'number' if isinstance(value,float) else 'text')
            values.append(cell(value,f'{letter(col)}{index}',catalog[style]))
            if isinstance(value,str):
                width=spec['widths'][col-1] if col<=len(spec['widths']) else 30
                height=max(height,8+16*sum(max(1,ceil(len(line)/max(8,width-3))) for line in value.split('\n')))
        height=max(46,height) if r['kind']=='header' else 32 if r['kind']=='title' else min(409,height)
        rows.append(f'<row r="{index}" ht="{height}" customHeight="1">'+''.join(values)+'</row>')
    root=ET.fromstring(original);old=root.find('s:sheetData',NS)
    index=list(root).index(old);root.remove(old)
    root.insert(index,ET.fromstring('<sheetData xmlns="'+S+'">'+''.join(rows)+'</sheetData>'))
    columns=max(len(r['values']) for r in spec['rows']);ref=f'A1:{letter(columns)}{len(rows)}'
    dimension=root.find('s:dimension',NS)
    if dimension is None:
        dimension=ET.Element(f'{{{S}}}dimension');root.insert(1 if root.find('s:sheetPr',NS) is not None else 0,dimension)
    dimension.set('ref',ref)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def chart(original,spec):
    root=ET.fromstring(original);rows=spec['rows'][4:4+spec['chart_rows']];count=len(rows)
    series=root.findall('.//c:ser',NS)
    if len(series)!=2:raise ValueError('Неверный шаблон диаграммы отчёта.')
    for ser,col in zip(series,[2,4]):
        for tag,key,values in [('cat','A',[r['values'][0] for r in rows]),('val',letter(col+1),[r['values'][col] for r in rows])]:
            branch=ser.find('c:'+tag,NS)
            formula=branch.find('.//c:f',NS);formula.text=f"'Рейтинги'!${key}$5:${key}${4+count}"
            cache=branch.find('.//c:strCache',NS) if tag=='cat' else branch.find('.//c:numCache',NS)
            if cache is None:raise ValueError('В шаблоне диаграммы не найден кэш данных.')
            for node in list(cache):
                if node.tag in (f'{{{C}}}pt',f'{{{C}}}ptCount'):cache.remove(node)
            ET.SubElement(cache,f'{{{C}}}ptCount',{'val':str(count)})
            for i,value in enumerate(values):
                point=ET.SubElement(cache,f'{{{C}}}pt',{'idx':str(i)});ET.SubElement(point,f'{{{C}}}v').text=str(value)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def export_bytes(data):
    plan=make_report(data);stream=BytesIO()
    with ZipFile(TEMPLATE) as source,ZipFile(stream,'w',compression=ZIP_DEFLATED,compresslevel=6) as output:
        catalog=styles(source)
        for item in source.infolist():
            raw=source.read(item.filename)
            match=re.fullmatch(r'xl/worksheets/sheet([1-7])\.xml',item.filename)
            if match:raw=worksheet(raw,plan[int(match[1])-1],catalog)
            elif re.fullmatch(r'xl/(?:drawings/)?charts/chart[0-9]+\.xml',item.filename):raw=chart(raw,plan[1])
            output.writestr(item.filename,raw)
    return stream.getvalue()
