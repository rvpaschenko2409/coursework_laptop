"""Project-scoped calculation launch, verified results and private charts."""
from typing import Annotated
from fastapi import Request,Depends,HTTPException,Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse,JSONResponse,Response
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.calculation_store import parse_v,run_for_user,history,load_pair


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted_form(request:Request):
        form=await request.form()
        if len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for _,v in form.multi_items()):raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted_form)]

    def listing(context,pid,page=1):
        project=project_access(context,pid)
        runs,has_next=history(context.connection,pid,page)
        stats=context.connection.execute('''SELECT
            (SELECT count(*) FROM alternatives WHERE project_id=%s) AS alternatives,
            (SELECT count(*) FROM criteria WHERE project_id=%s) AS criteria,
            (SELECT count(*) FROM scores WHERE project_id=%s) AS scores''',(pid,pid,pid)).fetchone()
        from web_app.criterion_store import items as active_criteria
        current=active_criteria(context.connection,pid)
        stats=dict(stats);stats['criteria']=len(current)
        stats['scores']=context.connection.execute('SELECT count(*) AS n FROM scores WHERE project_id=%s AND criterion_id=ANY(%s)',(pid,[c['id'] for c in current])).fetchone()['n']
        return {'project':project,'runs':runs,'has_next':has_next,'page':page,'stats':dict(stats),'error':None,'v':'0.5'}

    def pair(context,pid,rid):
        project=project_access(context,pid)
        try:data=load_pair(context.connection,pid,rid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if data is None:raise HTTPException(404,'Результат не найден в этом проекте.')
        return project,data

    @app.get('/projects/{project_id}/calculations',response_class=HTMLResponse)
    def calculations_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        return render(request,context,'calculations.html',**listing(context,project_id,page))

    @app.get('/api/projects/{project_id}/calculations')
    def calculations_api(project_id:int,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        return listing(context,project_id,page)

    @app.post('/projects/{project_id}/calculations')
    @app.post('/api/projects/{project_id}/calculations')
    def calculate(project_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        ready(context.connection)
        project_access(context,project_id,analyst=True,lock=True)
        check_revision(context.connection,project_id,form.get('revision'))
        if set(form)-{'v','csrf','revision'}:raise HTTPException(422,'Неизвестные поля расчёта.')
        try:
            v=parse_v(form.get('v','0.5'))
            with context.connection.transaction():result=run_for_user(context.connection,project_id,context.user['id'],v)
        except ValueError as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc)},422)
            data=listing(context,project_id);data.update(error=str(exc),v=form.get('v','0.5'))
            return render(request,context,'calculations.html',422,**data)
        return response(request,context,result,201 if result['created'] else 200,target=f"/projects/{project_id}/calculations/{result['result_ids']['marcos']}")

    @app.get('/projects/{project_id}/calculations/{result_id}',response_class=HTMLResponse)
    def result_page(project_id:int,result_id:int,request:Request,context:Ctx):
        project,data=pair(context,project_id,result_id)
        return render(request,context,'calculation_result.html',project=project,**data)

    @app.get('/api/projects/{project_id}/calculations/{result_id}')
    def result_api(project_id:int,result_id:int,context:Ctx):
        project,data=pair(context,project_id,result_id)
        return data

    @app.post('/api/projects/{project_id}/calculations/{result_id}/replay')
    def replay(project_id:int,result_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        project,data=pair(context,project_id,result_id)
        return {'verified':True,'is_stale':data['is_stale'],'project_revision':data['snapshot']['project']['revision'],
            'current_revision':data['current_revision'],'result_ids':{k:r['id'] for k,r in data['results'].items()}}

    @app.get('/projects/{project_id}/calculations/{result_id}/snapshot.json')
    def snapshot_json(project_id:int,result_id:int,context:Ctx):
        project,data=pair(context,project_id,result_id)
        return JSONResponse(jsonable_encoder({'input':data['snapshot'],'output':data['calculated'],'results':data['results']}),
            headers={'Content-Disposition':f'attachment; filename="calculation_{project_id}_{result_id}.json"'})

    @app.get('/projects/{project_id}/calculations/{result_id}/{chart}.png')
    def chart_png(project_id:int,result_id:int,chart:str,context:Ctx):
        if chart not in ('weights','ranks'):raise HTTPException(404,'Диаграмма не найдена.')
        project,data=pair(context,project_id,result_id)
        from web_app.calculation_charts import chart_bytes
        return Response(chart_bytes(data,chart),media_type='image/png')
