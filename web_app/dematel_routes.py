"""Scoped CATWOE, individual assessments, aggregation and historical DEMATEL."""
from typing import Annotated
from fastapi import Request,Depends,HTTPException,Query
from fastapi.responses import HTMLResponse,Response,JSONResponse
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from web_app import dematel_store as store
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.alternative_validation import InvalidForm


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted(request:Request):
        raw=await request.form()
        if any(not isinstance(v,str) for _,v in raw.multi_items()) or any(len(raw.getlist(k))>1 for k in raw if k!='expert_ids'):
            raise HTTPException(422,'Поля формы повторяются или содержат файлы.')
        form=dict(raw)
        if 'expert_ids' in raw:form['expert_ids']=raw.getlist('expert_ids')
        return form
    Posted=Annotated[dict,Depends(posted)]

    def access(context,pid,write=False,assessment=False):
        project=project_access(context,pid,lock=write)
        if write:
            ready(context.connection)
            if project['status']=='archived':raise HTTPException(403,'Архивный проект доступен только для просмотра.')
            if project['role'] not in (('analyst','expert') if assessment else ('analyst',)):raise HTTPException(403,'У вашей роли нет права на это действие.')
        return project

    def overview(context,pid,page=1):
        project=access(context,pid);items=store.criteria(context.connection,pid)
        candidates=store.sources(context.connection,pid,items) if project['role']=='analyst' else []
        history=[dict(r) for r in context.connection.execute('''SELECT s.id,s.project_revision,s.created_at,u.name AS actor_name
            FROM structure_models s JOIN users u ON u.id=s.created_by WHERE s.project_id=%s AND s.method='dematel'
            ORDER BY s.id DESC LIMIT 21 OFFSET %s''',(pid,(page-1)*20)).fetchall()]
        definition=dict(context.connection.execute('SELECT catwoe,root_definition FROM projects WHERE id=%s',(pid,)).fetchone())
        return {'project':project,'criteria':items,'candidates':candidates,'selected':[r['id'] for r in candidates if r['complete']][:20],
            'history':history[:20],'has_next':len(history)>20,'page':page,'error':None,'threshold':'','definition':definition}

    def matrix_data(context,pid):
        project=access(context,pid)
        if project['role'] not in ('analyst','expert'):raise HTTPException(403,'Ввод матрицы доступен аналитику и эксперту.')
        items=store.criteria(context.connection,pid)
        existing={(r['criterion_i'],r['criterion_j']):r['value'] for r in context.connection.execute('SELECT * FROM comparisons WHERE project_id=%s AND expert_id=%s',(pid,context.user['id'])).fetchall()}
        return {'project':project,'criteria':items,'fields':{f'cell_{i["id"]}_{j["id"]}':str(existing.get((i['id'],j['id']),0)) for i in items for j in items},'errors':{}}

    def definition_data(context,pid):
        project=access(context,pid);row=context.connection.execute('SELECT catwoe,root_definition FROM projects WHERE id=%s',(pid,)).fetchone()
        return {'project':project,'fields':{**row['catwoe'],'root_definition':row['root_definition']},'errors':{},'catwoe_fields':store.CATWOE}

    @app.get('/projects/{project_id}/dematel',response_class=HTMLResponse)
    def dematel_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        return render(request,context,'dematel.html',**overview(context,project_id,page))

    @app.get('/projects/{project_id}/dematel/matrix',response_class=HTMLResponse)
    def matrix_page(project_id:int,request:Request,context:Ctx):return render(request,context,'dematel_matrix.html',**matrix_data(context,project_id))

    @app.post('/projects/{project_id}/dematel/matrix')
    @app.post('/api/projects/{project_id}/dematel/matrix')
    def save_matrix(project_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);access(context,project_id,True,True)
        check_revision(context.connection,project_id,form.get('revision'))
        try:
            items=store.criteria(context.connection,project_id);matrix,reason=store.matrix_form(form,items)
            with context.connection.transaction():result=store.save_matrix(context.connection,project_id,context.user['id'],items,matrix,reason)
        except InvalidForm as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'errors':exc.errors},422)
            data=matrix_data(context,project_id);data.update(fields=form,errors=exc.errors)
            return render(request,context,'dematel_matrix.html',422,**data)
        return response(request,context,result,target=f'/projects/{project_id}/dematel')

    @app.get('/projects/{project_id}/dematel/definition',response_class=HTMLResponse)
    def definition_page(project_id:int,request:Request,context:Ctx):return render(request,context,'dematel_definition.html',**definition_data(context,project_id))

    @app.post('/projects/{project_id}/dematel/definition')
    @app.post('/api/projects/{project_id}/dematel/definition')
    def save_definition(project_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);access(context,project_id,True)
        check_revision(context.connection,project_id,form.get('revision'))
        allowed={'csrf','revision','reason','root_definition',*store.CATWOE}
        errors={}
        if set(form)-allowed:errors['definition']='Неизвестные поля CATWOE.'
        values={k:form.get(k,'').strip() for k in store.CATWOE};root=form.get('root_definition','').strip();reason=form.get('reason','').strip()
        for k,v in values.items():
            if not 1<=len(v)<=500 or any(ord(c)<32 and c not in '\n\r\t' for c in v):errors[k]='Заполните поле: до 500 печатных символов.'
        if not 1<=len(root)<=1500 or any(ord(c)<32 and c not in '\n\r\t' for c in root):errors['root_definition']='Корневое определение: от 1 до 1500 символов.'
        if not 3<=len(reason)<=500 or any(ord(c)<32 and c not in '\n\r\t' for c in reason):errors['reason']='Причина сохранения: 3–500 символов.'
        if errors:
            if request.url.path.startswith('/api/'):return response(request,context,{'errors':errors},422)
            data=definition_data(context,project_id);data.update(fields=form,errors=errors)
            return render(request,context,'dematel_definition.html',422,**data)
        old=context.connection.execute('SELECT catwoe,root_definition FROM projects WHERE id=%s',(project_id,)).fetchone()
        changed=old['catwoe']!=values or old['root_definition']!=root
        if changed:
            with context.connection.transaction():
                context.connection.execute('UPDATE projects SET catwoe=%s,root_definition=%s WHERE id=%s',(Jsonb(values),root,project_id))
                context.connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
                    VALUES(%s,%s,'web_catwoe_edit','project',%s,%s)''',(project_id,context.user['id'],str(project_id),Jsonb({'reason':reason,'before':dict(old),'after':{'catwoe':values,'root_definition':root}})))
        return response(request,context,{'changed':changed},target=f'/projects/{project_id}/dematel')

    @app.post('/projects/{project_id}/dematel')
    @app.post('/api/projects/{project_id}/dematel')
    def calculate(project_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);access(context,project_id,True)
        check_revision(context.connection,project_id,form.get('revision'))
        try:
            if set(form)-{'csrf','revision','expert_ids','threshold'}:raise ValueError('В форме есть неизвестные параметры.')
            selected=[int(x) for x in form.get('expert_ids',[])];threshold=store.threshold_value(form.get('threshold',''))
            with context.connection.transaction():result=store.run(context.connection,project_id,context.user['id'],selected,threshold)
        except ValueError as exc:
            message=str(exc) if not str(exc).startswith('invalid literal') else 'Выберите матрицы из списка проекта.'
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':message},422)
            data=overview(context,project_id);data.update(error=message,threshold=form.get('threshold',''),selected=[int(x) for x in form.get('expert_ids',[]) if x.isdigit()])
            return render(request,context,'dematel.html',422,**data)
        return response(request,context,result,201 if result['created'] else 200,target=f"/projects/{project_id}/dematel/results/{result['structure_id']}")

    def result(context,pid,rid):
        project=access(context,pid)
        try:data=store.load(context.connection,pid,rid)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if data is None:raise HTTPException(404,'Результат DEMATEL не найден в этом проекте.')
        return project,data

    @app.get('/projects/{project_id}/dematel/results/{structure_id}',response_class=HTMLResponse)
    def result_page(project_id:int,structure_id:int,request:Request,context:Ctx):
        project,data=result(context,project_id,structure_id)
        return render(request,context,'dematel_result.html',project=project,**data)

    @app.get('/api/projects/{project_id}/dematel/results/{structure_id}')
    def result_api(project_id:int,structure_id:int,context:Ctx):return result(context,project_id,structure_id)[1]

    @app.get('/projects/{project_id}/dematel/results/{structure_id}/{kind}.png')
    def chart(project_id:int,structure_id:int,kind:str,context:Ctx):
        if kind not in ('map','network'):raise HTTPException(404,'Диаграмма не найдена.')
        project,data=result(context,project_id,structure_id)
        from web_app.dematel_charts import chart_bytes
        return Response(chart_bytes(data,kind),media_type='image/png')

    @app.get('/projects/{project_id}/dematel/results/{structure_id}/snapshot.json')
    def snapshot(project_id:int,structure_id:int,context:Ctx):
        project,data=result(context,project_id,structure_id)
        return JSONResponse(jsonable_encoder(data),headers={'Content-Disposition':f'attachment; filename="dematel_{project_id}_{structure_id}.json"'})
