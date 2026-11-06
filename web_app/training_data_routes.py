"""Analyst upload with a bounded, expiring server-side preview and atomic confirmation."""
import json
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.datastructures import UploadFile

from web_app import training_data_store as store
from web_app.alternative_import_files import csv_bytes, xlsx_bytes
from web_app.alternative_store import check_revision
from web_app.security import csrf_check


class PreviewCache:
    """At most four previews/32 MiB per process; expires after 15 minutes."""
    def __init__(self):
        self.entries=OrderedDict();self.lock=threading.Lock();self.capacity=32*1024*1024

    def put(self,pid,actor,revision,review):
        packed=store.canonical(review).encode()
        if len(packed)>self.capacity:
            raise ValueError('Проверка занимает слишком много памяти. Уменьшите файл.')
        with self.lock:
            self._expire()
            while self.entries and (len(self.entries)>=4 or sum(len(v[4]) for v in self.entries.values())+len(packed)>self.capacity):
                self.entries.popitem(last=False)
            token=secrets.token_urlsafe(32)
            self.entries[token]=(time.monotonic()+900,pid,actor,revision,packed)
            return token

    def _expire(self):
        for token,value in list(self.entries.items()):
            if value[0]<=time.monotonic():del self.entries[token]

    def get(self,token,pid,actor):
        with self.lock:
            self._expire()
            value=self.entries.get(token)
            if not value or value[1:3]!=(pid,actor):
                raise HTTPException(409,'Предпросмотр истёк или недоступен. Загрузите файл для проверки заново.')
            return value[3],json.loads(value[4])


def public(review):
    return {k:v for k,v in review.items() if k not in ('raw','staging','semantic_sha256')}


def register_routes(app,Ctx,render,response,project_access):
    cache=PreviewCache()
    app.state.training_preview_cache=cache

    def page_data(context,pid):
        return dict(project=project_access(context,pid,analyst=True),datasets=store.listing(context.connection,pid),
                    review=None,error=None,fields={})

    @app.get('/projects/{project_id}/training-data',response_class=HTMLResponse)
    @app.get('/api/projects/{project_id}/training-data')
    def page(project_id:int,request:Request,context:Ctx):
        data=page_data(context,project_id)
        if request.url.path.startswith('/api/'):
            return response(request,context,{'datasets':data['datasets']})
        return render(request,context,'training_data.html',**data)

    @app.get('/projects/{project_id}/training-data/template.{extension}')
    def template(project_id:int,extension:str,context:Ctx):
        project_access(context,project_id,analyst=True)
        if extension not in ('csv','xlsx'):raise HTTPException(404)
        data=csv_bytes(store.HEADERS) if extension=='csv' else xlsx_bytes(store.HEADERS)
        media='text/csv; charset=utf-8' if extension=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return Response(data,media_type=media,headers={'Content-Disposition':f'attachment; filename="training_data.{extension}"','Cache-Control':'no-store'})

    @app.post('/projects/{project_id}/training-data/preview')
    @app.post('/api/projects/{project_id}/training-data/preview')
    async def preview(project_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id,analyst=True,lock=True)
        form=await request.form(max_files=1,max_fields=10,max_part_size=store.MAX_FILE)
        try:
            allowed={'csrf','revision','file','name','origin','source_url','license','seed','generator_version','reason'}
            if set(form)!=allowed or len(form)!=len(form.multi_items()) or not isinstance(form['file'],UploadFile) or any(not isinstance(form[k],str) for k in allowed-{'file'}):
                raise HTTPException(422,'Заполните паспорт данных и выберите один файл; повторяющиеся поля запрещены.')
            csrf_check(context,form['csrf'],request)
            check_revision(context.connection,project_id,form['revision'])
            content=await form['file'].read(store.MAX_FILE+1)
            filename=(form['file'].filename or '').replace('\\','/').rsplit('/',1)[-1]
            if not 1<=len(filename)<=200 or any(ord(c)<32 for c in filename):raise HTTPException(422,'Недопустимое имя файла.')
            fields={k:form[k] for k in allowed-{'csrf','revision','file'}}
            try:
                review=store.inspect(content,filename,fields)
                token=cache.put(project_id,context.user['id'],project['revision'],review) if review['valid'] else None
            except ValueError as exc:
                if request.url.path.startswith('/api/'):return JSONResponse({'detail':str(exc)},422)
                data=page_data(context,project_id);data.update(error=str(exc),fields=fields)
                return render(request,context,'training_data.html',422,**data)
            shown=public(review);shown['token']=token
            if request.url.path.startswith('/api/'):return JSONResponse(shown,status_code=200 if review['valid'] else 422)
            data=page_data(context,project_id);data.update(review=shown,fields=fields)
            return render(request,context,'training_data.html',200 if review['valid'] else 422,**data)
        finally:await form.close()

    @app.post('/projects/{project_id}/training-data/confirm')
    @app.post('/api/projects/{project_id}/training-data/confirm')
    async def confirm(project_id:int,request:Request,context:Ctx):
        project_access(context,project_id,analyst=True,lock=True)
        form=await request.form(max_files=0,max_fields=2)
        if set(form)!={'csrf','token'} or len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for v in form.values()):raise HTTPException(422,'Некорректное подтверждение.')
        csrf_check(context,form['csrf'],request)
        revision,review=cache.get(form['token'],project_id,context.user['id'])
        # Allow a successful replay despite a later revision; no new write occurs.
        previous=context.connection.execute('SELECT id FROM datasets WHERE project_id=%s AND sha256=%s',(project_id,review['file_sha256'])).fetchone()
        if previous:
            result={'dataset_id':previous['id'],'created':False}
        else:
            check_revision(context.connection,project_id,revision)
            with context.connection.transaction():result=store.save(context.connection,project_id,context.user['id'],review)
        return response(request,context,result,status=201 if result['created'] else 200,target=f'/projects/{project_id}/training-data')
