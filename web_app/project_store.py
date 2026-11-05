"""Project settings and independent copies of current inputs."""
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from psycopg import sql
from psycopg.types.json import Jsonb



def validate_fields(title, goal, budget):
    title = title.strip()
    goal = goal.strip()
    if not 2 <= len(title) <= 200 or len(goal) > 2000:
        raise HTTPException(422, 'Название — 2–200 символов, цель — до 2000 символов.')
    amount = None
    if budget.strip():
        try:
            amount = Decimal(budget.strip().replace(',', '.'))
        except InvalidOperation:
            raise HTTPException(422, 'Введите корректный бюджет.') from None
        if not amount.is_finite() or not 0 < amount <= Decimal('1e12'):
            raise HTTPException(422, 'Бюджет должен быть положительным и не больше 1 трлн рублей.')
    return {'title': title, 'goal': goal, 'budget': amount}


def audit(connection, project_id, actor, action, details):
    connection.execute(
        """INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
           VALUES(%s,%s,%s,'project',%s,%s)""",
        (project_id, actor, action, str(project_id),
         Jsonb(details)),
    )


def insert_row(connection, table, values, returning=True):
    """Insert only developer-selected columns, wrapping JSON values explicitly."""
    query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
        sql.Identifier(table),
        sql.SQL(',').join(map(sql.Identifier, values)),
        sql.SQL(',').join(sql.Placeholder() for _ in values),
    )
    if returning:
        query += sql.SQL(' RETURNING id')
    cursor = connection.execute(
        query, tuple(Jsonb(value) if isinstance(value, (dict, list)) else value
                     for value in values.values()),
    )
    return cursor.fetchone()['id'] if returning else None


def copy_inputs(connection, source_id, actor, fields, request_key):
    previous = connection.execute(
        """SELECT details FROM audit_events
           WHERE project_id=%s AND actor_id=%s AND action='web_project_copy'
             AND details->>'request_key'=%s""",
        (source_id, actor, request_key),
    ).fetchone()
    if previous:
        details = previous['details']
        if details['fields'] != {**fields, 'budget': str(fields['budget']) if fields['budget'] is not None else None}:
            raise HTTPException(409, 'Этот запрос уже использован для другой копии.')
        existing = connection.execute('SELECT id FROM projects WHERE id=%s',
                                      (details['copy_id'],)).fetchone()
        if not existing:
            raise HTTPException(409, 'Ранее созданная копия уже удалена. Откройте новую форму.')
        return existing['id'], False

    source = connection.execute('SELECT * FROM projects WHERE id=%s', (source_id,)).fetchone()
    project_id = insert_row(connection, 'projects', {
        'owner_id': actor, **fields, 'status': 'draft', 'settings': {key: value for key, value in source['settings'].items()
                     if not key.startswith(('demo_', 'ml_registry_')) and key != 'ml_predictions_pending'},
        'catwoe': source['catwoe'], 'root_definition': source['root_definition'],
    })
    members = connection.execute(
        'SELECT user_id,role FROM project_members WHERE project_id=%s', (source_id,),
    ).fetchall()
    for member in members:
        insert_row(connection, 'project_members', {
            'project_id': project_id, 'user_id': member['user_id'],
            'role': 'analyst' if member['user_id'] == actor else member['role'],
        }, returning=False)

    mappings = {}
    definitions = (
        ('criteria', ('code','name','unit','direction','scale_type','source','sort_order')),
        ('alternatives', ('code','name','description','features','sort_order')),
        ('stakeholders', ('name','stakeholder_role','interests','power','legitimacy','urgency')),
        ('datasets', ('name','origin','source_url','license','sha256','seed','generator_version',
                      'generation_params','quality_report','row_count','column_count','uploaded_by','uploaded_at')),
        ('ml_models', ('name','version','algorithm','task_type','feature_names','target_name','params',
                       'metrics','environment','split_manifest','seed','artifact_path','artifact_sha256',
                       'is_active','trained_at','registered_at','created_by')),
    )
    dataset_parents = []
    for table, columns in definitions:
        mapping = {}
        rows = connection.execute(sql.SQL('SELECT * FROM {} WHERE project_id=%s ORDER BY id')
                                  .format(sql.Identifier(table)), (source_id,)).fetchall()
        for row in rows:
            values = {column: row[column] for column in columns}
            values['project_id'] = project_id
            if table == 'ml_models':
                values['dataset_id'] = mappings['datasets'][row['dataset_id']]
            new_id = insert_row(connection, table, values)
            mapping[row['id']] = new_id
            if table == 'datasets':
                dataset_parents.append((new_id, row['parent_dataset_id']))
        mappings[table] = mapping

    for new_id, parent_id in dataset_parents:
        if parent_id is not None:
            connection.execute('UPDATE datasets SET parent_dataset_id=%s WHERE id=%s',
                               (mappings['datasets'][parent_id], new_id))
    for old_id, new_id in mappings['datasets'].items():
        connection.execute(
            """INSERT INTO dataset_groups(dataset_id,configuration_id,split)
               SELECT %s,configuration_id,split FROM dataset_groups WHERE dataset_id=%s""",
            (new_id, old_id),
        )
        connection.execute(
            """INSERT INTO dataset_rows(dataset_id,layer,row_number,configuration_id,split,data,provenance)
               SELECT %s,layer,row_number,configuration_id,split,data,provenance
               FROM dataset_rows WHERE dataset_id=%s""", (new_id, old_id),
        )

    predictions = {}
    rows = connection.execute(
        """SELECT DISTINCT p.* FROM predictions p
           JOIN scores s ON s.project_id=p.project_id AND s.prediction_id=p.id
           WHERE p.project_id=%s ORDER BY p.id""", (source_id,),
    ).fetchall()
    for row in rows:
        values = {column: row[column] for column in ('value','features_snapshot','explanation','created_by','created_at')}
        values.update(project_id=project_id, model_id=mappings['ml_models'][row['model_id']],
                      alternative_id=mappings['alternatives'][row['alternative_id']],
                      criterion_id=mappings['criteria'][row['criterion_id']])
        predictions[row['id']] = insert_row(connection, 'predictions', values)
    for row in connection.execute('SELECT * FROM scores WHERE project_id=%s', (source_id,)).fetchall():
        insert_row(connection, 'scores', {
            'project_id': project_id, 'alternative_id': mappings['alternatives'][row['alternative_id']],
            'criterion_id': mappings['criteria'][row['criterion_id']],
            'expert_id': row['expert_id'], 'value': row['value'], 'source': row['source'],
            'prediction_id': predictions.get(row['prediction_id']), 'override_reason': row['override_reason'],
        })
    for row in connection.execute('SELECT * FROM comparisons WHERE project_id=%s', (source_id,)).fetchall():
        insert_row(connection, 'comparisons', {
            'project_id': project_id, 'expert_id': row['expert_id'],
            'criterion_i': mappings['criteria'][row['criterion_i']],
            'criterion_j': mappings['criteria'][row['criterion_j']], 'value': row['value'],
        }, returning=False)

    details = {
        'copy_id': project_id, 'source_revision': source['revision'], 'request_key': request_key,
        'fields': {**fields, 'budget': str(fields['budget']) if fields['budget'] is not None else None},
    }
    audit(connection, source_id, actor, 'web_project_copy', details)
    audit(connection, project_id, actor, 'web_project_copy_created',
          {'source_project_id': source_id, 'source_revision': source['revision']})
    return project_id, True
