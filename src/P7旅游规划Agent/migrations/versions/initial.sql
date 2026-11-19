CREATE SCHEMA p6_kg;
CREATE SCHEMA p7_app;
CREATE TABLE p6_kg.dataset (
 dataset_id text PRIMARY KEY, graph_sha256 text NOT NULL, node_count int NOT NULL,
 edge_count int NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 manifest jsonb NOT NULL, is_active boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX p6_active_dataset ON p6_kg.dataset(is_active) WHERE is_active;
CREATE TABLE p6_kg.artifact (
 dataset_id text REFERENCES p6_kg.dataset, artifact_key text NOT NULL,
 source_relative_path text NOT NULL, raw_sha256 text NOT NULL, normalized_sha256 text,
 record_count int, PRIMARY KEY(dataset_id,artifact_key)
);
CREATE TABLE p6_kg.node (
 dataset_id text REFERENCES p6_kg.dataset, node_id text, label text NOT NULL,
 properties jsonb NOT NULL, PRIMARY KEY(dataset_id,node_id)
);
CREATE INDEX p6_node_label ON p6_kg.node(dataset_id,label);
CREATE TABLE p6_kg.edge (
 dataset_id text, edge_no int CHECK(edge_no > 0), source_id text NOT NULL, target_id text NOT NULL,
 type text NOT NULL, properties jsonb NOT NULL, PRIMARY KEY(dataset_id,edge_no),
 FOREIGN KEY(dataset_id,source_id) REFERENCES p6_kg.node,
 FOREIGN KEY(dataset_id,target_id) REFERENCES p6_kg.node
);
CREATE INDEX p6_source ON p6_kg.edge(dataset_id,source_id,type);
CREATE INDEX p6_target ON p6_kg.edge(dataset_id,target_id,type);
CREATE TABLE p6_kg.source_document (
 dataset_id text REFERENCES p6_kg.dataset, doc_id text, scenic_code text NOT NULL,
 title text NOT NULL, source_url text, published_text text, content text NOT NULL,
 paragraphs jsonb NOT NULL, PRIMARY KEY(dataset_id,doc_id)
);
CREATE TABLE p6_kg.scenic_area (
 dataset_id text REFERENCES p6_kg.dataset, scenic_code text, name text NOT NULL,
 aliases jsonb NOT NULL, PRIMARY KEY(dataset_id,scenic_code)
);
CREATE TABLE p6_kg.poi (
 dataset_id text, poi_id text, scenic_code text NOT NULL, name text NOT NULL,
 role text, subtype text, PRIMARY KEY(dataset_id,poi_id),
 FOREIGN KEY(dataset_id,poi_id) REFERENCES p6_kg.node
);
CREATE INDEX p6_poi_scenic ON p6_kg.poi(dataset_id,scenic_code,name);
CREATE TABLE p6_kg.route (
 dataset_id text, route_id text, scenic_code text NOT NULL, route_class text NOT NULL,
 itinerary_scope text NOT NULL, summary_text text, step_count int NOT NULL,
 mapped_stage_count int NOT NULL, hierarchy jsonb NOT NULL, PRIMARY KEY(dataset_id,route_id),
 FOREIGN KEY(dataset_id,route_id) REFERENCES p6_kg.node
);
CREATE INDEX p6_route_scenic ON p6_kg.route(dataset_id,scenic_code);
CREATE TABLE p6_kg.route_step (
 dataset_id text, step_id text, route_id text NOT NULL, seq int NOT NULL CHECK(seq>0),
 source_day_no int, period text, order_group text, time_meta jsonb NOT NULL,
 PRIMARY KEY(dataset_id,step_id), UNIQUE(dataset_id,route_id,seq), UNIQUE(dataset_id,route_id,step_id),
 FOREIGN KEY(dataset_id,route_id) REFERENCES p6_kg.route,
 FOREIGN KEY(dataset_id,step_id) REFERENCES p6_kg.node
);
CREATE TABLE p6_kg.step_poi (
 dataset_id text, step_id text, poi_id text,
 PRIMARY KEY(dataset_id,step_id,poi_id),
 FOREIGN KEY(dataset_id,step_id) REFERENCES p6_kg.route_step,
 FOREIGN KEY(dataset_id,poi_id) REFERENCES p6_kg.poi
);
CREATE TABLE p6_kg.transport_leg (
 dataset_id text, leg_id text, scenic_code text NOT NULL, from_poi_id text NOT NULL,
 to_poi_id text NOT NULL, mode_id text, service_name text, PRIMARY KEY(dataset_id,leg_id),
 FOREIGN KEY(dataset_id,leg_id) REFERENCES p6_kg.node,
 FOREIGN KEY(dataset_id,from_poi_id) REFERENCES p6_kg.poi,
 FOREIGN KEY(dataset_id,to_poi_id) REFERENCES p6_kg.poi,
 FOREIGN KEY(dataset_id,mode_id) REFERENCES p6_kg.node
);
CREATE INDEX p6_leg_direction ON p6_kg.transport_leg(dataset_id,from_poi_id,to_poi_id);
CREATE TABLE p6_kg.advice_theme (
 dataset_id text, theme_id text, target_id text NOT NULL, scenic_code text NOT NULL,
 advice_summary_id text NOT NULL, title text NOT NULL, summary text NOT NULL,
 source_item_ids jsonb NOT NULL, source_record_ids jsonb NOT NULL,
 current_validity_verified boolean NOT NULL, PRIMARY KEY(dataset_id,theme_id),
 FOREIGN KEY(dataset_id,target_id) REFERENCES p6_kg.node,
 FOREIGN KEY(dataset_id,advice_summary_id) REFERENCES p6_kg.node
);
CREATE INDEX p6_themes_target ON p6_kg.advice_theme(dataset_id,target_id,scenic_code);
CREATE TABLE p7_app.conversation (
 id uuid PRIMARY KEY, owner_key text NOT NULL, title text NOT NULL,
 data_version text NOT NULL REFERENCES p6_kg.dataset,
 state jsonb NOT NULL DEFAULT '{}', context_summary jsonb NOT NULL DEFAULT '{}',
 summary_through_seq bigint, pending_question_message_id uuid, active_itinerary_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,data_version)
);
CREATE INDEX p7_conversation_owner ON p7_app.conversation(owner_key,updated_at DESC,id);
CREATE TABLE p7_app.turn (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES p7_app.conversation,
 client_message_id uuid NOT NULL, request_hash text NOT NULL, input_message_id uuid NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','completed','awaiting_user','partial','cancelled','failed')),
 decision_count int NOT NULL DEFAULT 0 CHECK(decision_count BETWEEN 0 AND 10),
 tool_call_count int NOT NULL DEFAULT 0 CHECK(tool_call_count BETWEEN 0 AND 30),
 prompt_tokens int, cancel_requested_at timestamptz, error jsonb, admitted_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, ended_at timestamptz,
 UNIQUE(conversation_id,id), UNIQUE(conversation_id,client_message_id)
);
CREATE UNIQUE INDEX p7_one_turn ON p7_app.turn(conversation_id) WHERE status IN ('queued','running');
CREATE TABLE p7_app.message (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL, turn_id uuid NOT NULL,
 seq bigint NOT NULL, role text NOT NULL CHECK(role IN ('user','assistant','tool')),
 content text, tool_calls jsonb, tool_call_id text, parent_assistant_message_id uuid,
 attachments jsonb NOT NULL DEFAULT '[]', is_public boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(conversation_id,seq),
 UNIQUE(conversation_id,id), UNIQUE(turn_id,id),
 UNIQUE(turn_id,parent_assistant_message_id,tool_call_id),
 FOREIGN KEY(conversation_id,turn_id) REFERENCES p7_app.turn(conversation_id,id),
 FOREIGN KEY(turn_id,parent_assistant_message_id) REFERENCES p7_app.message(turn_id,id)
);
ALTER TABLE p7_app.turn ADD FOREIGN KEY(id,input_message_id) REFERENCES p7_app.message(turn_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE p7_app.conversation ADD FOREIGN KEY(id,pending_question_message_id) REFERENCES p7_app.message(conversation_id,id);
CREATE TABLE p7_app.turn_event (
 turn_id uuid REFERENCES p7_app.turn, seq bigint, event_type text NOT NULL, payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(turn_id,seq)
);
CREATE TABLE p7_app.itinerary (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL, data_version text NOT NULL, current_version int NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(conversation_id,data_version) REFERENCES p7_app.conversation(id,data_version),
 UNIQUE(id,data_version), UNIQUE(conversation_id,id)
);
ALTER TABLE p7_app.conversation ADD FOREIGN KEY(id,active_itinerary_id) REFERENCES p7_app.itinerary(conversation_id,id);
CREATE TABLE p7_app.itinerary_version (
 itinerary_id uuid, version int CHECK(version>0), data_version text NOT NULL, parent_version int,
 created_by_turn_id uuid REFERENCES p7_app.turn, title text NOT NULL,
 status text NOT NULL CHECK(status IN ('ready','partial')), assumptions jsonb NOT NULL,
 warnings jsonb NOT NULL, unresolved_items jsonb NOT NULL, change_summary jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(itinerary_id,version),
 UNIQUE(itinerary_id,version,data_version),
 FOREIGN KEY(itinerary_id,data_version) REFERENCES p7_app.itinerary(id,data_version),
 FOREIGN KEY(itinerary_id,parent_version) REFERENCES p7_app.itinerary_version(itinerary_id,version)
);
ALTER TABLE p7_app.itinerary ADD FOREIGN KEY(id,current_version) REFERENCES p7_app.itinerary_version(itinerary_id,version) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE p7_app.itinerary_day (
 itinerary_id uuid, version int, day_id uuid, day_no int NOT NULL CHECK(day_no>0),
 date date, label text, schedule_basis text NOT NULL, PRIMARY KEY(itinerary_id,version,day_id),
 UNIQUE(itinerary_id,version,day_no), FOREIGN KEY(itinerary_id,version) REFERENCES p7_app.itinerary_version
);
CREATE TABLE p7_app.itinerary_item (
 itinerary_id uuid, version int, item_id uuid, day_id uuid NOT NULL, position int NOT NULL CHECK(position>0),
 type text NOT NULL CHECK(type IN ('poi_visit','transport_leg','note')), data_version text NOT NULL,
 poi_id text, leg_id text, route_id text, step_id text, recommendation_reason text,
 fields jsonb NOT NULL, evidence_refs jsonb NOT NULL, PRIMARY KEY(itinerary_id,version,item_id),
 UNIQUE(itinerary_id,version,day_id,position),
 CHECK((type = 'poi_visit') = (poi_id IS NOT NULL)), CHECK((type = 'transport_leg') = (leg_id IS NOT NULL)),
 CHECK(step_id IS NULL OR route_id IS NOT NULL),
 FOREIGN KEY(itinerary_id,version,day_id) REFERENCES p7_app.itinerary_day,
 FOREIGN KEY(itinerary_id,version,data_version) REFERENCES p7_app.itinerary_version(itinerary_id,version,data_version),
 FOREIGN KEY(data_version,poi_id) REFERENCES p6_kg.poi,
 FOREIGN KEY(data_version,leg_id) REFERENCES p6_kg.transport_leg,
 FOREIGN KEY(data_version,route_id) REFERENCES p6_kg.route,
 FOREIGN KEY(data_version,route_id,step_id) REFERENCES p6_kg.route_step(dataset_id,route_id,step_id)
);
CREATE INDEX p7_item_locator ON p7_app.itinerary_item(itinerary_id,version,route_id,poi_id);
CREATE TABLE p7_app.itinerary_write_receipt (
 conversation_id uuid, operation_key text, request_hash text NOT NULL,
 itinerary_id uuid NOT NULL, result_version int NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(conversation_id,operation_key),
 FOREIGN KEY(conversation_id,itinerary_id) REFERENCES p7_app.itinerary(conversation_id,id),
 FOREIGN KEY(itinerary_id,result_version) REFERENCES p7_app.itinerary_version(itinerary_id,version)
);
