INSERT INTO hosaka_citations.hc_violation_types
SELECT * FROM jsonb_populate_recordset(NULL::hosaka_citations.hc_violation_types, :'defaults'::jsonb)
ON CONFLICT DO NOTHING;
