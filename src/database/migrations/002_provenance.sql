BEGIN;
ALTER TABLE recalls ADD COLUMN IF NOT EXISTS organization_key VARCHAR(200);
CREATE TABLE IF NOT EXISTS recall_snapshots (
 id SERIAL PRIMARY KEY, recall_id INTEGER NOT NULL REFERENCES recalls(id),
 payload JSONB NOT NULL, content_hash VARCHAR(64) NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ix_recall_snapshots_recall_id ON recall_snapshots(recall_id);
CREATE TABLE IF NOT EXISTS documents (
 id SERIAL PRIMARY KEY, recall_id INTEGER NOT NULL REFERENCES recalls(id),
 role VARCHAR(40) NOT NULL, UNIQUE(recall_id,role)
);
CREATE TABLE IF NOT EXISTS document_versions (
 id SERIAL PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
 version_number INTEGER NOT NULL, fingerprint VARCHAR(64) NOT NULL,
 text_sha256 VARCHAR(64) NOT NULL, raw_sha256 VARCHAR(64), source_url TEXT,
 source_path TEXT, provenance JSONB NOT NULL, raw_text TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(document_id,version_number)
);
CREATE TABLE IF NOT EXISTS analysis_runs (
 id SERIAL PRIMARY KEY, recall_snapshot_id INTEGER NOT NULL REFERENCES recall_snapshots(id),
 model_name VARCHAR(200) NOT NULL, model_revision VARCHAR(200),
 prompt_version VARCHAR(100) NOT NULL, prompt_hash VARCHAR(64) NOT NULL,
 scoring_version VARCHAR(100) NOT NULL, parameters JSONB NOT NULL,
 git_commit VARCHAR(40), git_dirty BOOLEAN, status VARCHAR(40) NOT NULL,
 error_type VARCHAR(100), started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ
);
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS run_id INTEGER UNIQUE REFERENCES analysis_runs(id);
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS official_version_id INTEGER REFERENCES document_versions(id);
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS company_version_id INTEGER REFERENCES document_versions(id);
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS scoring_version VARCHAR(100);
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(40) NOT NULL DEFAULT 'legacy_unlinked';
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ;
CREATE OR REPLACE FUNCTION etics_immutable_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ETICS snapshots are append-only'; END $$;
DROP TRIGGER IF EXISTS immutable_document_version ON document_versions;
CREATE TRIGGER immutable_document_version BEFORE UPDATE OR DELETE ON document_versions
 FOR EACH ROW EXECUTE FUNCTION etics_immutable_snapshot();
DROP TRIGGER IF EXISTS immutable_recall_snapshot ON recall_snapshots;
CREATE TRIGGER immutable_recall_snapshot BEFORE UPDATE OR DELETE ON recall_snapshots
 FOR EACH ROW EXECUTE FUNCTION etics_immutable_snapshot();
COMMIT;
