-- Objects belong to the isolated application owner (see ../postgres_bootstrap.sql).
SET ROLE hosaka_citations_owner;
CREATE TABLE hosaka_citations.hc_workspaces(id uuid PRIMARY KEY, name text NOT NULL);
CREATE TABLE hosaka_citations.hc_members(workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), user_id uuid NOT NULL, PRIMARY KEY(workspace_id,user_id));
CREATE TABLE hosaka_citations.hc_persons (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL,
 display_name text NOT NULL CHECK(length(trim(display_name))>0), email text, phone text,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, deleted_at timestamptz,
 sync_revision bigint NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,id)
);
CREATE TABLE hosaka_citations.hc_violation_types (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL, code text NOT NULL, title text NOT NULL,
 description text, default_remedy text, default_resolution_seconds integer CHECK(default_resolution_seconds>0),
 severity integer NOT NULL CHECK(severity BETWEEN 1 AND 5), active boolean NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, deleted_at timestamptz,
 sync_revision bigint NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,code)
);
CREATE TABLE hosaka_citations.hc_citations (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL, citation_number text NOT NULL,
 person_id uuid NOT NULL, violation_type_id uuid NOT NULL, issued_at timestamptz NOT NULL, issued_by text,
 description text, first_offense boolean NOT NULL, status text NOT NULL CHECK(status IN ('open','resolved','void')),
 resolution_duration_seconds integer CHECK(resolution_duration_seconds>0), due_at timestamptz,
 resolved_at timestamptz, resolved_by text, resolution_notes text,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, revision integer NOT NULL CHECK(revision>=0), deleted_at timestamptz,
 sync_revision bigint NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,citation_number),
 FOREIGN KEY(workspace_id,person_id) REFERENCES hosaka_citations.hc_persons(workspace_id,id),
 FOREIGN KEY(workspace_id,violation_type_id) REFERENCES hosaka_citations.hc_violation_types(workspace_id,id),
 CHECK((status='resolved')=(resolved_at IS NOT NULL)), CHECK(deleted_at IS NULL OR status<>'open'),
 CHECK(resolved_at IS NULL OR resolved_at>=issued_at), CHECK(due_at IS NULL OR due_at>=issued_at)
);
CREATE TABLE hosaka_citations.hc_remedies (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL, citation_id uuid NOT NULL,
 description text NOT NULL, required boolean NOT NULL, completed boolean NOT NULL,
 completed_at timestamptz, completed_by text, completion_notes text, position integer NOT NULL CHECK(position>=0),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, deleted_at timestamptz,
 sync_revision bigint NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,id),
 FOREIGN KEY(workspace_id,citation_id) REFERENCES hosaka_citations.hc_citations(workspace_id,id),
 CHECK(completed=(completed_at IS NOT NULL))
);
CREATE TABLE hosaka_citations.hc_citation_events (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL, citation_id uuid NOT NULL,
 actor_id text, event_type text NOT NULL, event_payload jsonb NOT NULL CHECK(jsonb_typeof(event_payload)='object'), created_at timestamptz NOT NULL,
 sync_revision bigint NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,id),
 FOREIGN KEY(workspace_id,citation_id) REFERENCES hosaka_citations.hc_citations(workspace_id,id)
);
CREATE TABLE hosaka_citations.hc_attachments (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), id uuid NOT NULL, citation_id uuid NOT NULL,
 uploaded_by text, original_filename text NOT NULL,
 mime_type text NOT NULL CHECK(mime_type IN ('image/png','image/jpeg','image/webp','application/pdf')),
 size_bytes bigint NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 storage_key text NOT NULL CHECK(storage_key ~ '^[a-f0-9]{32}\.[a-f0-9]{64}\.(png|jpg|webp|pdf)$'),
 created_at timestamptz NOT NULL, deleted_at timestamptz, sync_revision bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,storage_key),
 FOREIGN KEY(workspace_id,citation_id) REFERENCES hosaka_citations.hc_citations(workspace_id,id)
);
CREATE TABLE hosaka_citations.hc_clock(workspace_id uuid PRIMARY KEY REFERENCES hosaka_citations.hc_workspaces(id), value bigint NOT NULL DEFAULT 0);
CREATE TABLE hosaka_citations.hc_changes (
 workspace_id uuid NOT NULL, cursor bigint NOT NULL, entity_type text NOT NULL, entity_id uuid NOT NULL,
 revision bigint NOT NULL, payload jsonb NOT NULL, PRIMARY KEY(workspace_id,cursor)
);
CREATE TABLE hosaka_citations.hc_operations (
 workspace_id uuid NOT NULL REFERENCES hosaka_citations.hc_workspaces(id), operation_id uuid NOT NULL,
 request jsonb NOT NULL, response jsonb NOT NULL, PRIMARY KEY(workspace_id,operation_id)
);

CREATE FUNCTION hosaka_citations.hc_is_member(p_workspace uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,hosaka_citations
AS $$ SELECT hosaka_citations.hc_uid() IS NOT NULL AND EXISTS(
 SELECT 1 FROM hosaka_citations.hc_members WHERE workspace_id=p_workspace AND user_id=hosaka_citations.hc_uid()
) $$;
REVOKE ALL ON FUNCTION hosaka_citations.hc_is_member(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hosaka_citations.hc_is_member(uuid) TO authenticated;

ALTER TABLE hosaka_citations.hc_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE hosaka_citations.hc_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY hc_workspace_read ON hosaka_citations.hc_workspaces FOR SELECT TO authenticated USING(hosaka_citations.hc_is_member(id));
CREATE POLICY hc_members_read ON hosaka_citations.hc_members FOR SELECT TO authenticated USING(user_id=hosaka_citations.hc_uid());
GRANT SELECT ON hosaka_citations.hc_workspaces, hosaka_citations.hc_members TO authenticated;
DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['persons','violation_types','citations','remedies','citation_events','attachments','clock','changes','operations'] LOOP
  EXECUTE format('ALTER TABLE hosaka_citations.hc_%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY hc_read ON hosaka_citations.hc_%I FOR SELECT TO authenticated USING(hosaka_citations.hc_is_member(workspace_id))',t);
  EXECUTE format('REVOKE ALL ON hosaka_citations.hc_%I FROM anon,authenticated',t);
  EXECUTE format('GRANT SELECT ON hosaka_citations.hc_%I TO authenticated',t);
 END LOOP;
END $$;
RESET ROLE;
