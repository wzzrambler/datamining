// Set $dataset to: p6-quality-themes-v1-7945f3e47bb8
MATCH (n:P5Node {dataset:$dataset}) RETURN labels(n),count(*);
MATCH ()-[r]->() WHERE r.dataset=$dataset RETURN type(r),count(*);
