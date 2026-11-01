"""Project-scoped launch, history, charts and replay of sensitivity experiments."""
from typing import Annotated
from fastapi import Request,Depends,HTTPException,Query
from fastapi.responses import HTMLResponse,JSONResponse,Response
from fastapi.encoders import jsonable_encoder
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.calculation_store import load_pair
from web_app.weight_math import LABELS
from web_app import sensitivity_store as store
from web_app.sensitivity_math import step_value


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted(request:Request):
        form=await request.form()
        if len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for _,v in form.multi_items()):raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted)]

    def source(context,pid,rid):
        try:data=load_pair(context.connection,pid,rid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if data is None:raise HTTPException(404,'Исходный расчёт не найден в этом проекте.')
        return data

    def result(context,pid,rid):
        project=project_access(context,pid)
        try:data=store.load(context.connection,pid,rid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if not data:raise HTTPException(404,'Анализ не найден в этом проекте.')
        return project,data

    @app.get('/projects/{project_id}/calculations/{result_id}/sensitivity',response_class=HTMLResponse)
    def sensitivity_form(project_id:int,result_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id)
        return render(request,context,'sensitivity_form.html',project=project,data=source(context,project_id,result_id),step_percent='10',error=None)

    @app.post('/projects/{project_id}/calculations/{result_id}/sensitivity')
    @app.post('/api/projects/{project_id}/calculations/{result_id}/sensitivity')
    def launch(project_id:int,result_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);ready(context.connection)
        project=project_access(context,project_id,analyst=True,lock=True)
        check_revision(context.connection,project_id,form.get('revision'))
        if set(form)-{'csrf','revision','step_percent'}:raise HTTPException(422,'Неизвестные поля анализа.')
        try:
            step=step_value(form.get('step_percent','10'))
            with context.connection.transaction():outcome=store.run(context.connection,project_id,result_id,context.user['id'],step)
        except ValueError as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc)},422)
            return render(request,context,'sensitivity_form.html',422,project=project,data=source(context,project_id,result_id),step_percent=form.get('step_percent','10'),error=str(exc))
        return response(request,context,outcome,201 if outcome['created'] else 200,target=f"/projects/{project_id}/sensitivity/{outcome['result_id']}")

    @app.get('/projects/{project_id}/sensitivity',response_class=HTMLResponse)
    def history_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id);runs,has_next=store.history(context.connection,project_id,page)
        return render(request,context,'sensitivity_history.html',project=project,runs=runs,page=page,has_next=has_next)

    @app.get('/api/projects/{project_id}/sensitivity')
    def history_api(project_id:int,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id);runs,has_next=store.history(context.connection,project_id,page)
        return {'project':project,'runs':runs,'page':page,'has_next':has_next}

    @app.get('/projects/{project_id}/sensitivity/{result_id}',response_class=HTMLResponse)
    def result_page(project_id:int,result_id:int,request:Request,context:Ctx,scenario:str=Query('base',max_length=80)):
        project,data=result(context,project_id,result_id)
        selected=next((s for s in data['output']['scenarios'] if s['id']==scenario),None)
        if selected is None:raise HTTPException(404,'Сценарий не найден.')
        return render(request,context,'sensitivity_result.html',project=project,**data,selected=selected,labels=LABELS)

    @app.get('/api/projects/{project_id}/sensitivity/{result_id}')
    def result_api(project_id:int,result_id:int,context:Ctx):
        project,data=result(context,project_id,result_id)
        return data

    @app.post('/api/projects/{project_id}/sensitivity/{result_id}/replay')
    def replay(project_id:int,result_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        if set(form)-{'csrf'}:raise HTTPException(422,'Неизвестные поля проверки.')
        project,data=result(context,project_id,result_id)
        return {'verified':True,'is_stale':data['is_stale'],'scenario_count':len(data['output']['scenarios'])}

    @app.get('/projects/{project_id}/sensitivity/{result_id}/snapshot.json')
    def snapshot_json(project_id:int,result_id:int,context:Ctx):
        project,data=result(context,project_id,result_id)
        return JSONResponse(jsonable_encoder({'input':data['snapshot'],'output':data['output'],'record':data['record']}),
            headers={'Content-Disposition':f'attachment; filename="sensitivity_{project_id}_{result_id}.json"'})

    @app.get('/projects/{project_id}/sensitivity/{result_id}/{chart}.png')
    def chart_png(project_id:int,result_id:int,chart:str,context:Ctx):
        if chart not in ('weights','v'):raise HTTPException(404,'Диаграмма не найдена.')
        project,data=result(context,project_id,result_id)
        from web_app.sensitivity_charts import chart_bytes
        return Response(chart_bytes(data,chart),media_type='image/png')
