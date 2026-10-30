"""FastAPI/Jinja2 foundation: accounts and project-scoped roles."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import secrets
from typing import Annotated

from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.templating import Jinja2Templates
import psycopg
from psycopg.types.json import Jsonb

from web_app.database import Database
from web_app.security import (COOKIE,PH,Context,csrf_check,is_blocked,limit_key,
    normalize_email,record_failure,rotate,session_context,validate_password,verify_password)

ROOT=Path(__file__).resolve().parent
ROLES={'analyst':'Аналитик','decision_maker':'ЛПР','expert':'Эксперт'}
CRITERIA=(('price_rub','Цена','руб.','min','manual'),
          ('performance_score','Прогнозная производительность','условные синтетические баллы','max','ml'),
          ('mass_kg','Масса','кг','min','manual'),('battery_hours','Автономность','ч','max','manual'),
          ('warranty_months','Гарантия','мес.','max','manual'))


@dataclass(frozen=True)
class Settings:
    secure_cookies: bool=False
    allowed_hosts: tuple=('localhost','127.0.0.1','testserver')


class SmallForms:
    """Bound POST bodies before the form parser buffers them."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method']!='POST':
            return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            part=message.get('body',b'');size+=len(part)
            if size>8192:
                return await JSONResponse({'detail':'Форма слишком большая.'},status_code=413)(scope,receive,send)
            chunks.append(part)
            if not message.get('more_body',False):break
        delivered=False
        async def buffered():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,buffered,send)


def create_app(database: Database,settings: Settings | None=None,verify_database: bool=True) -> FastAPI:
    settings=settings or Settings()
    if verify_database:database.verify()
    app=FastAPI(title='СППР «Ноутбук»',version='0.1.0',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.database=database
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html','xml']))
    templates=Jinja2Templates(env=env)
    dummy_hash=PH.hash(secrets.token_urlsafe(32))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(SmallForms)
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'none'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if not request.url.path.startswith('/static/'):
            response.headers['Cache-Control']='no-store'
        return response

    def context_dependency(request: Request):
        with database.connect() as connection:
            context=session_context(connection,request.cookies.get(COOKIE))
            yield context

    Ctx=Annotated[Context,Depends(context_dependency,scope='function')]

    def cookie(response,context):
        seconds=max(0,int((context.session['expires_at']-datetime.now(timezone.utc)).total_seconds()))
        response.set_cookie(COOKIE,context.cookie_token,max_age=seconds,httponly=True,secure=settings.secure_cookies,samesite='lax',path='/')
        return response

    def render(request,context,template_name,status=200,**values):
        return cookie(templates.TemplateResponse(request=request,name=template_name,
            context={'user':context.user,'csrf':context.session['csrf_token'],'roles':ROLES,**values},status_code=status),context)

    def api(request):return request.url.path.startswith('/api/') or request.url.path=='/openapi.json'
    def response(request,context,data,status=200,target='/projects'):
        return cookie(JSONResponse(jsonable_encoder(data),status_code=status) if api(request) else RedirectResponse(target,status_code=303),context)
    def require_user(context):
        if not context.user:raise HTTPException(401,'Сначала войдите в систему.')
        return context.user

    def project_access(context,project_id,analyst=False,lock=False):
        user=require_user(context)
        query='''SELECT p.id,p.owner_id,p.title,p.goal,p.budget,p.status,m.role::text AS role
            FROM projects p JOIN project_members m ON m.project_id=p.id WHERE p.id=%s AND m.user_id=%s'''
        row=context.connection.execute(query,(project_id,user['id'])).fetchone()
        if not row:raise HTTPException(404,'Проект не найден или доступ не предоставлен.')
        if lock:
            context.connection.execute('SELECT id FROM projects WHERE id=%s FOR UPDATE',(project_id,))
            row=context.connection.execute(query,(project_id,user['id'])).fetchone()
            if not row:raise HTTPException(404,'Доступ к проекту изменён.')
        if analyst and row['role']!='analyst':raise HTTPException(403,'Действие доступно только аналитику проекта.')
        return dict(row)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        if api(request):return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
        if exc.status_code==401:return RedirectResponse('/login',status_code=303)
        return templates.TemplateResponse(request=request,name='error.html',context={'user':None,'csrf':'','message':exc.detail,'status':exc.status_code},status_code=exc.status_code)

    @app.exception_handler(psycopg.Error)
    async def database_error(request,exc):
        if api(request):return JSONResponse({'detail':'База временно недоступна. Повторите действие.'},status_code=503)
        return templates.TemplateResponse(request=request,name='error.html',context={'user':None,'csrf':'','message':'База временно недоступна. Повторите действие.','status':503},status_code=503)

    @app.get('/',include_in_schema=False)
    def home():return RedirectResponse('/projects',status_code=303)

    @app.get('/login',response_class=HTMLResponse)
    def login_page(request: Request,context: Ctx):
        if context.user:return response(request,context,{})
        return render(request,context,'login.html',email='',error=None)

    @app.get('/register',response_class=HTMLResponse)
    def register_page(request: Request,context: Ctx):
        if context.user:return response(request,context,{})
        return render(request,context,'register.html',email='',name='',error=None)

    @app.get('/api/auth/csrf')
    def get_csrf(request: Request,context: Ctx):
        return cookie(JSONResponse({'csrf_token':context.session['csrf_token']}),context)

    @app.get('/docs',response_class=HTMLResponse,include_in_schema=False)
    def api_docs(request: Request,context: Ctx):
        require_user(context)
        operations=[{'path':path,'method':method.upper(),'summary':info.get('summary','')}
                    for path,methods in app.openapi()['paths'].items() if path.startswith('/api/')
                    for method,info in methods.items() if method in ('get','post','put','delete','patch')]
        return render(request,context,'api_docs.html',operations=operations)

    @app.get('/openapi.json',include_in_schema=False)
    def openapi_json(context: Ctx):
        require_user(context)
        return app.openapi()

    @app.get('/api/auth/me')
    def me(context: Ctx):return {'user':require_user(context)}

    @app.post('/register')
    @app.post('/api/auth/register')
    def register_account(request: Request,context: Ctx,name: str=Form(''),email: str=Form(''),password: str=Form(''),csrf: str=Form('')):
        csrf_check(context,csrf,request)
        if context.user:raise HTTPException(400,'Вы уже вошли в систему.')
        key=limit_key('register','',request.client.host if request.client else '')
        if is_blocked(context.connection,key):
            return JSONResponse({'detail':'Слишком много попыток. Повторите через 15 минут.'},status_code=429) if api(request) else render(request,context,'register.html',429,name=name,email=email,error='Слишком много попыток. Повторите через 15 минут.')
        try:
            name=name.strip()
            if not 2<=len(name)<=100 or any(ord(char)<32 for char in name):raise ValueError('Имя должно содержать от 2 до 100 печатных символов.')
            email=normalize_email(email)
            validate_password(password)
        except ValueError as exc:
            record_failure(context.connection,key,threshold=10)
            return cookie(JSONResponse({'detail':str(exc)},status_code=422),context) if api(request) else render(request,context,'register.html',422,name=name,email=email,error=str(exc))
        try:
            with context.connection.transaction():
                user=context.connection.execute('''INSERT INTO users(email,password_hash,name) VALUES(%s,%s,%s) RETURNING id,email,name''',(email,PH.hash(password),name)).fetchone()
        except psycopg.errors.UniqueViolation:
            record_failure(context.connection,key,threshold=10)
            return cookie(JSONResponse({'detail':'Этот email уже используется.'},status_code=409),context) if api(request) else render(request,context,'register.html',409,name=name,email=email,error='Этот email уже используется. Войдите в свою учётную запись.')
        record_failure(context.connection,key,threshold=10)
        rotate(context,dict(user))
        return response(request,context,{'user':dict(user)},status=201)

    @app.post('/login')
    @app.post('/api/auth/login')
    def login_account(request: Request,context: Ctx,email: str=Form(''),password: str=Form(''),csrf: str=Form('')):
        csrf_check(context,csrf,request)
        try:normalized=normalize_email(email)
        except ValueError:normalized=email.strip().lower()[:254]
        key=limit_key('login',normalized,request.client.host if request.client else '')
        if is_blocked(context.connection,key):
            return cookie(JSONResponse({'detail':'Слишком много попыток. Повторите через 15 минут.'},status_code=429),context) if api(request) else render(request,context,'login.html',429,email=email,error='Слишком много попыток. Повторите через 15 минут.')
        user=context.connection.execute('SELECT id,email,name,password_hash,is_active FROM users WHERE email=%s',(normalized,)).fetchone()
        encoded=user['password_hash'] if user and user['is_active'] and user['password_hash'].startswith('$argon2') else dummy_hash
        verified=verify_password(encoded,password) if len(password)<=128 else False
        if not user or not user['is_active'] or not verified or encoded==dummy_hash:
            record_failure(context.connection,key)
            return cookie(JSONResponse({'detail':'Неверный email или пароль.'},status_code=401),context) if api(request) else render(request,context,'login.html',401,email=email,error='Неверный email или пароль.')
        if PH.check_needs_rehash(user['password_hash']):
            context.connection.execute('UPDATE users SET password_hash=%s WHERE id=%s',(PH.hash(password),user['id']))
        context.connection.execute('DELETE FROM web_auth_attempts WHERE key_hash=%s',(key,))
        public={key:user[key] for key in ('id','email','name')}
        rotate(context,public)
        return response(request,context,{'user':public})

    @app.post('/logout')
    @app.post('/api/auth/logout')
    def logout(request: Request,context: Ctx,csrf: str=Form('')):
        csrf_check(context,csrf,request)
        rotate(context,None)
        return response(request,context,{'logged_out':True},target='/login')

    def project_list(context):
        user=require_user(context)
        return [dict(row) for row in context.connection.execute('''SELECT p.id,p.title,p.goal,p.budget,p.status,m.role::text AS role,
            (SELECT count(*) FROM alternatives a WHERE a.project_id=p.id) AS alternatives_count
            FROM projects p JOIN project_members m ON m.project_id=p.id WHERE m.user_id=%s ORDER BY p.updated_at DESC,p.id DESC''',(user['id'],)).fetchall()]

    @app.get('/projects',response_class=HTMLResponse)
    def projects_page(request: Request,context: Ctx):
        return render(request,context,'projects.html',projects=project_list(context),error=None)

    @app.get('/api/projects')
    def projects_api(context: Ctx):return {'projects':project_list(context)}

    @app.post('/projects')
    @app.post('/api/projects')
    def create_project(request: Request,context: Ctx,title: str=Form(''),goal: str=Form(''),budget: str=Form(''),csrf: str=Form('')):
        user=require_user(context);csrf_check(context,csrf,request)
        try:
            title=title.strip();goal=goal.strip()
            if not 2<=len(title)<=200 or len(goal)>2000:raise ValueError('Название — 2–200 символов, цель — до 2000 символов.')
            amount=None
            if budget.strip():
                amount=float(Decimal(budget.strip().replace(',','.')))
                if not 0<amount<=1e12:raise ValueError('Введите положительный бюджет до 1 трлн рублей.')
        except (ValueError,InvalidOperation,OverflowError) as exc:
            message=str(exc) if isinstance(exc,ValueError) and str(exc) else 'Введите корректный бюджет.'
            return cookie(JSONResponse({'detail':message},status_code=422),context) if api(request) else render(request,context,'projects.html',422,projects=project_list(context),error=message)
        project=context.connection.execute('INSERT INTO projects(owner_id,title,goal,budget) VALUES(%s,%s,%s,%s) RETURNING id',(user['id'],title,goal,amount)).fetchone()
        context.connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'analyst')",(project['id'],user['id']))
        with context.connection.cursor() as cursor:
            cursor.executemany('''INSERT INTO criteria(project_id,code,name,unit,direction,source,sort_order) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                [(project['id'],*criterion,index) for index,criterion in enumerate(CRITERIA)])
        context.connection.execute("INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id) VALUES(%s,%s,'web_project_create','project',%s)",(project['id'],user['id'],str(project['id'])))
        return response(request,context,{'project_id':project['id']},status=201,target=f"/projects/{project['id']}")

    def detail(context,project_id):
        project=project_access(context,project_id)
        criteria=[dict(row) for row in context.connection.execute('SELECT id,code,name,unit,direction::text AS direction,sort_order FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(project_id,)).fetchall()]
        alternatives=[dict(row) for row in context.connection.execute('SELECT id,code,name,features FROM alternatives WHERE project_id=%s ORDER BY sort_order,id',(project_id,)).fetchall()]
        scores=[dict(row) for row in context.connection.execute('SELECT alternative_id,criterion_id,value,source::text AS source FROM scores WHERE project_id=%s',(project_id,)).fetchall()]
        matrix={(row['alternative_id'],row['criterion_id']):row for row in scores}
        for alternative in alternatives:
            alternative['cells']=[matrix.get((alternative['id'],criterion['id'])) for criterion in criteria]
        members=[]
        if project['role']=='analyst':
            members=[dict(row) for row in context.connection.execute('''SELECT u.id,u.name,u.email,u.is_active,m.role::text AS role
                FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=%s ORDER BY u.id''',(project_id,)).fetchall()]
        synthetic=context.connection.execute('''SELECT EXISTS(SELECT 1 FROM scores s
            JOIN predictions p ON p.project_id=s.project_id AND p.id=s.prediction_id
            JOIN ml_models m ON m.project_id=p.project_id AND m.id=p.model_id
            JOIN datasets d ON d.project_id=m.project_id AND d.id=m.dataset_id
            WHERE s.project_id=%s AND s.source='ml' AND d.origin='synthetic') AS present''',(project_id,)).fetchone()['present']
        return {'project':project,'criteria':criteria,'alternatives':alternatives,'members':members,'synthetic_predictions':synthetic}

    @app.get('/projects/{project_id}',response_class=HTMLResponse)
    def project_page(project_id: int,request: Request,context: Ctx):
        return render(request,context,'project.html',**detail(context,project_id))

    @app.get('/api/projects/{project_id}')
    def project_api(project_id: int,context: Ctx):return detail(context,project_id)

    @app.post('/projects/{project_id}/members')
    @app.post('/api/projects/{project_id}/members')
    def set_member(project_id: int,request: Request,context: Ctx,email: str=Form(''),role: str=Form(''),csrf: str=Form('')):
        require_user(context);csrf_check(context,csrf,request)
        project=project_access(context,project_id,analyst=True,lock=True)
        if role not in ROLES:raise HTTPException(422,'Неизвестная роль.')
        try:email=normalize_email(email)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        target=context.connection.execute('SELECT id,is_active FROM users WHERE email=%s',(email,)).fetchone()
        if not target or not target['is_active']:raise HTTPException(404,'Нужен email зарегистрированного активного пользователя.')
        previous=context.connection.execute('SELECT role::text AS role FROM project_members WHERE project_id=%s AND user_id=%s',(project_id,target['id'])).fetchone()
        if target['id']==project['owner_id'] and role!='analyst':raise HTTPException(403,'Роль владельца проекта должна оставаться «Аналитик».')
        if target['id']==context.user['id'] and (not previous or previous['role']!=role):raise HTTPException(403,'Свою роль менять нельзя.')
        if previous and previous['role']=='analyst' and role!='analyst':
            other=context.connection.execute("SELECT count(*) AS n FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=%s AND m.role='analyst' AND u.is_active AND u.id<>%s",(project_id,target['id'])).fetchone()['n']
            if not other:raise HTTPException(403,'В проекте должен остаться активный аналитик.')
        if not previous or previous['role']!=role:
            context.connection.execute('''INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,%s)
                ON CONFLICT(project_id,user_id) DO UPDATE SET role=EXCLUDED.role''',(project_id,target['id'],role))
            context.connection.execute('UPDATE projects SET settings=settings WHERE id=%s',(project_id,))
            context.connection.execute("INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details) VALUES(%s,%s,'web_member_set','user',%s,%s)",(project_id,context.user['id'],str(target['id']),Jsonb({'role':role})))
        return response(request,context,{'project_id':project_id,'role':role},target=f'/projects/{project_id}')

    return app
