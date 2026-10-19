INSERT INTO storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
VALUES('halloween-citations-evidence','halloween-citations-evidence',false,10485760,
 ARRAY['image/png','image/jpeg','image/webp','application/pdf'])
ON CONFLICT(id) DO NOTHING;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM storage.buckets WHERE id='halloween-citations-evidence' AND public) THEN
  RAISE EXCEPTION 'The evidence bucket must be private; review its configuration before proceeding';
 END IF;
END $$;
CREATE FUNCTION public.hc_storage_workspace(path text) RETURNS uuid
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN split_part(path,'/',1) ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
 THEN split_part(path,'/',1)::uuid ELSE NULL END
$$;
CREATE POLICY hc_evidence_read ON storage.objects FOR SELECT TO authenticated
USING(bucket_id='halloween-citations-evidence' AND public.hc_is_member(public.hc_storage_workspace(name)));
CREATE POLICY hc_evidence_insert ON storage.objects FOR INSERT TO authenticated
WITH CHECK(bucket_id='halloween-citations-evidence' AND public.hc_is_member(public.hc_storage_workspace(name)));
