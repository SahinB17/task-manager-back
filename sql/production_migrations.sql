-- ============================================================================
-- Production Migration SQL (Consolidated, Idempotent)
-- ============================================================================
-- Purpose: Single ordered SQL file for setting up / upgrading a production
--          PostgreSQL database. Safe to run multiple times on the same DB.
--
-- Rules:
--   - Uses IF NOT EXISTS / IF NOT EXISTS wherever possible.
--   - No DROP COLUMN, no destructive statements.
--   - PostgreSQL-compatible.
--   - Sections are ordered and can be run in sequence from top to bottom.
--
-- Migrations covered (in order):
--   A) custom_fields       – tasks.custom_fields (JSONB)
--   B) external_id          – tasks.external_id (VARCHAR)
--   C) start_date           – tasks.start_date (DATE)
--   D) category             – tasks.category (VARCHAR(255))
--   E) category_options     – category_options table + unique index
--   F) notifications        – notifications table + FK + indexes
--   G) external_id index    – unique partial index on LOWER(external_id)
-- ============================================================================

BEGIN;

-- ============================================================================
-- A) custom_fields
--    Add JSONB custom_fields column to tasks if missing.
--    Model: Column(JSONB, nullable=False, default=dict)
-- ============================================================================

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ============================================================================
-- B) external_id
--    Add external_id VARCHAR column if missing, then backfill from sxa_task_id.
--    Model: Column(String, nullable=True, index=True) – no length = unbounded VARCHAR
-- ============================================================================

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS external_id VARCHAR;

-- Backfill external_id from sxa_task_id where missing
UPDATE tasks
SET external_id = sxa_task_id
WHERE external_id IS NULL
  AND sxa_task_id IS NOT NULL
  AND sxa_task_id <> '';

-- ============================================================================
-- C) start_date
--    Add start_date DATE column if missing, then backfill from sxa_start_date.
--    Model: Column(Date, nullable=True)
-- ============================================================================

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS start_date DATE;

-- Backfill start_date from legacy sxa_start_date
UPDATE tasks
SET start_date = sxa_start_date
WHERE start_date IS NULL
  AND sxa_start_date IS NOT NULL;

-- ============================================================================
-- D) category
--    Add category VARCHAR(255) column if missing, then backfill from
--    direction_program (legacy column).
--    Model: Column(String(255), nullable=True)
-- ============================================================================

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS category VARCHAR(255);

-- Backfill category from legacy direction_program
UPDATE tasks
SET category = direction_program
WHERE category IS NULL
  AND direction_program IS NOT NULL
  AND direction_program <> '';

-- ============================================================================
-- E) category_options table
--    Managed category choices for the task form.
--    Columns: id, name, normalized_name, is_active, sort_order, created_at, updated_at
--    Unique index on LOWER(normalized_name)
-- ============================================================================

CREATE TABLE IF NOT EXISTS category_options (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    normalized_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_category_options_normalized_name
ON category_options (LOWER(normalized_name));

-- ============================================================================
-- F) notifications table
--    In-app notification system.
--    Columns: id, user_id, actor_id, task_id, type, title, message, is_read,
--             created_at, read_at, metadata
--    Foreign keys:
--      user_id -> users(id) ON DELETE CASCADE
--      actor_id -> users(id) ON DELETE SET NULL
--      task_id -> tasks(id) ON DELETE CASCADE
--    Indexes: user_id+created_at, user_id+is_read, task_id
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    task_id UUID NULL REFERENCES tasks(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    read_at TIMESTAMP NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id_created_at
    ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id_is_read
    ON notifications (user_id, is_read);

CREATE INDEX IF NOT EXISTS ix_notifications_task_id
    ON notifications (task_id);

-- ============================================================================
-- G) external_id unique index (OPTIONAL — separate file)
--    Case-insensitive unique constraint on external_id for dedup safety.
--    Only non-null, non-empty values are constrained.
--
--    SAFETY: This index creation will FAIL if duplicate external_id values
--    exist. To keep this migration safe to run unconditionally, the active
--    index creation has been moved to a separate optional file.
--
--    Run add_unique_external_id_index.sql only after duplicate pre-check
--    returns zero rows. The pre-check query is documented in that file.
-- ============================================================================

-- Index creation moved to: add_unique_external_id_index.sql

COMMIT;

-- ============================================================================
-- End of production migrations.
-- All statements above are idempotent and safe to re-run.
-- ============================================================================
