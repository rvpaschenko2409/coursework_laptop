"""Declared Mitchell attribute threshold, average salience and bounded text input."""
from decimal import Decimal,ROUND_HALF_UP
import unicodedata
from web_app.alternative_validation import InvalidForm

RULE={'version':'mitchell-threshold-3-v1','attribute_threshold':3,'salience':'(power+legitimacy+urgency)/3','round_digits':3}
ATTRIBUTES={'power':'Власть','legitimacy':'Легитимность','urgency':'Срочность'}
CLASSES={0:'Нет выраженных атрибутов',1:'Латентный',2:'Ожидающий',3:'Определяющий'}
FIELDS=('name','stakeholder_role','interests',*ATTRIBUTES)


def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


def scores(values):
    for field in ATTRIBUTES:
        if type(values[field]) is not int or not 1<=values[field]<=5:raise ValueError('Оценки атрибутов должны быть целыми от 1 до 5.')
    total=sum(values[field] for field in ATTRIBUTES)
    present=[field for field in ATTRIBUTES if values[field]>=RULE['attribute_threshold']]
    return {'salience':float((Decimal(total)/3).quantize(Decimal('.001'),rounding=ROUND_HALF_UP)),
        'class_label':CLASSES[len(present)],'expressed_attributes':present,'expressed_count':len(present)}


def text(raw,label,minimum,maximum,multiline=True):
    if not isinstance(raw,str):raise ValueError(f'{label}: требуется текст.')
    # Reject hidden controls before whitespace normalization, including NUL.
    allowed='\n\r\t' if multiline else ''
    if any(unicodedata.category(c) in ('Cc','Cs') and c not in allowed for c in raw):raise ValueError(f'{label}: недопустимые управляющие символы.')
    value=unicodedata.normalize('NFC',raw).strip()
    if not multiline:value=' '.join(value.split())
    if not minimum<=len(value)<=maximum:raise ValueError(f'{label}: от {minimum} до {maximum} символов.')
    return value


def reason(raw):
    try:return text(raw,'Основание изменения',3,500)
    except ValueError as exc:raise InvalidForm({'reason':str(exc)}) from exc


def validate(form):
    errors={};values={}
    for field,label,minimum,maximum,multiline in [
        ('name','Название стороны',2,200,False),('stakeholder_role','Роль в задаче',2,300,False),
        ('interests','Интересы',3,1000,True),('reason','Основание изменения',3,500,True)]:
        try:values[field]=text(form.get(field,''),label,minimum,maximum,multiline)
        except ValueError as exc:errors[field]=str(exc)
    for field,label in ATTRIBUTES.items():
        raw=form.get(field,'')
        if raw not in ('1','2','3','4','5'):errors[field]=f'{label}: выберите целую оценку от 1 до 5.'
        else:values[field]=int(raw)
    if errors:raise InvalidForm(errors)
    return values
