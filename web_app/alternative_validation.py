"""Validate the original five-criterion laptop form; no implicit default scores."""
from decimal import Decimal,InvalidOperation
import math
import re

FEATURES=['cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_gb','storage_type']
MANUAL={
 'price_rub':('Цена, ₽',1,1e12,False),
 'mass_kg':('Масса, кг',0.1,20,False),
 'battery_hours':('Автономность, ч',0.1,72,False),
 'warranty_months':('Гарантия, мес.',1,120,True),
}
HARDWARE={
 'cpu_cores':('Ядра процессора',1,128,True),
 'cpu_threads':('Потоки процессора',1,256,True),
 'cpu_frequency_ghz':('Частота, ГГц',0.1,10,False),
 'ram_gb':('Оперативная память, ГБ',1,1024,True),
 'storage_gb':('Ёмкость накопителя, ГБ',16,65536,True),
}


class InvalidForm(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__('Проверьте отмеченные поля.')


def number(value,label,lower,upper,integer=False):
    try:
        if not isinstance(value,str) or len(value)>40:raise ValueError()
        d=Decimal(value.strip().replace(',','.'))
        if not d.is_finite() or not Decimal(str(lower))<=d<=Decimal(str(upper)) or (integer and d!=d.to_integral_value()):raise ValueError()
        result=int(d) if integer else float(d)
        if not math.isfinite(result):raise ValueError()
        return result
    except (ValueError,InvalidOperation,OverflowError):
        whole=' целое' if integer else ''
        raise ValueError(f'{label}: введите{whole} число от {human_number(lower)} до {human_number(upper)}.') from None


def scores_from_form(form):
    errors={};scores={}
    for field,args in MANUAL.items():
        try:scores[field]=number(form.get(field,''),*args)
        except ValueError as exc:errors[field]=str(exc)
    if errors:raise InvalidForm(errors)
    return scores


def validate_form(form,expert=False):
    errors={};values={}
    try:values['scores']=scores_from_form(form)
    except InvalidForm as exc:errors.update(exc.errors)
    reason=str(form.get('change_reason','')).strip()
    if len(reason)>500 or any(ord(c)<32 and c not in '\r\n' for c in reason):errors['change_reason']='Причина: не более 500 печатных символов.'
    values['reason']=reason
    if not expert:
        code=str(form.get('code','')).strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9_-]{0,19}',code):errors['code']='Код: латинская буква, затем буквы, цифры, _ или -; до 20 символов.'
        name=str(form.get('name','')).strip();description=str(form.get('description','')).strip()
        if not 2<=len(name)<=200 or any(ord(c)<32 for c in name):errors['name']='Название: от 2 до 200 печатных символов.'
        if len(description)>1000 or any(ord(c)<32 and c not in '\r\n\t' for c in description):errors['description']='Описание: до 1000 символов.'
        values.update(code=code,name=name,description=description)
        features={}
        for field,args in HARDWARE.items():
            try:features[field]=number(form.get(field,''),*args)
            except ValueError as exc:errors[field]=str(exc)
        storage=str(form.get('storage_type',''))
        if storage not in ('SSD','HDD'):errors['storage_type']='Выберите SSD или HDD.'
        features['storage_type']=storage
        if 'cpu_cores' in features and 'cpu_threads' in features and features['cpu_threads']<features['cpu_cores']:errors['cpu_threads']='Потоков должно быть не меньше, чем ядер.'
        values['features']=features
        mode=form.get('performance_mode','auto')
        if mode not in ('auto','override'):errors['performance_mode']='Выберите прогноз модели или ручную корректировку.'
        values['performance_mode']=mode
        if mode=='override':
            try:values['performance_override']=number(form.get('performance_override',''),'Корректировка производительности',0.000001,1e9)
            except ValueError as exc:errors['performance_override']=str(exc)
            if not reason:errors['change_reason']='Укажите причину ручной корректировки прогноза.'
    if errors:raise InvalidForm(errors)
    return values


def in_training_profiles(features):
    profiles={(2,4,2.0),(4,4,2.5),(4,8,2.5),(6,12,2.8),(8,16,3.0)}
    return ((features.get('cpu_cores'),features.get('cpu_threads'),features.get('cpu_frequency_ghz')) in profiles
        and features.get('ram_gb') in (4,8,16,32)
        and features.get('storage_gb') in ((256,512,1024) if features.get('storage_type')=='SSD' else (500,1000)))


def human_number(value):
    return f'{value:,.6f}'.rstrip('0').rstrip('.').replace(',',' ').replace('.',',')
