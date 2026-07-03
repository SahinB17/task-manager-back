ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS category VARCHAR(255);

UPDATE tasks
SET category = direction_program
WHERE category IS NULL
  AND direction_program IS NOT NULL
  AND direction_program <> '';
