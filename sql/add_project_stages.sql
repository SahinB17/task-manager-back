CREATE TABLE IF NOT EXISTS project_stages (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id),
    name VARCHAR(255) NOT NULL,
    weight_percent NUMERIC(5, 2),
    planned_start DATE,
    planned_end DATE,
    actual_end DATE,
    status VARCHAR(50),
    delay_reason TEXT,
    completion_percent NUMERIC(5, 2),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_project_stages_project_id
    ON project_stages (project_id);
