-- SXA Import Support Migration
-- Run this against your PostgreSQL database before using POST /excel/sxa/import
--
-- Usage:
--   psql -U <user> -d <database> -f add_sxa_import_support.sql

-- Make client_id nullable for SXA project tasks that have no client
ALTER TABLE tasks ALTER COLUMN client_id DROP NOT NULL;
