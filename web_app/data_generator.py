"""Reproducible toy laptop data with declared, disjoint educational defects."""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

import numpy as np

from web_app import training_data_store as store
from web_app.alternative_import_files import csv_bytes
from web_app.alternative_validation import FEATURES, number

VERSION = 'web-laptop-generator-v1'
FORMULA_VERSION = 'laptop-toy-score-v1'
FORMULA = '1000 / (0.7 / ((cores / 4)^0.7 * (GHz / 2.5)) + 0.2 / (RAM / 8)^0.3 + 0.1 / disk)'
HEADERS = ['sample_id','cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_type','storage_gb','synthetic_score','data_origin']
DEFAULTS = {'n_rows':'2000','seed':'42','missing_pct':'0','outlier_pct':'0','duplicate_pct':'0','noise_sigma':'0.05',
            'name':'Синтетические данные ноутбуков','reason':'Учебный эксперимент с качеством данных'}


def parameters(fields):
    result = {
        'n_rows':number(fields.get('n_rows',''),'Число строк',100,10000,True),
        'seed':number(fields.get('seed',''),'Seed',0,2147483647,True),
        'noise_sigma':number(fields.get('noise_sigma',''),'Шум (sigma)',0,0.5),
    }
    for key,label in [('missing_pct','Пропуски, %'),('outlier_pct','Выбросы, %'),('duplicate_pct','Дубликаты, %')]:
        result[key]=number(fields.get(key,''),label,0,30)
    if sum(Decimal(str(result[k])) for k in ('missing_pct','outlier_pct','duplicate_pct'))>60:
        raise ValueError('Сумма долей дефектов должна быть не больше 60%.')
    for key,label,lower,upper in [('name','Название',2,200),('reason','Цель генерации',3,500)]:
        value=fields.get(key,'').strip()
        if not lower<=len(value)<=upper or any(ord(c)<32 for c in value):
            raise ValueError(f'{label}: от {lower} до {upper} печатных символов.')
        result[key]=value
    return result


def defect_count(n,percent):
    return int((Decimal(n)*Decimal(str(percent))/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def build(fields):
    p=parameters(fields)
    n=p['n_rows'];seed=p['seed'];sigma=p['noise_sigma']
    counts={key:defect_count(n,p[key+'_pct']) for key in ('missing','outlier','duplicate')}
    unique=n-counts['duplicate']
    if sum(counts.values())>n-20:
        raise ValueError('После внесения дефектов должно оставаться не меньше 20 строк.')
    rng=np.random.default_rng(seed)
    profiles=np.array([[2,4,2.0],[4,4,2.5],[4,8,2.5],[6,12,2.8],[8,16,3.0]])
    selected=profiles[rng.integers(0,len(profiles),unique)]
    cores=selected[:,0].astype(int);threads=selected[:,1].astype(int);frequency=selected[:,2]
    ram=rng.choice([4,8,16,32],size=unique)
    storage_type=rng.choice(['SSD','HDD'],size=unique)
    storage_gb=np.empty(unique,dtype=int);ssd=storage_type=='SSD'
    storage_gb[ssd]=rng.choice([256,512,1024],size=int(ssd.sum()))
    storage_gb[~ssd]=rng.choice([500,1000],size=int((~ssd).sum()))
    cpu=(cores/4)**0.7*(frequency/2.5);memory=(ram/8)**0.3;disk=np.where(ssd,1.0,0.4)
    relative_time=0.7/cpu+0.2/memory+0.1/disk
    noise=rng.lognormal(mean=-(sigma**2)/2,sigma=sigma,size=unique)
    score=np.round(1000/relative_time*noise,2)
    rows=[]; provenance=[]
    for i in range(unique):
        values=[i+1,int(cores[i]),int(threads[i]),float(frequency[i]),int(ram[i]),str(storage_type[i]),int(storage_gb[i]),float(score[i]),'synthetic']
        rows.append(dict(zip(HEADERS,map(str,values))))
        provenance.append({'origin':'synthetic','generator_version':VERSION,'formula_version':FORMULA_VERSION,
                           'seed':seed,'source_sample_id':i+1,'base_draw_index':i,'defect':None})
    # Defect RNG does not consume or change the clean generation stream.
    corrupt=np.random.default_rng(np.random.SeedSequence([seed,271828]))
    order=corrupt.permutation(unique).tolist()
    missing_ids=order[:counts['missing']]
    outlier_ids=order[counts['missing']:counts['missing']+counts['outlier']]
    clean_ids=order[counts['missing']+counts['outlier']:]
    candidates=[*FEATURES,'synthetic_score']
    for i in missing_ids:
        field=candidates[int(corrupt.integers(len(candidates)))]
        rows[i][field]=''
        provenance[i]['defect']={'kind':'missing','field':field}
    for i in outlier_ids:
        rows[i]['cpu_frequency_ghz']=str(float(rows[i]['cpu_frequency_ghz'])*20)
        provenance[i]['defect']={'kind':'outlier','field':'cpu_frequency_ghz','multiplier':20}
    for _ in range(counts['duplicate']):
        source=int(corrupt.choice(clean_ids))
        rows.append(dict(rows[source]))
        provenance.append({**provenance[source],'defect':{'kind':'duplicate','source_row_number':source+1}})
    content=csv_bytes(HEADERS,[[r[key] for key in HEADERS] for r in rows])
    review=store.inspect(content,'generated_laptops.csv',{
        'name':p['name'],'origin':'synthetic','source_url':'','license':'Авторская учебная синтетика; реальные измерения не используются.',
        'seed':str(seed),'generator_version':VERSION,'reason':p['reason']})
    code_sha=hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()
    generation={key:value for key,value in p.items() if key not in ('name','reason')}
    generation.update({'generator_version':VERSION,'formula_version':FORMULA_VERSION,'formula':FORMULA,
        'disk_factors':{'SSD':1.0,'HDD':0.4},'noise':{'distribution':'lognormal','log_mean':-(sigma**2)/2,'log_std':sigma},
        'defect_counts':counts,'denominator':'final raw row count','count_rounding':'nearest integer; halves up',
        'defects_disjoint':True,'outlier_rule':'cpu_frequency_ghz multiplied by 20 (outside 0.1..10 GHz domain)',
        'missing_rule':'one randomly selected feature or target cell per selected row',
        'duplicate_rule':'copies of intact base rows appended, including their sample_id',
        'generator_code_sha256':code_sha,'numpy_version':np.__version__,'rng':'PCG64',
        'defect_rng_seed_sequence':[seed,271828],'content_sha256':review['file_sha256']})
    for r in review['raw']:
        r['provenance'].update(provenance[r['row_number']-1])
        r['provenance']['generated_row_number']=r['row_number']
    for r in review['staging']:
        r['provenance'].update(provenance[r['row_number']-1])
        r['provenance']['generated_row_number']=r['row_number']
    # A generated dataset is identified by its full recipe, not just its CSV bytes.
    review['file_sha256']=store.digest({'generation':generation,'raw':[r['data'] for r in review['raw']]})
    review['generation']=generation
    review['quality']['injected_defects']=counts
    review['quality']['generation']=generation
    review['format_name']='Сгенерировано в приложении'
    review['sample']=review['raw'][:10]
    return review
