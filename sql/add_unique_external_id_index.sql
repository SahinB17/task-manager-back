-- ============================================================================
-- OPTIONAL: Unique index on tasks.external_id
-- ============================================================================
-- Purpose: Case-insensitive unique constraint on external_id for dedup safety.
-- Only non-null, non-empty values are constrained.
--
-- SAFETY: Run this file ONLY after the duplicate pre-check returns zero rows.
-- If duplicates exist, the CREATE UNIQUE INDEX will fail.
--
-- Duplicate pre-check (run before this file):
--
--   SELECT LOWER(external_id) AS normalized, COUNT(*) AS dup_count
--   FROM tasks
--   WHERE external_id IS NOT NULL AND external_id <> ''
--   GROUP BY LOWER(external_id)
--   HAVING COUNT(*) > 1;
--
-- The pre-check MUST return zero rows before proceeding. If duplicates are
-- found, resolve them manually (merge, reassign, or delete) then re-run the
-- pre-check until zero rows are returned.
-- ============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_external_id
ON tasks (LOWER(external_id))
WHERE external_id IS NOT NULL AND external_id <> '';
