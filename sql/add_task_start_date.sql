ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS start_date DATE;

UPDATE tasks
SET start_date = sxa_start_date
WHERE start_date IS NULL
  AND sxa_start_date IS NOT NULL;
