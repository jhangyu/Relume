-- Patches that failed in a successful build (the CLI runs with --continue-on-error): JSON list of names, NULL when none.
ALTER TABLE build_jobs ADD COLUMN failed_patches TEXT;
