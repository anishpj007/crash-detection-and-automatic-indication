-- Automatic Crash Detection System - Database Schema

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',   -- 'admin' or 'operator'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    location_name TEXT DEFAULT 'Unknown',
    latitude REAL DEFAULT 0,
    longitude REAL DEFAULT 0,
    uploaded_by INTEGER,
    status TEXT NOT NULL DEFAULT 'uploaded',  -- uploaded, processing, processed, failed
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    frame_number INTEGER NOT NULL,
    timestamp_in_video REAL NOT NULL,        -- seconds into the video
    severity TEXT NOT NULL,                  -- Low, Medium, High
    confidence REAL NOT NULL,
    snapshot_path TEXT,
    location_name TEXT DEFAULT 'Unknown',
    latitude REAL DEFAULT 0,
    longitude REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',      -- new, acknowledged, resolved, false_alarm
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    channel TEXT NOT NULL,                   -- email, sms, log
    recipient TEXT,
    sent_status TEXT NOT NULL DEFAULT 'simulated',
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

CREATE TABLE IF NOT EXISTS training_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,                  -- stored filename on disk
    original_name TEXT NOT NULL,
    duration REAL DEFAULT 0,                 -- seconds
    fps REAL DEFAULT 25,
    crash_intervals TEXT NOT NULL DEFAULT '[]',  -- JSON [[start,end], ...]
    normal_intervals TEXT NOT NULL DEFAULT '[]', -- JSON [[start,end], ...]
    label TEXT NOT NULL DEFAULT 'unlabeled', -- unlabeled, crash, normal, mixed
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    added_by INTEGER,
    FOREIGN KEY (added_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, done, failed
    samples_total INTEGER DEFAULT 0,
    samples_positive INTEGER DEFAULT 0,
    samples_negative INTEGER DEFAULT 0,
    accuracy REAL DEFAULT 0,
    f1 REAL DEFAULT 0,
    log TEXT DEFAULT '',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
