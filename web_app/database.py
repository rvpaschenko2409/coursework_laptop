"""Database connections and explicit grants for the non-owner web role."""
from dataclasses import dataclass, field
import psycopg
from psycopg import sql
from psycopg.rows import dict_row


@dataclass
class Database:
    dsn: str=field(repr=False)
    expected_role: str='coursework_app'

    def connect(self):
        return psycopg.connect(self.dsn,row_factory=dict_row)

    def verify(self):
        with self.connect() as connection:
            role=connection.execute('''SELECT current_user AS name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolbypassrls
                FROM pg_roles r WHERE r.rolname=current_user''').fetchone()
            if role['name']!=self.expected_role or any(role[key] for key in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls')):
                raise ValueError('Веб-сервер должен работать от отдельной непривилегированной роли приложения.')
            if connection.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']!='0002':
                raise ValueError('Нужна схема ядра 0002.')
            if connection.execute('SELECT version_num FROM web_alembic_version').fetchone()['version_num'] not in ('web0001','web0002','web0003'):
                raise ValueError('Нужна подготовленная веб-схема web0001–web0003.')
            if connection.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE') AS allowed").fetchone()['allowed']:
                raise ValueError('Роль приложения не должна иметь CREATE в рабочей схеме.')
            connection.execute('DELETE FROM web_sessions WHERE expires_at<=now()')
            connection.execute("DELETE FROM web_auth_attempts WHERE window_started_at<now()-interval '1 day'")


def grant_web_permissions(connection,schema: str='public',role: str='coursework_app') -> None:
    info=connection.execute('SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
    if not info or not info['rolcanlogin'] or any(info[key] for key in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls')):
        raise ValueError('Сначала создайте coursework_app с Can login=Yes и без административных привилегий.')
    if connection.execute('SELECT has_schema_privilege(%s,%s,\'CREATE\') AS allowed',(role,schema)).fetchone()['allowed']:
        raise ValueError('У роли приложения есть CREATE в схеме. Уберите это право от имени владельца схемы.')
    if not connection.execute('SELECT has_schema_privilege(%s,%s,\'USAGE\') AS allowed',(role,schema)).fetchone()['allowed']:
        raise ValueError('Владелец схемы должен выдать coursework_app право USAGE.')
    def grant(privileges,table):
        connection.execute(sql.SQL('GRANT '+privileges+' ON TABLE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    for table in ('alembic_version','web_alembic_version','alternatives','criteria','scores','predictions','ml_models','datasets','results','weight_sets','weights','stakeholders'):
        grant('SELECT',table)
    grant('SELECT','users')
    grant('INSERT(email,password_hash,name)','users')
    grant('UPDATE(password_hash)','users')
    grant('SELECT','projects')
    grant('INSERT(owner_id,title,goal,budget)','projects')
    grant('UPDATE(title,goal,budget,status,settings,catwoe,root_definition,updated_at)','projects')
    grant('SELECT, INSERT','criteria')
    grant('SELECT, INSERT','project_members')
    grant('UPDATE(role)','project_members')
    grant('SELECT, INSERT','audit_events')
    for table in ('web_sessions','web_auth_attempts'):
        grant('SELECT, INSERT, UPDATE, DELETE',table)
    for sequence in ('users_id_seq','projects_id_seq','criteria_id_seq','audit_events_id_seq'):
        connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(sequence),sql.Identifier(role)))

    if connection.execute("SELECT to_regclass(%s) AS name",(schema+'.web_deleted_alternatives',)).fetchone()['name'] is not None:
        grant('INSERT(project_id,code,name,description,features,sort_order)','alternatives')
        grant('UPDATE(code,name,description,features)','alternatives')
        grant('DELETE','alternatives')
        grant('INSERT(project_id,alternative_id,criterion_id,expert_id,value,source,prediction_id,override_reason)','scores')
        grant('UPDATE(value,source,prediction_id,expert_id,override_reason)','scores')
        grant('DELETE','scores')
        grant('INSERT(project_id,model_id,alternative_id,criterion_id,value,features_snapshot,explanation,created_by)','predictions')
        grant('DELETE','predictions')
        grant('SELECT,INSERT,DELETE','score_history')
        grant('SELECT,INSERT','web_deleted_alternatives')
        for sequence in ('alternatives_id_seq','scores_id_seq','predictions_id_seq','score_history_id_seq','web_deleted_alternatives_id_seq'):
            connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(sequence),sql.Identifier(role)))

    grant('INSERT(project_id,method,alpha,input_snapshot,project_revision,created_by)','weight_sets')
    # The existing guard_weight_identity trigger locks its parent row FOR UPDATE.
    # Column-level UPDATE permits the lock; historical replay also verifies alpha.
    grant('UPDATE(alpha)','weight_sets')
    grant('INSERT(project_id,weight_set_id,criterion_id,value)','weights')
    grant('INSERT(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at)','results')
    for sequence in ('weight_sets_id_seq','results_id_seq'):
        connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(sequence),sql.Identifier(role)))

    grant('SELECT,INSERT','comparisons')
    grant('UPDATE(value)','comparisons')
    grant('SELECT','structure_models')
    grant('INSERT(project_id,method,input,output,project_revision,created_by)','structure_models')
    connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier('structure_models_id_seq'),sql.Identifier(role)))

    grant('INSERT(project_id,name,stakeholder_role,interests,power,legitimacy,urgency)','stakeholders')
    grant('UPDATE(name,stakeholder_role,interests,power,legitimacy,urgency)','stakeholders')
    grant('DELETE','stakeholders')
    connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier('stakeholders_id_seq'),sql.Identifier(role)))

    grant('SELECT','decision_choices')
    grant('INSERT(project_id,result_id,alternative_code,alternative_snapshot,decided_by,reason)','decision_choices')
    connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier('decision_choices_id_seq'),sql.Identifier(role)))


def grant_project_management_permissions(connection, schema='public', role='coursework_app'):
    """Additional grants for copying current project inputs and deleting a project."""
    # Validate the role and preserve the grants required by earlier features.
    grant_web_permissions(connection, schema, role)
    permissions = {
        'projects': 'DELETE, INSERT(owner_id,title,goal,budget,status,settings,catwoe,root_definition)',
        'datasets': 'SELECT, INSERT, UPDATE(parent_dataset_id)',
        'dataset_groups': 'SELECT, INSERT',
        'dataset_rows': 'SELECT, INSERT',
        'ml_models': 'SELECT, INSERT',
        'predictions': 'INSERT(created_at)',
        'criteria': 'INSERT(scale_type)',
        'alternatives': 'INSERT(sort_order)',
    }
    for table, privileges in permissions.items():
        connection.execute(sql.SQL('GRANT ' + privileges + ' ON TABLE {}.{} TO {}').format(
            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    for sequence in ('datasets_id_seq', 'dataset_rows_id_seq', 'ml_models_id_seq'):
        connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(
            sql.Identifier(schema), sql.Identifier(sequence), sql.Identifier(role)))


def grant_criteria_permissions(connection, schema='public', role='coursework_app'):
    """Add metadata editing; removal retains rows referenced by saved results."""
    grant_project_management_permissions(connection, schema, role)
    connection.execute(sql.SQL('GRANT UPDATE(name,unit,direction) ON TABLE {}.criteria TO {}').format(
        sql.Identifier(schema), sql.Identifier(role)))


def grant_model_training_permissions(connection, schema='public', role='coursework_app'):
    """Allow durable training jobs and explicit activation; model identities stay read-only."""
    grant_criteria_permissions(connection,schema,role)
    connection.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE(status,progress,output,error,cancel_requested,started_at,finished_at) ON TABLE {}.web_training_runs TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    connection.execute(sql.SQL('GRANT UPDATE(is_active) ON TABLE {}.ml_models TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
