-- Idempotent isolation bootstrap; run before numbered migrations by the deploying role.
-- The application lives in its own schema, owned by a dedicated NOLOGIN role, so its
-- SECURITY DEFINER functions cannot reach other applications sharing this project.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='hosaka_citations_owner') THEN
  CREATE ROLE hosaka_citations_owner NOLOGIN;
 END IF;
END $$;
GRANT hosaka_citations_owner TO CURRENT_USER;
CREATE SCHEMA IF NOT EXISTS hosaka_citations AUTHORIZATION hosaka_citations_owner;
REVOKE ALL ON SCHEMA hosaka_citations FROM PUBLIC;
GRANT USAGE ON SCHEMA hosaka_citations TO authenticated;
-- The owner role has no access to Supabase's auth schema. This one helper, owned by the
-- deploying role, is the application's only path to the caller's Auth user id.
CREATE OR REPLACE FUNCTION hosaka_citations.hc_uid() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT auth.uid() $$;
REVOKE ALL ON FUNCTION hosaka_citations.hc_uid() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hosaka_citations.hc_uid() TO hosaka_citations_owner, authenticated;
SET ROLE hosaka_citations_owner;
CREATE TABLE IF NOT EXISTS hosaka_citations.hc_schema_migrations(version integer PRIMARY KEY);
RESET ROLE;
