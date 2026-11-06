"""Project-scoped generator; the existing training confirmation owns all database writes."""
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from web_app import data_generator as generator
from web_app.alternative_store import check_revision
from web_app.security import csrf_check
from web_app.training_data_routes import public


def register_routes(app,Ctx,render,response,project_access):
    @app.get('/projects/{project_id}/training-data/generate',response_class=HTMLResponse)
    def page(project_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id,analyst=True)
        return render(request,context,'data_generator.html',project=project,fields=generator.DEFAULTS,review=None,error=None)

    @app.post('/projects/{project_id}/training-data/generate/preview')
    @app.post('/api/projects/{project_id}/training-data/generate/preview')
    async def preview(project_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id,analyst=True,lock=True)
        form=await request.form(max_files=0,max_fields=12)
        allowed=set(generator.DEFAULTS)|{'csrf','revision'}
        if set(form)!=allowed or len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for v in form.values()):
            raise HTTPException(422,'Заполните параметры генерации без повторяющихся полей.')
        csrf_check(context,form['csrf'],request)
        check_revision(context.connection,project_id,form['revision'])
        fields={k:form[k] for k in generator.DEFAULTS}
        try:
            review=await run_in_threadpool(generator.build,fields)
            token=app.state.training_preview_cache.put(project_id,context.user['id'],project['revision'],review) if review['valid'] else None
        except ValueError as exc:
            if request.url.path.startswith('/api/'):return JSONResponse({'detail':str(exc)},422)
            return render(request,context,'data_generator.html',422,project=project,fields=fields,review=None,error=str(exc))
        shown=public(review);shown['token']=token
        if request.url.path.startswith('/api/'):return JSONResponse(shown,status_code=200 if review['valid'] else 422)
        return render(request,context,'data_generator.html',200 if review['valid'] else 422,
                      project=project,fields=fields,review=shown,error=None)
