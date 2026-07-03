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
