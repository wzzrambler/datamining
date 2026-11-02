// Set $dataset to: p6-quality-themes-v1-1bae9157a9a3
MATCH (r:ROUTE {dataset:$dataset})-[:HAS_STEP]->(s:ROUTE_STEP) OPTIONAL MATCH (s)-[:VISITS]->(p:POI) RETURN r.id,s.index,s.step_id,collect(p.name) ORDER BY r.id,s.index;
MATCH (p:POI {dataset:$dataset})-[:HAS_FACT]->(f:FACT) WHERE f.scope_status IS NULL AND f.route_id IS NULL RETURN p.name,f.attribute,f.value_text;
MATCH (c:CONDITIONAL_RULE {dataset:$dataset}) WHERE c.scope_status IS NULL OPTIONAL MATCH (c)-[:APPLIES_TO]->(target) RETURN c.id,c.condition_json,c.consequence_text,collect(target.id);
MATCH (p:POI {dataset:$dataset})-[:HAS_TIME_SUMMARY]->(t:TIME_SUMMARY) RETURN p.name,t.attribute,t.text_union_json,t.interval_unions_json;
MATCH (e:P5Node {dataset:$dataset}) WHERE e.advice_themes_json IS NOT NULL RETURN e.id,e.name,e.scenic_code,e.advice_themes_json ORDER BY e.scenic_code,e.name;
