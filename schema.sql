
-- Football Match Organizer — Database Schema
-- PostgreSQL (Supabase compatible)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ===================== USERS =====================
CREATE TABLE IF NOT EXISTS users (
    id              BIGINT PRIMARY KEY,
    username        TEXT,
    name            TEXT,
    photo_url       TEXT,
    position        TEXT DEFAULT 'unknown',
    skill_level     DOUBLE PRECISION DEFAULT 50.0,
    goals           INT DEFAULT 0,
    assists         INT DEFAULT 0,
    wins            INT DEFAULT 0,
    losses          INT DEFAULT 0,
    draws           INT DEFAULT 0,
    matches_played  INT DEFAULT 0,
    mvp_count       INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ===================== MATCHES =====================
CREATE TABLE IF NOT EXISTS matches (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status        TEXT DEFAULT 'scheduled' CHECK (status IN ('scheduled','active','finished')),
    scheduled_at  TIMESTAMPTZ NULL,
    started_at    TIMESTAMPTZ NULL,
    finished_at   TIMESTAMPTZ NULL,
    location      TEXT NULL,
    team_a        JSONB DEFAULT '[]'::jsonb,
    team_b        JSONB DEFAULT '[]'::jsonb,
    score_a       INT DEFAULT 0,
    score_b       INT DEFAULT 0
);

-- ===================== REGISTRATIONS =====================
CREATE TABLE IF NOT EXISTS match_registrations (
    match_id  UUID REFERENCES matches(id) ON DELETE CASCADE,
    user_id   BIGINT REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (match_id, user_id)
);

-- ===================== MVP VOTES =====================
CREATE TABLE IF NOT EXISTS mvp_votes (
    match_id     UUID REFERENCES matches(id) ON DELETE CASCADE,
    voter_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    candidate_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (match_id, voter_id)
);

-- ===================== ACHIEVEMENTS =====================
CREATE TABLE IF NOT EXISTS user_achievements (
    user_id           BIGINT REFERENCES users(id) ON DELETE CASCADE,
    achievement_code  TEXT NOT NULL,
    unlocked_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, achievement_code)
);

-- ===================== INDEXES =====================
CREATE INDEX IF NOT EXISTS idx_users_skill_level ON users (skill_level DESC);
CREATE INDEX IF NOT EXISTS idx_matches_finished_at ON matches (finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches (status);
CREATE INDEX IF NOT EXISTS idx_registrations_match ON match_registrations (match_id);
CREATE INDEX IF NOT EXISTS idx_mvp_votes_match ON mvp_votes (match_id, candidate_id);
