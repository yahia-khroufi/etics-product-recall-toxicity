BEGIN;
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS toxicity_score DOUBLE PRECISION;
ALTER TABLE llm_results ADD COLUMN IF NOT EXISTS toxicity_score_origin VARCHAR(100);
ALTER TABLE llm_results ALTER COLUMN score DROP NOT NULL;
ALTER TABLE llm_results ALTER COLUMN severity DROP NOT NULL;
ALTER TABLE llm_results ALTER COLUMN transparency_class DROP NOT NULL;
ALTER TABLE llm_results ALTER COLUMN verdict DROP NOT NULL;
ALTER TABLE llm_results ALTER COLUMN explanation DROP NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='llm_results'::regclass
                AND conname='ck_llm_results_toxicity_score_range') THEN
  ALTER TABLE llm_results ADD CONSTRAINT ck_llm_results_toxicity_score_range
   CHECK (toxicity_score BETWEEN 0 AND 100);
 END IF;
END $$;
-- A derived metric, explicitly labelled; original verdict/score/timestamp are untouched.
UPDATE llm_results SET toxicity_score = round((
 (NOT q1)::int + (NOT q2)::int + (NOT q3)::int + (NOT q4)::int +
 (NOT q5)::int + q6::int + q7::int)::numeric * 100 / 7, 2),
 toxicity_score_origin='derived_on_migration:seven-equal-weights-v1'
WHERE toxicity_score IS NULL AND score IS NOT NULL;
COMMIT;
