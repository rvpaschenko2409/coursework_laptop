-- Run in coursework_laptop. Read-only checks.
SELECT r.id,r.method,r.status,r.project_revision,p.revision AS current_revision,
       r.project_revision<>p.revision AS is_stale,r.params->>'weight_set_id' AS weight_set_id,r.created_at
FROM results r JOIN projects p ON p.id=r.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' ORDER BY r.id;

SELECT ws.id AS weight_set_id,c.code,w.value
FROM weights w JOIN weight_sets ws ON ws.id=w.weight_set_id JOIN criteria c ON c.id=w.criterion_id
JOIN projects p ON p.id=ws.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' ORDER BY ws.id,c.sort_order;

SELECT r.id,r.method,item->>'alternative_code' AS alternative,
       (item->>'rank')::integer AS rank,item->>'utility' AS utility,item->>'Q' AS q
FROM results r JOIN projects p ON p.id=r.project_id
CROSS JOIN LATERAL jsonb_array_elements(r.output->'ranking') AS item
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' AND r.status='succeeded'
ORDER BY r.id,(item->>'rank')::integer;

SELECT r.id,r.output->'conditions' AS vikor_conditions
FROM results r JOIN projects p ON p.id=r.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' AND r.method='vikor' ORDER BY r.id;
