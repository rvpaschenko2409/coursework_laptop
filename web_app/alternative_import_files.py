"""Bounded CSV/XLSX interchange. No spreadsheet formulas or workbook code are executed."""
import csv
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

MAX_FILE = 1024 * 1024
MAX_ROWS = 15
MAX_COLS = 32
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def xml(data):
    scan=data.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:
        raise ValueError('Объявления DTD и сущностей в Excel не поддерживаются.')
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise ValueError('Повреждён XML внутри Excel.') from None


def read_csv(content, max_rows=MAX_ROWS):
    try:
        text = content.decode('utf-8-sig'); encoding = 'UTF-8'
    except UnicodeDecodeError:
        try:
            text = content.decode('cp1251'); encoding = 'Windows-1251'
        except UnicodeDecodeError:
            raise ValueError('Сохраните CSV в UTF-8 или Windows-1251.') from None
    if '\x00' in text:
        raise ValueError('CSV содержит недопустимые двоичные данные.')
    first = next((s for s in text.splitlines() if s.strip()), '')
    separator = max((';', ',', '\t'), key=lambda sep: len(next(csv.reader([first], delimiter=sep))))
    rows = []
    try:
        reader = csv.reader(io.StringIO(text, newline=''), delimiter=separator, strict=True)
        previous = 0
        for row in reader:
            line = previous + 1; previous = reader.line_num
            if any(cell.strip() for cell in row):
                rows.append((line, row))
            if len(rows) > max_rows + 1:
                raise ValueError(f'В одном файле допускается не больше {max_rows} строк данных.')
    except csv.Error:
        raise ValueError('Не удалось разобрать CSV. Проверьте разделители и кавычки.') from None
    return rows, f'CSV · {encoding}'


def column(reference, max_sheet_rows=256):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,5})', reference)
    if not match:
        raise ValueError('Неверный адрес ячейки Excel.')
    number = 0
    for c in match[1]:
        number = number * 26 + ord(c) - 64
    if number > MAX_COLS or int(match[2]) > max_sheet_rows:
        raise ValueError(f'Excel: максимум 32 столбца и {max_sheet_rows} строк, включая пустые.')
    return number - 1, int(match[2])


def read_xlsx(content, max_rows=MAX_ROWS, max_sheet_rows=256):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 64 or len(names) != len(set(names)) or sum(i.file_size for i in infos) > (16 if max_rows==MAX_ROWS else 64) * MAX_FILE:
                raise ValueError('Слишком сложный или большой файл Excel.')
            if any(i.flag_bits & 1 or i.file_size > (8 if max_rows==MAX_ROWS else 32) * MAX_FILE or i.file_size > max(1, i.compress_size) * 500 for i in infos):
                raise ValueError('Зашифрованный или чрезмерно сжатый Excel не поддерживается.')
            if any('vbaproject' in n.lower() or n.startswith('xl/externalLinks/') for n in names):
                raise ValueError('Используйте обычный .xlsx без макросов и внешних связей.')
            for name in names:
                if name.endswith('.rels'):
                    relations = xml(archive.read(name))
                    if any(r.attrib.get('TargetMode', '').lower() == 'external' for r in relations):
                        raise ValueError('Внешние связи Excel не поддерживаются. Вставьте значения в чистый файл.')
            workbook = xml(archive.read('xl/workbook.xml'))
            sheets = workbook.findall('s:sheets/s:sheet', NS)
            if len(sheets) != 1 or sheets[0].attrib.get('state', 'visible') != 'visible':
                raise ValueError('Для импорта нужен Excel с одним видимым листом.')
            relationships = xml(archive.read('xl/_rels/workbook.xml.rels'))
            target = next((r.attrib['Target'] for r in relationships if r.attrib['Id'] == sheets[0].attrib.get('{'+REL+'}id')), None)
            if not target:
                raise ValueError('В Excel не найден лист данных.')
            path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if not path.startswith('xl/') or path not in names:
                raise ValueError('Недопустимый путь листа Excel.')
            shared = []
            if 'xl/sharedStrings.xml' in names:
                root = xml(archive.read('xl/sharedStrings.xml'))
                shared = [''.join(t.text or '' for t in si.iter('{'+NS['s']+'}t')) for si in root]
            date_styles=set()
            if 'xl/styles.xml' in names:
                styles=xml(archive.read('xl/styles.xml'))
                custom={int(n.attrib['numFmtId']):n.attrib.get('formatCode','') for n in styles.findall('s:numFmts/s:numFmt',NS)}
                for index,style in enumerate(styles.findall('s:cellXfs/s:xf',NS)):
                    fmt=int(style.attrib.get('numFmtId','0'))
                    code=re.sub(r'"[^"]*"|\\.', '',custom.get(fmt,''))
                    elapsed=bool(re.search(r'\[(?:h+|m+|s+)\]',code,re.I))
                    code=re.sub(r'\[[^\]]*\]','',code)
                    if fmt in set(range(14,23))|{45,46,47} or elapsed or re.search(r'[ymdhis]',code,re.I):date_styles.add(index)
            sheet = xml(archive.read(path))
            if sheet.find('s:mergeCells', NS) is not None:
                raise ValueError('Объединённые ячейки не поддерживаются. Используйте шаблон.')
            output = []; seen_rows = set(); cell_count = 0
            for row in sheet.findall('s:sheetData/s:row', NS):
                raw_row=row.attrib.get('r','0')
                if not re.fullmatch('[1-9][0-9]{0,5}',raw_row):raise ValueError('Неверный номер строки Excel.')
                rn = int(raw_row)
                if not 1 <= rn <= max_sheet_rows or rn in seen_rows:
                    raise ValueError('Неверные или повторяющиеся строки Excel.')
                seen_rows.add(rn); cells = {}
                for c in row.findall('s:c', NS):
                    cell_count += 1
                    if cell_count > max(4096, (max_rows+1)*MAX_COLS):
                        raise ValueError('В Excel слишком много ячеек.')
                    ci, ri = column(c.attrib.get('r', ''), max_sheet_rows)
                    if ri != rn or ci in cells:
                        raise ValueError('Повторяющиеся или неверные адреса ячеек Excel.')
                    if c.find('s:f', NS) is not None:
                        raise ValueError(f'Строка {rn}: формулы не импортируются. Вставьте результат как значение.')
                    if int(c.attrib.get('s','0')) in date_styles:
                        raise ValueError(f'Строка {rn}: даты и время не подходят для характеристик ноутбука.')
                    kind = c.attrib.get('t', 'n')
                    value = c.findtext('s:v', default='', namespaces=NS)
                    if kind == 's':
                        try:
                            value = shared[int(value)]
                            if int(c.findtext('s:v', namespaces=NS)) < 0:raise IndexError()
                        except (ValueError, IndexError, TypeError):
                            raise ValueError('Повреждена таблица строк Excel.') from None
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in c.findall('.//s:t', NS))
                    elif kind not in ('n', 'str'):
                        raise ValueError(f'Строка {rn}: допустимы текст и числа, без дат, логических значений и ошибок Excel.')
                    cells[ci] = value
                if any(v.strip() for v in cells.values()):
                    output.append((rn, [cells.get(i, '') for i in range(max(cells) + 1)]))
                if len(output) > max_rows + 1:
                    raise ValueError(f'В одном файле допускается не больше {max_rows} строк данных.')
            return sorted(output), 'Excel XLSX'
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, OverflowError, ET.ParseError):
        raise ValueError('Не удалось открыть Excel. Нужен обычный файл .xlsx без пароля.') from None


def read_table(content, filename, required, optional=('description',), *, max_rows=MAX_ROWS, max_file=MAX_FILE):
    if not content or len(content) > max_file:
        raise ValueError(f'Файл должен быть непустым и не больше {max_file // MAX_FILE} МБ.')
    extension = filename.lower().rsplit('.', 1)[-1]
    if extension == 'csv':rows, label = read_csv(content, max_rows)
    elif extension == 'xlsx':rows, label = read_xlsx(content, max_rows, max(256, max_rows+1))
    else:raise ValueError('Выберите CSV или XLSX. Старый формат XLS не поддерживается.')
    if len(rows) < 2:
        raise ValueError('Нужны строка заголовков и хотя бы один ноутбук.')
    headers = [c.strip() for c in rows[0][1]]
    if len(headers) > MAX_COLS or any(not h for h in headers) or len(headers) != len(set(headers)):
        raise ValueError('Заголовки должны быть непустыми и не повторяться; максимум 32 столбца.')
    missing = set(required) - set(headers); unknown = set(headers) - set(required) - set(optional)
    if missing or unknown:
        raise ValueError('Проверьте заголовки. Отсутствуют: ' + ', '.join(sorted(missing)) + '. Неизвестные: ' + ', '.join(sorted(unknown)) + '. Скачайте шаблон для текущих критериев.')
    output = []
    for line, cells in rows[1:]:
        if len(cells) > len(headers) and any(c.strip() for c in cells[len(headers):]):
            raise ValueError(f'Строка {line}: больше значений, чем заголовков.')
        if any(len(c) > 2000 for c in cells):
            raise ValueError(f'Строка {line}: значение длиннее 2000 символов.')
        output.append((line, {name: (cells[i].strip() if i < len(cells) else '') for i, name in enumerate(headers)}))
    return output, label


def csv_bytes(headers, rows=()):
    stream = io.StringIO(newline=''); writer = csv.writer(stream, delimiter=';', lineterminator='\r\n')
    writer.writerow(headers)
    for row in rows:
        # Template/example exports never turn untrusted text into spreadsheet formulas.
        writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for v in row])
    return stream.getvalue().encode('utf-8-sig')


def xlsx_bytes(headers):
    # A small native template built by the application, independent of optional Excel libraries.
    cells=[]
    for i, h in enumerate(headers):
        letter = ''; n=i+1
        while n:n,r=divmod(n-1,26);letter=chr(65+r)+letter
        cells.append(f'<c r="{letter}1" t="inlineStr" s="1"><is><t>{escape(h)}</t></is></c>')
    files = {
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="'+REL+'/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<workbook xmlns="'+NS['s']+'" xmlns:r="'+REL+'"><sheets><sheet name="Ноутбуки" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="'+REL+'/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="'+REL+'/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':'<styleSheet xmlns="'+NS['s']+'"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF233449"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf xfId="0"/><xf xfId="0" fontId="1" fillId="2" applyFill="1" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml':'<worksheet xmlns="'+NS['s']+'"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="32" width="23" customWidth="1"/></cols><sheetData><row r="1" ht="42" customHeight="1">'+''.join(cells)+'</row></sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, value in files.items():z.writestr(path, '<?xml version="1.0" encoding="UTF-8"?>'+value)
    return output.getvalue()
