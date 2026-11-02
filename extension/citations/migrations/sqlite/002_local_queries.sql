CREATE INDEX remedies_citation ON remedies(citation_id, position);
CREATE INDEX events_citation ON citation_events(citation_id, created_at);
CREATE INDEX attachments_citation ON attachments(citation_id);
CREATE INDEX citations_person_type ON citations(person_id, violation_type_id, issued_at);
CREATE INDEX citations_list ON citations(deleted_at, issued_at DESC, id DESC);
