"""Two-stage analyst import. Every write rechecks session, CSRF, role and project revision."""
import secrets
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.datastructures import UploadFile
from web_app import alternative_import_store as store
from web_app.alternative_import_files import MAX_FILE, csv_bytes, xlsx_bytes
from web_app.alternative_store import check_revision, ready
from web_app.security import csrf_check


def register_routes(app, Ctx, render, response, project_access):
    key = secrets.token_bytes(32)

    def editor(context, pid):
        project = project_access(context, pid, analyst=True)
        ready(context.connection)
        from web_app.criterion_store import manual_fields
        return dict(project=project, columns=store.columns(context.connection,pid),
                    manual=manual_fields(context.connection,pid), review=None, error=None)

    @app.get('/projects/{project_id}/alternatives/import', response_class=HTMLResponse)
    def page(project_id: int, request: Request, context: Ctx):
        return render(request, context, 'alternative_import.html', **editor(context, project_id))

    @app.get('/projects/{project_id}/alternatives/import/template.{extension}')
    def template(project_id: int, extension: str, context: Ctx):
        project_access(context, project_id, analyst=True)
        if extension not in ('csv','xlsx'):raise HTTPException(404)
        headers=store.columns(context.connection,project_id)
        content=csv_bytes(headers) if extension=='csv' else xlsx_bytes(headers)
        media='text/csv; charset=utf-8' if extension=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return Response(content,media_type=media,headers={'Content-Disposition':f'attachment; filename="laptops_project_{project_id}.{extension}"','Cache-Control':'no-store'})

    @app.post('/projects/{project_id}/alternatives/import/preview')
    @app.post('/api/projects/{project_id}/alternatives/import/preview')
    async def preview(project_id: int, request: Request, context: Ctx):
        project=project_access(context,project_id,analyst=True,lock=True)
        ready(context.connection)
        form=await request.form(max_files=1,max_fields=4,max_part_size=MAX_FILE)
        try:
            if len(form)!=len(form.multi_items()) or set(form)!={'csrf','revision','reason','file'}:
                raise HTTPException(422,'Нужны файл, причина импорта и действующая форма без повторяющихся полей.')
            if not all(isinstance(form[x],str) for x in ('csrf','revision','reason')) or not isinstance(form['file'],UploadFile):
                raise HTTPException(422,'Некорректная форма загрузки.')
            csrf_check(context,form['csrf'],request)
            check_revision(context.connection,project_id,form['revision'])
            content=await form['file'].read(MAX_FILE+1)
            filename=(form['file'].filename or '').replace('\\','/').rsplit('/',1)[-1]
            if not 1<=len(filename)<=200 or any(ord(c)<32 for c in filename):
                raise HTTPException(422,'Недопустимое имя файла.')
            try:
                review=store.preview(context.connection,project_id,context.user['id'],project['revision'],content,filename,form['reason'],key)
            except ValueError as exc:
                if request.url.path.startswith('/api/'):return JSONResponse({'detail':str(exc)},422)
                data=editor(context,project_id);data['error']=str(exc)
                return render(request,context,'alternative_import.html',422,**data)
            if request.url.path.startswith('/api/'):
                return JSONResponse(review,status_code=200 if review['valid'] else 422)
            data=editor(context,project_id);data['review']=review
            return render(request,context,'alternative_import.html',200 if review['valid'] else 422,**data)
        finally:
            await form.close()

    @app.post('/projects/{project_id}/alternatives/import/confirm')
    @app.post('/api/projects/{project_id}/alternatives/import/confirm')
    async def confirm(project_id: int, request: Request, context: Ctx):
        project_access(context,project_id,analyst=True,lock=True)
        ready(context.connection)
        form=await request.form(max_files=0,max_fields=2,max_part_size=128*1024)
        if len(form)!=len(form.multi_items()) or set(form)!={'csrf','token'} or any(not isinstance(v,str) for v in form.values()):
            raise HTTPException(422,'Некорректное подтверждение импорта.')
        csrf_check(context,form['csrf'],request)
        try:
            with context.connection.transaction():
                result=store.confirm(context.connection,project_id,context.user['id'],form['token'],key)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        return response(request,context,result,201 if result['created'] else 200,target=f'/projects/{project_id}')
