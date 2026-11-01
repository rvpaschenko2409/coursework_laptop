"""Forms and scoped APIs for the stakeholder registry, separate from user accounts."""
from typing import Annotated
import psycopg
from fastapi import Request,Depends,HTTPException,Query
from fastapi.responses import HTMLResponse
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.alternative_validation import InvalidForm
from web_app.stakeholder_validation import ATTRIBUTES,RULE,validate,FIELDS
from web_app import stakeholder_store as store


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted(request:Request):
        form=await request.form()
        if len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for _,v in form.multi_items()):raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted)]

    def access(context,pid,write=False):
        ready(context.connection)
        return project_access(context,pid,analyst=write,lock=write)

    def editor(context,pid,sid=None):
        project=project_access(context,pid,analyst=True)
        row=store.item(context.connection,pid,sid) if sid is not None else None
        fields={k:str(row[k]) if row else '' for k in FIELDS};fields['reason']=''
        return {'project':project,'stakeholder':row,'fields':fields,'errors':{},'attributes':ATTRIBUTES,'rule':RULE}

    @app.get('/projects/{project_id}/stakeholders',response_class=HTMLResponse)
    def list_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return render(request,context,'stakeholders.html',project=project,attributes=ATTRIBUTES,**store.listing(context.connection,project_id,page))

    @app.get('/api/projects/{project_id}/stakeholders')
    def list_api(project_id:int,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return {'project':project,**store.listing(context.connection,project_id,page)}

    @app.get('/projects/{project_id}/stakeholders/history',response_class=HTMLResponse)
    def history_page(project_id:int,request:Request,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return render(request,context,'stakeholder_history.html',project=project,attributes=ATTRIBUTES,**store.history(context.connection,project_id,page))

    @app.get('/api/projects/{project_id}/stakeholders/history')
    def history_api(project_id:int,context:Ctx,page:int=Query(1,ge=1,le=100000)):
        project=project_access(context,project_id)
        return {'project':project,**store.history(context.connection,project_id,page)}

    @app.get('/projects/{project_id}/stakeholders/new',response_class=HTMLResponse)
    def create_page(project_id:int,request:Request,context:Ctx):
        return render(request,context,'stakeholder_form.html',**editor(context,project_id))

    @app.get('/api/projects/{project_id}/stakeholders/{stakeholder_id}')
    def item_api(project_id:int,stakeholder_id:int,context:Ctx):
        project=project_access(context,project_id)
        return {'project':project,'stakeholder':store.item(context.connection,project_id,stakeholder_id),'rule':RULE}

    @app.get('/projects/{project_id}/stakeholders/{stakeholder_id}/edit',response_class=HTMLResponse)
    def edit_page(project_id:int,stakeholder_id:int,request:Request,context:Ctx):
        return render(request,context,'stakeholder_form.html',**editor(context,project_id,stakeholder_id))

    def submit(request,context,pid,sid,form):
        require_user(context);csrf_check(context,form.get('csrf',''),request);access(context,pid,write=True)
        check_revision(context.connection,pid,form.get('revision'))
        previous=store.item(context.connection,pid,sid) if sid is not None else None
        if set(form)-set(FIELDS)-{'csrf','revision','reason'}:raise HTTPException(422,'Неизвестные поля заинтересованной стороны.')
        try:
            values=validate(form)
            try:
                with context.connection.transaction():result=store.save(context.connection,pid,context.user['id'],values,previous)
            except psycopg.errors.UniqueViolation:raise InvalidForm({'name':'Сторона с таким названием уже существует.'}) from None
        except InvalidForm as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc),'errors':exc.errors},422)
            data=editor(context,pid,sid);data.update(fields=form,errors=exc.errors)
            return render(request,context,'stakeholder_form.html',422,**data)
        return response(request,context,result,201 if result['created'] else 200,target=f'/projects/{pid}/stakeholders')

    @app.post('/projects/{project_id}/stakeholders')
    @app.post('/api/projects/{project_id}/stakeholders')
    def create(project_id:int,request:Request,context:Ctx,form:Posted):return submit(request,context,project_id,None,form)

    @app.post('/projects/{project_id}/stakeholders/{stakeholder_id}')
    @app.post('/api/projects/{project_id}/stakeholders/{stakeholder_id}')
    def update(project_id:int,stakeholder_id:int,request:Request,context:Ctx,form:Posted):return submit(request,context,project_id,stakeholder_id,form)

    @app.get('/projects/{project_id}/stakeholders/{stakeholder_id}/delete',response_class=HTMLResponse)
    def delete_page(project_id:int,stakeholder_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id,analyst=True)
        return render(request,context,'stakeholder_delete.html',project=project,stakeholder=store.item(context.connection,project_id,stakeholder_id),fields={},errors={})

    @app.post('/projects/{project_id}/stakeholders/{stakeholder_id}/delete')
    @app.post('/api/projects/{project_id}/stakeholders/{stakeholder_id}/delete')
    def delete(project_id:int,stakeholder_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);project=access(context,project_id,write=True)
        check_revision(context.connection,project_id,form.get('revision'))
        if set(form)-{'csrf','revision','reason','confirm_name'}:raise HTTPException(422,'Неизвестные поля удаления.')
        row=store.item(context.connection,project_id,stakeholder_id)
        try:
            with context.connection.transaction():result=store.remove(context.connection,project_id,context.user['id'],row,form.get('reason',''),form.get('confirm_name',''))
        except InvalidForm as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc),'errors':exc.errors},422)
            return render(request,context,'stakeholder_delete.html',422,project=project,stakeholder=row,fields=form,errors=exc.errors)
        return response(request,context,result,target=f'/projects/{project_id}/stakeholders')
