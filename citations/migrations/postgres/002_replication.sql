-- Objects belong to the isolated application owner (see ../postgres_bootstrap.sql).
SET ROLE hosaka_citations_owner;
-- Serialize writers before row locks. A sequence alone is not a commit-order cursor.
CREATE FUNCTION hosaka_citations.hc_writer_lock() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,hosaka_citations AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('hosaka_citations.halloween-citations.replica',0));
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Use tombstones, not physical deletion'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION hosaka_citations.hc_record_revision() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,hosaka_citations AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.workspace_id<>OLD.workspace_id OR NEW.id<>OLD.id THEN RAISE EXCEPTION 'Record identity is immutable'; END IF;
  IF TG_TABLE_NAME='hc_citation_events' THEN RAISE EXCEPTION 'Citation history is append-only'; END IF;
  NEW.sync_revision=OLD.sync_revision+1;
 ELSE NEW.sync_revision=1;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION hosaka_citations.hc_record_change() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,hosaka_citations SET timezone='UTC' AS $$
DECLARE position bigint;
BEGIN
 INSERT INTO hosaka_citations.hc_clock VALUES(NEW.workspace_id,1)
 ON CONFLICT(workspace_id) DO UPDATE SET value=hc_clock.value+1 RETURNING value INTO position;
 INSERT INTO hosaka_citations.hc_changes VALUES(NEW.workspace_id,position,substr(TG_TABLE_NAME,4),NEW.id,NEW.sync_revision,to_jsonb(NEW)-'workspace_id'-'sync_revision');
 IF TG_TABLE_NAME='hc_citations' AND TG_OP='UPDATE' THEN
  INSERT INTO hosaka_citations.hc_citation_events(workspace_id,id,citation_id,actor_id,event_type,event_payload,created_at)
  VALUES(NEW.workspace_id,gen_random_uuid(),NEW.id,hosaka_citations.hc_uid()::text,'remote_revision',
   jsonb_build_object('before',to_jsonb(OLD)-'workspace_id'-'sync_revision','after',to_jsonb(NEW)-'workspace_id'-'sync_revision'),clock_timestamp());
 END IF;
 RETURN NEW;
END $$;

DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['persons','violation_types','citations','remedies','citation_events','attachments'] LOOP
  EXECUTE format('CREATE TRIGGER hc_lock BEFORE INSERT OR UPDATE OR DELETE ON hosaka_citations.hc_%I FOR EACH STATEMENT EXECUTE FUNCTION hosaka_citations.hc_writer_lock()',t);
  EXECUTE format('CREATE TRIGGER hc_revision BEFORE INSERT OR UPDATE ON hosaka_citations.hc_%I FOR EACH ROW EXECUTE FUNCTION hosaka_citations.hc_record_revision()',t);
  EXECUTE format('CREATE TRIGGER hc_change AFTER INSERT OR UPDATE ON hosaka_citations.hc_%I FOR EACH ROW EXECUTE FUNCTION hosaka_citations.hc_record_change()',t);
 END LOOP;
END $$;

CREATE FUNCTION hosaka_citations.hc_push(
 p_workspace uuid, p_operation uuid, p_entity_type text, p_entity_id uuid, p_base_revision bigint, p_payload jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,hosaka_citations SET timezone='UTC' AS $$
DECLARE
 tab text; prior jsonb; saved jsonb; current_record jsonb; candidate jsonb; result jsonb; wanted jsonb;
 current_revision bigint; columns text; assignments text; allowed text[];
BEGIN
 IF NOT hosaka_citations.hc_is_member(p_workspace) THEN RAISE EXCEPTION 'Workspace access denied' USING ERRCODE='42501'; END IF;
 IF p_entity_type<>ALL(ARRAY['persons','violation_types','citations','remedies','citation_events','attachments']) THEN
  RAISE EXCEPTION 'Unsupported entity';
 END IF;
 IF jsonb_typeof(p_payload)<>'object' OR (p_payload->>'id')::uuid<>p_entity_id OR p_base_revision<0 THEN
  RAISE EXCEPTION 'Invalid operation';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('hosaka_citations.halloween-citations.replica',0));
 wanted=jsonb_build_object('type',p_entity_type,'id',p_entity_id,'base',p_base_revision,'payload',p_payload);
 SELECT request,response INTO prior,saved FROM hosaka_citations.hc_operations WHERE workspace_id=p_workspace AND operation_id=p_operation;
 IF FOUND THEN
  IF prior<>wanted THEN RAISE EXCEPTION 'Operation identity reused with different content'; END IF;
  RETURN saved;
 END IF;
 tab='hc_'||p_entity_type;
 SELECT array_agg(attname::text ORDER BY attnum),
  string_agg(format('%I',attname),',' ORDER BY attnum),
  string_agg(format('%I=excluded.%I',attname,attname),',' ORDER BY attnum) FILTER(WHERE attname NOT IN ('workspace_id','id','sync_revision'))
 INTO allowed,columns,assignments FROM pg_attribute
 WHERE attrelid=format('hosaka_citations.%I',tab)::regclass AND attnum>0 AND NOT attisdropped;
 IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_payload) k WHERE NOT(k=ANY(allowed)) OR k IN ('workspace_id','sync_revision')) THEN
  RAISE EXCEPTION 'Unexpected record fields';
 END IF;
 EXECUTE format('SELECT to_jsonb(t) FROM hosaka_citations.%I t WHERE workspace_id=$1 AND id=$2 FOR UPDATE',tab)
 INTO current_record USING p_workspace,p_entity_id;
 current_revision=coalesce((current_record->>'sync_revision')::bigint,0);
 EXECUTE format('SELECT to_jsonb(jsonb_populate_record(NULL::hosaka_citations.%I,$1))',tab)
 INTO candidate USING p_payload||jsonb_build_object('workspace_id',p_workspace,'sync_revision',0);
 IF current_record IS NOT NULL AND candidate-'workspace_id'-'sync_revision'=current_record-'workspace_id'-'sync_revision' THEN
  result=jsonb_build_object('status','ok','revision',current_revision);
 ELSIF current_revision<>p_base_revision THEN
  result=jsonb_build_object('status','conflict','revision',current_revision,'payload',current_record-'workspace_id'-'sync_revision');
 ELSE
  EXECUTE format(
   'INSERT INTO hosaka_citations.%I(%s) SELECT %s FROM jsonb_populate_record(NULL::hosaka_citations.%I,$1) ON CONFLICT(workspace_id,id) DO UPDATE SET %s RETURNING sync_revision',
   tab,columns,columns,tab,assignments
  ) INTO current_revision USING candidate;
  result=jsonb_build_object('status','ok','revision',current_revision);
 END IF;
 INSERT INTO hosaka_citations.hc_operations VALUES(p_workspace,p_operation,wanted,result);
 RETURN result;
END $$;

CREATE FUNCTION hosaka_citations.hc_pull(p_workspace uuid,p_after bigint,p_limit integer DEFAULT 100)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,hosaka_citations AS $$
DECLARE rows jsonb; position bigint; more boolean;
BEGIN
 IF NOT hosaka_citations.hc_is_member(p_workspace) THEN RAISE EXCEPTION 'Workspace access denied' USING ERRCODE='42501'; END IF;
 IF p_after<0 OR p_limit<1 OR p_limit>500 THEN RAISE EXCEPTION 'Invalid cursor page'; END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('cursor',cursor,'entity_type',entity_type,'entity_id',entity_id,'revision',revision,'payload',payload) ORDER BY cursor),'[]'::jsonb),
  coalesce(max(cursor),p_after) INTO rows,position
 FROM (SELECT * FROM hosaka_citations.hc_changes WHERE workspace_id=p_workspace AND cursor>p_after ORDER BY cursor LIMIT p_limit) batch;
 SELECT EXISTS(SELECT 1 FROM hosaka_citations.hc_changes WHERE workspace_id=p_workspace AND cursor>position) INTO more;
 RETURN jsonb_build_object('changes',rows,'cursor',position,'has_more',more);
END $$;
REVOKE ALL ON FUNCTION hosaka_citations.hc_push(uuid,uuid,text,uuid,bigint,jsonb) FROM PUBLIC,anon;
REVOKE ALL ON FUNCTION hosaka_citations.hc_pull(uuid,bigint,integer) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION hosaka_citations.hc_push(uuid,uuid,text,uuid,bigint,jsonb),hosaka_citations.hc_pull(uuid,bigint,integer) TO authenticated;
REVOKE ALL ON FUNCTION hosaka_citations.hc_writer_lock(),hosaka_citations.hc_record_revision(),hosaka_citations.hc_record_change() FROM PUBLIC,anon,authenticated;
RESET ROLE;
