-- Add sxa_task_id column for SXA Excel task identification
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS sxa_task_id VARCHAR;
CREATE INDEX IF NOT EXISTS ix_tasks_sxa_task_id ON tasks (sxa_task_id);
