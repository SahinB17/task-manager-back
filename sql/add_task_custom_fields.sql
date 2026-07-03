-- Add JSONB custom_fields column to tasks table
-- Phase 6A: Minimal optional custom fields

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb;
