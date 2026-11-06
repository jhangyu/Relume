-- patches version of the build an install row names: the planner reads it here, so a purged build_jobs row (it cascades
-- with old jobs) no longer erases it. NULL = unknown (its build job is already gone).
ALTER TABLE ota_installs ADD COLUMN patches_version TEXT;
UPDATE ota_installs SET patches_version = (SELECT b.patches_version FROM build_jobs b WHERE b.job_id = ota_installs.build_job_id)
  WHERE build_job_id IS NOT NULL;
