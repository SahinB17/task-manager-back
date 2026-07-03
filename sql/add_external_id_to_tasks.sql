-- Add external_id column to tasks table
-- Phase 6C: Professional upsert/dedup for global Excel import
-- external_id replaces sxa_task_id as the neutral primary match key

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS external_id VARCHAR;

-- Backfill from sxa_task_id where available
UPDATE tasks
SET external_id = sxa_task_id
WHERE external_id IS NULL AND sxa_task_id IS NOT NULL AND sxa_task_id <> '';

-- Optional: unique index for dedup lookups (only if no duplicates exist)
-- Run this manually after verifying no duplicate external_ids:
-- CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_external_id
-- ON tasks (LOWER(external_id))
-- WHERE external_id IS NOT NULL AND external_id <> '';
