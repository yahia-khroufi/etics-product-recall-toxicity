-- 1. Evolution by company/product/month and model+prompt+scoring version.
-- Counts analyses, NOT distinct documents: repeat analyses are retained.
SELECT company_key, company_name, product_key, product_name,
       date_trunc('month',analysis_created_at AT TIME ZONE 'UTC') AS month,
       model_name, provider_reported_revision, prompt_version, prompt_hash, scoring_version,
       context_origin,analysis_status,evidence_verified, count(*) AS analyses,
       avg(completeness_score) AS mean_completeness,
       avg(toxicity_score) AS mean_problem_index,
       count(*) FILTER (WHERE verdict='toxique')::numeric / NULLIF(count(verdict),0) AS toxic_rate
FROM dwh.analysis_history
WHERE verdict IS NOT NULL
-- Add AND evidence_verified AND analysis_status='scored' for validated new runs only.
GROUP BY company_key,company_name,product_key,product_name,month,
         model_name,provider_reported_revision,prompt_version,prompt_hash,scoring_version,context_origin,analysis_status,evidence_verified
ORDER BY month,company_key,product_key;

-- 2. All original analyses, INCLUDING unlinked legacy results and pending reviews.
SELECT source_result_id,case_id,analysis_created_at,company_name,product_name,
       model_name,provider_reported_revision,prompt_version,scoring_version,
       completeness_score,toxicity_score,toxicity_score_origin,verdict,
       analysis_status,evidence_verified,context_origin,company_version_sk
FROM dwh.analysis_history ORDER BY case_id,analysis_created_at,source_result_id;

-- 3. Compare analyses of the SAME observed communication and SAME scoring rule.
-- A model/prompt change is visible separately; this is not evidence of a document edit.
WITH ordered AS (
 SELECT source_result_id,case_id,company_version_sk,analysis_created_at,model_name,
        prompt_version,scoring_version,toxicity_score,verdict,
        lag(toxicity_score) OVER w AS previous_score,
        lag(verdict) OVER w AS previous_verdict
 FROM dwh.analysis_history
 WHERE company_version_sk IS NOT NULL AND analysis_status='scored' AND evidence_verified
 WINDOW w AS (PARTITION BY company_version_sk,scoring_version
              ORDER BY analysis_created_at,source_result_id)
)
SELECT *,toxicity_score-previous_score AS score_delta FROM ordered
ORDER BY case_id,analysis_created_at,source_result_id;

-- 4. Observed bytes/text/provenance changes; NEVER infer a publication/edit date.
WITH observed AS (
SELECT source_document_id,role,version_number,observed_at,source_url,
       raw_sha256,text_sha256,
       lag(text_sha256) OVER w AS previous_text_sha256,
       lag(raw_sha256) OVER w AS previous_raw_sha256
FROM dwh.dim_document_version
WINDOW w AS (PARTITION BY source_document_id ORDER BY version_number)
)
SELECT *,CASE WHEN version_number=1 THEN 'initial_observation'
              WHEN text_sha256 IS DISTINCT FROM previous_text_sha256 THEN 'extracted_text_changed'
              WHEN raw_sha256 IS DISTINCT FROM previous_raw_sha256 THEN 'raw_file_changed'
              ELSE 'provenance_only_changed' END AS observed_change
FROM observed ORDER BY source_document_id,version_number;

-- 5. SCD2 descriptive history: observation time, not a reconstructed legal validity date.
SELECT company_key,company_name,organization_key,valid_from,valid_to,is_current
FROM dwh.dim_company ORDER BY company_key,valid_from;
SELECT product_key,product_name,brand,gtin,valid_from,valid_to,is_current
FROM dwh.dim_product ORDER BY product_key,valid_from;

-- 6. Latest validated analysis per observed communication, for document-level counts.
WITH ranked AS (
 SELECT *,row_number() OVER (PARTITION BY company_version_sk
          ORDER BY analysis_created_at DESC,source_result_id DESC) AS rn
 FROM dwh.analysis_history
 WHERE company_version_sk IS NOT NULL AND analysis_status='scored' AND evidence_verified
)
SELECT company_key,company_name,count(*) AS document_versions,
       count(*) FILTER (WHERE verdict='toxique') AS flagged_versions
FROM ranked WHERE rn=1 GROUP BY company_key,company_name;

-- 7. Coverage and limitations, to show beside historical charts.
SELECT context_origin,analysis_status,evidence_verified,
       count(*) AS analyses,count(company_version_sk) AS linked_to_communication,
       count(completeness_score) AS with_score
FROM dwh.fact_analysis GROUP BY context_origin,analysis_status,evidence_verified;
