CREATE TABLE persons (
 id TEXT PRIMARY KEY, display_name TEXT NOT NULL CHECK(length(trim(display_name))>0),
 email TEXT, phone TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE violation_types (
 id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, title TEXT NOT NULL, description TEXT,
 default_remedy TEXT, default_resolution_seconds INTEGER CHECK(default_resolution_seconds>0),
 severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 5), active INTEGER NOT NULL CHECK(active IN (0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE citations (
 id TEXT PRIMARY KEY, citation_number TEXT NOT NULL UNIQUE,
 person_id TEXT NOT NULL REFERENCES persons(id), violation_type_id TEXT NOT NULL REFERENCES violation_types(id),
 issued_at TEXT NOT NULL, issued_by TEXT, description TEXT, first_offense INTEGER NOT NULL CHECK(first_offense IN (0,1)),
 status TEXT NOT NULL CHECK(status IN ('open','resolved','void')),
 resolution_duration_seconds INTEGER CHECK(resolution_duration_seconds>0), due_at TEXT,
 resolved_at TEXT, resolved_by TEXT, resolution_notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=0), deleted_at TEXT,
 CHECK((status='resolved')=(resolved_at IS NOT NULL)),
 CHECK(resolved_at IS NULL OR resolved_at>=issued_at), CHECK(due_at IS NULL OR due_at>=issued_at),
 CHECK(deleted_at IS NULL OR status<>'open')
);
CREATE TABLE remedies (
 id TEXT PRIMARY KEY, citation_id TEXT NOT NULL REFERENCES citations(id), description TEXT NOT NULL,
 required INTEGER NOT NULL CHECK(required IN (0,1)), completed INTEGER NOT NULL CHECK(completed IN (0,1)),
 completed_at TEXT, completed_by TEXT, completion_notes TEXT, position INTEGER NOT NULL CHECK(position>=0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 CHECK(completed=(completed_at IS NOT NULL))
);
CREATE TABLE citation_events (
 id TEXT PRIMARY KEY, citation_id TEXT NOT NULL REFERENCES citations(id), actor_id TEXT,
 event_type TEXT NOT NULL, event_payload TEXT NOT NULL CHECK(json_valid(event_payload) AND json_type(event_payload)='object'), created_at TEXT NOT NULL
);
CREATE TABLE attachments (
 id TEXT PRIMARY KEY, citation_id TEXT NOT NULL REFERENCES citations(id), uploaded_by TEXT,
 original_filename TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), storage_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE row_versions (
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(entity_type,entity_id)
);
CREATE TABLE replica_state (
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(entity_type,entity_id)
);
CREATE TABLE sync_outbox (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE,
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
 base_revision INTEGER NOT NULL CHECK(base_revision>=0), created_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_error TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','delivered','conflict','superseded'))
);
CREATE INDEX outbox_pending ON sync_outbox(status,sequence);
CREATE TABLE sync_checkpoints (scope TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sync_conflicts (
 id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 local_payload TEXT NOT NULL, remote_payload TEXT, remote_revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, resolved_at TEXT, resolution TEXT, resolution_payload TEXT,
 UNIQUE(entity_type,entity_id,remote_revision)
);
CREATE TABLE file_transfers (
 attachment_id TEXT PRIMARY KEY REFERENCES attachments(id), direction TEXT NOT NULL CHECK(direction IN ('upload','download')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_error TEXT
);
CREATE INDEX overdue_open ON citations(status,due_at);
