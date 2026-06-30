
-- Football Match Organizer — Migration Script
-- Adds missing columns to existing tables (Supabase compatibility)

-- Добавление новых колонок в таблицу матчей
ALTER TABLE matches ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS location TEXT NULL;

-- Добавление игровой позиции в таблицу пользователей
ALTER TABLE users ADD COLUMN IF NOT EXISTS position TEXT DEFAULT 'unknown';

-- Добавление остальных полей статистики (на случай если база очень старая)
ALTER TABLE users ADD COLUMN IF NOT EXISTS skill_level DOUBLE PRECISION DEFAULT 50.0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS goals INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS assists INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS wins INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS losses INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS draws INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS matches_played INT DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mvp_count INT DEFAULT 0;

-- Добавление состава команд, если таблица матчей была создана раннее
ALTER TABLE matches ADD COLUMN IF NOT EXISTS team_a JSONB DEFAULT '[]'::jsonb;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS team_b JSONB DEFAULT '[]'::jsonb;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS score_a INT DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS score_b INT DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'scheduled';
