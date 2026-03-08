-- FitNudge Database Schema
-- PostgreSQL 15+

-- Users
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kakao_id BIGINT UNIQUE NOT NULL,
  kakao_nickname TEXT,
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ NOT NULL,
  workplace_lat TEXT,
  workplace_lng TEXT,
  default_departure_time TIME,
  weekly_goal_count INT DEFAULT 3,
  preferred_exercises TEXT[],
  timezone TEXT DEFAULT 'Asia/Seoul',
  onboarding_complete BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Workout sessions
CREATE TABLE IF NOT EXISTS workout_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  muscle_groups TEXT[] NOT NULL,
  raw_user_message TEXT,
  agent_notes TEXT,
  source TEXT CHECK (source IN ('user_report', 'geofence', 'manual')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Date-keyed notes (agent memory)
CREATE TABLE IF NOT EXISTS agent_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL DEFAULT CURRENT_DATE,
  note TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_notes_user_date ON agent_notes(user_id, date DESC);

-- Conversation history
CREATE TABLE IF NOT EXISTS conversation_turns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  role TEXT CHECK (role IN ('agent', 'user')) NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_user_created ON conversation_turns(user_id, created_at DESC);

-- Streak tracking
CREATE TABLE IF NOT EXISTS streaks (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  current_streak INT DEFAULT 0,
  longest_streak INT DEFAULT 0,
  last_workout_date DATE,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Daily message count guard
CREATE TABLE IF NOT EXISTS daily_message_counts (
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL DEFAULT CURRENT_DATE,
  count INT DEFAULT 0,
  PRIMARY KEY (user_id, date)
);

-- Scheduled follow-ups
CREATE TABLE IF NOT EXISTS scheduled_followups (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  reason TEXT,
  scheduled_for TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
