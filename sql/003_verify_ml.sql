-- Run in coursework_laptop. Read-only checks.
SELECT version_num FROM alembic_version;

SELECT m.id,m.name,m.version,m.is_active,m.trained_at,m.registered_at,
       (m.metrics->>'cv_mae')::double precision AS cv_mae,
       (m.metrics->>'test_mae')::double precision AS test_mae,
       (m.metrics->>'test_rmse')::double precision AS test_rmse,
       (m.metrics->>'test_r2')::double precision AS test_r2
FROM ml_models m JOIN projects p ON p.id=m.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' ORDER BY m.name;

SELECT r.layer,r.split,count(*) AS stored_rows
FROM dataset_rows r JOIN datasets d ON d.id=r.dataset_id JOIN projects p ON p.id=d.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' GROUP BY r.layer,r.split ORDER BY r.layer,r.split;

SELECT a.code,s.value AS performance,s.source,s.prediction_id,m.name AS model,m.version AS model_version
FROM scores s JOIN criteria c ON c.id=s.criterion_id JOIN alternatives a ON a.id=s.alternative_id
JOIN predictions pr ON pr.id=s.prediction_id JOIN ml_models m ON m.id=pr.model_id JOIN projects p ON p.id=s.project_id
WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1' AND c.code='performance_score' ORDER BY a.sort_order;

SELECT count(*) AS scores, count(*) FILTER (WHERE s.source='ml') AS ml_scores
FROM scores s JOIN projects p ON p.id=s.project_id WHERE p.settings->>'demo_key'='laptop-variant27-demo-v1';
