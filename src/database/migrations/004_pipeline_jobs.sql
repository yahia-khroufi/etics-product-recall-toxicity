BEGIN;
CREATE TABLE IF NOT EXISTS pipeline_jobs (
 id SERIAL PRIMARY KEY, case_id VARCHAR(100) NOT NULL UNIQUE,
 source_path TEXT NOT NULL, input_hash VARCHAR(64) NOT NULL,
 status VARCHAR(40) NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 result_id INTEGER REFERENCES llm_results(id), error_type VARCHAR(100),
 next_attempt_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_status ON pipeline_jobs(status);
CREATE TABLE IF NOT EXISTS pipeline_workers (
 name VARCHAR(40) PRIMARY KEY, pid INTEGER NOT NULL, status VARCHAR(40) NOT NULL,
 current_case_id VARCHAR(100), started_at TIMESTAMPTZ NOT NULL, heartbeat_at TIMESTAMPTZ NOT NULL,
 next_attempt_at TIMESTAMPTZ
);
COMMIT;
