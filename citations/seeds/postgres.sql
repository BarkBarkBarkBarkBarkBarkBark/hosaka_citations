INSERT INTO public.hc_violation_types
SELECT * FROM jsonb_populate_recordset(NULL::public.hc_violation_types, :'defaults'::jsonb)
ON CONFLICT DO NOTHING;
