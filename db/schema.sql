-- FitNudge Database Schema (PostgreSQL 15+)
-- Reference only — generated from db/models.py. Apply changes with Alembic:
--   uv run alembic upgrade head

CREATE TABLE users (
	id UUID NOT NULL, 
	display_name TEXT, 
	active_channel TEXT DEFAULT 'discord' NOT NULL, 
	kakao_id BIGINT, 
	kakao_nickname TEXT, 
	access_token TEXT, 
	refresh_token TEXT, 
	token_expires_at TIMESTAMP WITH TIME ZONE, 
	workplace_lat VARCHAR, 
	workplace_lng VARCHAR, 
	default_departure_time TIME WITHOUT TIME ZONE, 
	weekly_goal_count INTEGER, 
	preferred_exercises TEXT[], 
	timezone TEXT, 
	onboarding_complete BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (kakao_id)
);

CREATE TABLE channel_identities (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	channel TEXT NOT NULL, 
	external_id TEXT NOT NULL, 
	opted_in_proactive BOOLEAN DEFAULT false NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_channel_identities_channel_external_id UNIQUE (channel, external_id), 
	CONSTRAINT uq_channel_identities_user_channel UNIQUE (user_id, channel), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE conversation_turns (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	role TEXT NOT NULL, 
	content TEXT NOT NULL, 
	proactive BOOLEAN DEFAULT false NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE daily_message_counts (
	user_id UUID NOT NULL, 
	date DATE NOT NULL, 
	proactive_count INTEGER DEFAULT 0 NOT NULL, 
	reply_count INTEGER DEFAULT 0 NOT NULL, 
	PRIMARY KEY (user_id, date), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE scheduled_followups (
	user_id UUID NOT NULL, 
	job_id TEXT NOT NULL, 
	reason TEXT, 
	scheduled_for TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE streaks (
	user_id UUID NOT NULL, 
	current_streak INTEGER, 
	longest_streak INTEGER, 
	last_workout_date DATE, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE workout_sessions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	date DATE NOT NULL, 
	muscle_groups TEXT[] NOT NULL, 
	raw_user_message TEXT, 
	agent_notes TEXT, 
	source TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE memory_entries (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	kind TEXT NOT NULL, 
	text TEXT NOT NULL, 
	pinned BOOLEAN DEFAULT false NOT NULL, 
	superseded_by UUID, 
	source TEXT DEFAULT 'agent' NOT NULL, 
	source_turn_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(superseded_by) REFERENCES memory_entries (id) ON DELETE SET NULL, 
	FOREIGN KEY(source_turn_id) REFERENCES conversation_turns (id) ON DELETE SET NULL
);
CREATE INDEX idx_memory_entries_user_at ON memory_entries (user_id, at DESC);
