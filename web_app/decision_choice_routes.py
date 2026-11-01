"""Only a project decision maker may commit a choice from a current calculation."""
from typing import Annotated
from fastapi import Request,Depends,HTTPException,Query
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.encoders import jsonable_encoder
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.calculation_store import load_pair
from web_app.weight_math import LABELS
from web_app import decision_choice_store as store


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted(request:Request):
        form=await request.form()
        if len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for _,v in form.multi_items()):raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted)]

    def source(context,pid,rid):
        try:data=load_pair(context.connection,pid,rid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if not data:raise HTTPException(404,'Исходный расчёт не найден в этом проекте.')
        return data

    def chosen(context,pid,sid):
        project=project_access(context,pid)
        try:data=store.load(context.connection,pid,sid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if not data:raise HTTPException(404,'Решение не найдено в этом проекте.')
        return project,data

    @app.get('/projects/{project_id}/calculations/{result_id}/choice',response_class=HTMLResponse)
    def choice_page(project_id:int,result_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id)
        return render(request,context,'decision_choice_form.html',project=project,data=source(context,project_id,result_id),
            previous_choice=store.latest(context.connection,project_id),fields={},error=None)

    @app.post('/projects/{project_id}/calculations/{result_id}/choice')
    @app.post('/api/projects/{project_id}/calculations/{result_id}/choice')
    def commit(project_id:int,result_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);ready(context.connection)
        project=project_access(context,project_id,lock=True)
        if project['role']!='decision_maker':raise HTTPException(403,'Фиксировать выбор может только ЛПР этого проекта.')
        check_revision(context.connection,project_id,form.get('revision'))
        if set(form)-{'csrf','revision','alternative_code','reason','previous_choice_id','acknowledge'}:raise HTTPException(422,'Неизвестные поля решения.')
        try:
            with context.connection.transaction():result=store.run(context.connection,project_id,result_id,context.user,
                form.get('alternative_code',''),form.get('reason',''),form.get('previous_choice_id',''),form.get('acknowledge',''))
        except ValueError as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc)},422)
            return render(request,context,'decision_choice_form.html',422,project=project,data=source(context,project_id,result_id),
                previous_choice=store.latest(context.connection,project_id),fields=form,error=str(exc))
        return response(request,context,result,201 if result['created'] else 200,target=f"/projects/{project_id}/choices/{result['choice_id']}")

    @app.get('/projects/{project_id}/choices',response_class=HTMLResponse)
    def history_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return render(request,context,'decision_choice_history.html',project=project,**store.history(context.connection,project_id,page))

    @app.get('/api/projects/{project_id}/choices')
    def history_api(project_id:int,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return {'project':project,**store.history(context.connection,project_id,page)}

    @app.get('/projects/{project_id}/choices/{choice_id}',response_class=HTMLResponse)
    def choice_detail(project_id:int,choice_id:int,request:Request,context:Ctx):
        project,data=chosen(context,project_id,choice_id)
        return render(request,context,'decision_choice_detail.html',project=project,labels=LABELS,decision=data['context'],**{k:v for k,v in data.items() if k!='context'})

    @app.get('/api/projects/{project_id}/choices/{choice_id}')
    def detail_api(project_id:int,choice_id:int,context:Ctx):
        project,data=chosen(context,project_id,choice_id)
        return data

    @app.post('/api/projects/{project_id}/choices/{choice_id}/replay')
    def replay(project_id:int,choice_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        if set(form)-{'csrf'}:raise HTTPException(422,'Неизвестные поля проверки.')
        project,data=chosen(context,project_id,choice_id)
        return {'verified':True,'is_stale':data['is_stale'],'is_latest':data['is_latest'],'alternative_code':data['record']['alternative_code']}

    @app.get('/projects/{project_id}/choices/{choice_id}/snapshot.json')
    def snapshot_json(project_id:int,choice_id:int,context:Ctx):
        project,data=chosen(context,project_id,choice_id)
        return JSONResponse(jsonable_encoder({'context':data['context'],'record':data['record'],'verified':True}),
            headers={'Content-Disposition':f'attachment; filename="decision_choice_{project_id}_{choice_id}.json"'})
