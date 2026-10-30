-- Run in coursework_laptop. Read-only verification.
SELECT id, title, budget, revision, settings->>'ml_predictions_pending' AS ml_predictions_pending
FROM projects WHERE settings->>'demo_key'='laptop-variant27-demo-v1';

WITH demo AS (SELECT id FROM projects WHERE settings->>'demo_key'='laptop-variant27-demo-v1')
SELECT 'criteria' AS entity, count(*) AS actual, 5 AS expected FROM criteria WHERE project_id IN(SELECT id FROM demo)
UNION ALL SELECT 'alternatives',count(*),5 FROM alternatives WHERE project_id IN(SELECT id FROM demo)
UNION ALL SELECT 'scores',count(*),20 FROM scores WHERE project_id IN(SELECT id FROM demo)
UNION ALL SELECT 'comparisons',count(*),25 FROM comparisons WHERE project_id IN(SELECT id FROM demo)
UNION ALL SELECT 'stakeholders',count(*),4 FROM stakeholders WHERE project_id IN(SELECT id FROM demo)
UNION ALL SELECT 'project_members',count(*),3 FROM project_members WHERE project_id IN(SELECT id FROM demo);

SELECT a.code, c.code AS criterion, s.value, s.source
FROM scores s JOIN alternatives a ON a.id=s.alternative_id JOIN criteria c ON c.id=s.criterion_id
JOIN projects p ON p.id=s.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1'
ORDER BY a.sort_order,c.sort_order;

SELECT s.name,s.power,s.legitimacy,s.urgency,s.salience
FROM stakeholders s JOIN projects p ON p.id=s.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' ORDER BY s.id;
