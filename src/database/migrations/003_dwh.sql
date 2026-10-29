BEGIN;
CREATE SCHEMA IF NOT EXISTS dwh;
CREATE TABLE IF NOT EXISTS dwh.dim_company (
 company_sk BIGSERIAL PRIMARY KEY, company_key TEXT NOT NULL, attributes_hash TEXT NOT NULL,
 company_name TEXT, organization_key TEXT, valid_from TIMESTAMPTZ NOT NULL,
 valid_to TIMESTAMPTZ, is_current BOOLEAN NOT NULL,
 CHECK ((is_current AND valid_to IS NULL) OR (NOT is_current AND valid_to > valid_from)),
 UNIQUE(company_key,valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_current ON dwh.dim_company(company_key) WHERE is_current;
CREATE TABLE IF NOT EXISTS dwh.dim_product (
 product_sk BIGSERIAL PRIMARY KEY, product_key TEXT NOT NULL, attributes_hash TEXT NOT NULL,
 product_name TEXT, brand TEXT, gtin JSONB NOT NULL, valid_from TIMESTAMPTZ NOT NULL,
 valid_to TIMESTAMPTZ, is_current BOOLEAN NOT NULL,
 CHECK ((is_current AND valid_to IS NULL) OR (NOT is_current AND valid_to > valid_from)),
 UNIQUE(product_key,valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_current ON dwh.dim_product(product_key) WHERE is_current;
CREATE TABLE IF NOT EXISTS dwh.snapshot_dimensions (
 source_snapshot_id INTEGER PRIMARY KEY REFERENCES recall_snapshots(id),
 company_sk BIGINT NOT NULL REFERENCES dwh.dim_company(company_sk),
 product_sk BIGINT NOT NULL REFERENCES dwh.dim_product(product_sk)
);
CREATE TABLE IF NOT EXISTS dwh.dim_date (
 date_sk INTEGER PRIMARY KEY, full_date DATE NOT NULL UNIQUE,
 year INTEGER NOT NULL, quarter INTEGER NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dwh.dim_model (
 model_sk BIGSERIAL PRIMARY KEY, model_key TEXT NOT NULL UNIQUE,
 model_name TEXT NOT NULL, provider_reported_revision TEXT
);
CREATE TABLE IF NOT EXISTS dwh.dim_prompt (
 prompt_sk BIGSERIAL PRIMARY KEY, prompt_key TEXT NOT NULL UNIQUE,
 prompt_version TEXT NOT NULL, prompt_hash TEXT
);
CREATE TABLE IF NOT EXISTS dwh.dim_scoring (
 scoring_sk BIGSERIAL PRIMARY KEY, scoring_version TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dwh.dim_execution (
 execution_sk BIGSERIAL PRIMARY KEY, source_run_id INTEGER NOT NULL UNIQUE REFERENCES analysis_runs(id),
 git_commit TEXT, git_dirty BOOLEAN, parameters JSONB NOT NULL,
 started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dwh.dim_document_version (
 document_version_sk BIGSERIAL PRIMARY KEY, source_version_id INTEGER NOT NULL UNIQUE REFERENCES document_versions(id),
 source_document_id INTEGER NOT NULL, role TEXT NOT NULL, version_number INTEGER NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, source_url TEXT, source_path TEXT,
 raw_sha256 TEXT, text_sha256 TEXT NOT NULL, provenance JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS dwh.etl_runs (
 id BIGSERIAL PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ, status TEXT NOT NULL, inserted INTEGER NOT NULL DEFAULT 0,
 updated INTEGER NOT NULL DEFAULT 0, source_count INTEGER, error_type TEXT
);
CREATE TABLE IF NOT EXISTS dwh.fact_analysis (
 analysis_sk BIGSERIAL PRIMARY KEY, source_result_id INTEGER NOT NULL UNIQUE,
 source_recall_id INTEGER NOT NULL, case_id TEXT NOT NULL,
 company_sk BIGINT NOT NULL REFERENCES dwh.dim_company(company_sk),
 product_sk BIGINT NOT NULL REFERENCES dwh.dim_product(product_sk),
 analysis_date_sk INTEGER NOT NULL REFERENCES dwh.dim_date(date_sk),
 publication_date_sk INTEGER REFERENCES dwh.dim_date(date_sk),
 model_sk BIGINT NOT NULL REFERENCES dwh.dim_model(model_sk),
 prompt_sk BIGINT NOT NULL REFERENCES dwh.dim_prompt(prompt_sk),
 scoring_sk BIGINT NOT NULL REFERENCES dwh.dim_scoring(scoring_sk),
 execution_sk BIGINT REFERENCES dwh.dim_execution(execution_sk),
 official_version_sk BIGINT REFERENCES dwh.dim_document_version(document_version_sk),
 company_version_sk BIGINT REFERENCES dwh.dim_document_version(document_version_sk),
 analysis_created_at TIMESTAMPTZ NOT NULL, scored_at TIMESTAMPTZ,
 context_origin TEXT NOT NULL, analysis_status TEXT NOT NULL, evidence_verified BOOLEAN NOT NULL,
 q1 BOOLEAN NOT NULL, q2 BOOLEAN NOT NULL, q3 BOOLEAN NOT NULL, q4 BOOLEAN NOT NULL,
 q5 BOOLEAN NOT NULL, q6 BOOLEAN NOT NULL, q7 BOOLEAN NOT NULL,
 evidence JSONB NOT NULL, completeness_score DOUBLE PRECISION,
 toxicity_score DOUBLE PRECISION CHECK(toxicity_score BETWEEN 0 AND 100),
 toxicity_score_origin TEXT, severity INTEGER, transparency_class TEXT, verdict TEXT, explanation TEXT,
 source_hash TEXT NOT NULL, loaded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 etl_run_id BIGINT NOT NULL REFERENCES dwh.etl_runs(id)
);
CREATE INDEX IF NOT EXISTS ix_fact_analysis_date ON dwh.fact_analysis(analysis_date_sk);
CREATE INDEX IF NOT EXISTS ix_fact_analysis_company ON dwh.fact_analysis(company_sk);
CREATE INDEX IF NOT EXISTS ix_fact_analysis_product ON dwh.fact_analysis(product_sk);
CREATE OR REPLACE VIEW dwh.analysis_history AS
 SELECT f.*, c.company_key, c.company_name, p.product_key, p.product_name, p.brand,
        m.model_name, m.provider_reported_revision, pr.prompt_version, pr.prompt_hash,
        s.scoring_version, d.full_date AS analysis_date
 FROM dwh.fact_analysis f JOIN dwh.dim_company c USING(company_sk)
 JOIN dwh.dim_product p USING(product_sk) JOIN dwh.dim_model m USING(model_sk)
 JOIN dwh.dim_prompt pr USING(prompt_sk) JOIN dwh.dim_scoring s USING(scoring_sk)
 JOIN dwh.dim_date d ON d.date_sk=f.analysis_date_sk;
COMMIT;
