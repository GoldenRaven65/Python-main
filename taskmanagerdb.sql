-- ============================================================
--  TaskmanagerDB – PostgreSQL DDL & DML
--  Gegenereerd op basis van de Flask-SQLAlchemy modellen in app.py
-- ============================================================

-- ──────────────── 1. DATABASE ────────────────

-- Voer dit uit als superuser BUITEN elke database-context:
-- CREATE DATABASE "TaskmanagerDB" ENCODING 'UTF8';
-- \connect "TaskmanagerDB"


-- ──────────────── 2. TABELLEN ────────────────

CREATE TABLE IF NOT EXISTS "user" (
    id            SERIAL          PRIMARY KEY,
    username      VARCHAR(80)     NOT NULL UNIQUE,
    email         VARCHAR(120)    NOT NULL UNIQUE,
    password_hash VARCHAR(256)    NOT NULL,
    role          VARCHAR(10)     NOT NULL DEFAULT 'user',   -- 'admin' of 'user'
    display_name  VARCHAR(100),
    bio           TEXT,
    avatar_color  VARCHAR(7)      NOT NULL DEFAULT '#ee653f'
);

CREATE TABLE IF NOT EXISTS task (
    id              SERIAL          PRIMARY KEY,
    title           VARCHAR(200)    NOT NULL,
    description     TEXT            DEFAULT '',
    status          VARCHAR(20)     NOT NULL DEFAULT 'open',      -- 'open' | 'bezig' | 'afgerond'
    priority        VARCHAR(10)     NOT NULL DEFAULT 'normaal',   -- 'laag' | 'normaal' | 'hoog'
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    due_date        DATE,
    user_id         INTEGER         NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    assigned_to_id  INTEGER         REFERENCES "user"(id) ON DELETE SET NULL
);


-- ──────────────── 3. INDEXEN ────────────────

-- Snelle lookups op veelgebruikte filters
CREATE INDEX IF NOT EXISTS idx_task_user_id         ON task(user_id);
CREATE INDEX IF NOT EXISTS idx_task_assigned_to_id  ON task(assigned_to_id);
CREATE INDEX IF NOT EXISTS idx_task_status          ON task(status);
CREATE INDEX IF NOT EXISTS idx_task_due_date        ON task(due_date);
CREATE INDEX IF NOT EXISTS idx_user_username        ON "user"(username);
CREATE INDEX IF NOT EXISTS idx_user_email           ON "user"(email);


-- ──────────────── 4. STANDAARD ADMIN-ACCOUNT ────────────────
-- Wachtwoord-hash hieronder is voor 'admin123' gegenereerd met Werkzeug pbkdf2:sha256.
-- Vervang de hash door een eigen Werkzeug-hash in productie!

INSERT INTO "user" (username, email, password_hash, role, avatar_color)
VALUES (
    'admin',
    'admin@taskmanager.local',
    'scrypt:32768:8:1$6KbKjLXGkoGXUye6$987a800572e36c84d6a2420441a630f1ff3c6ce0c0b27bd5085b3c00d6feb00f1c7c2b18b582f41691810d1ceab3a18fe70ebc66c468828f73aa46fb38d68967',
    'admin',
    '#ee653f'
)
ON CONFLICT (username) DO NOTHING;


-- ──────────────── 5. HANDIGE QUERY'S ────────────────

-- --- Alle gebruikers ---
SELECT id, username, email, role, display_name, avatar_color
FROM "user"
ORDER BY username;

-- --- Alle taken (met eigenaar en toegewezene) ---
SELECT
    t.id,
    t.title,
    t.status,
    t.priority,
    t.due_date,
    t.created_at,
    u_owner.username    AS eigenaar,
    u_assign.username   AS toegewezen_aan
FROM task t
JOIN "user" u_owner ON u_owner.id = t.user_id
LEFT JOIN "user" u_assign ON u_assign.id = t.assigned_to_id
ORDER BY t.created_at DESC;

-- --- Taken per status ---
SELECT status, COUNT(*) AS aantal
FROM task
GROUP BY status;

-- --- Open taken gesorteerd op deadline (meest urgent eerst) ---
SELECT
    t.id,
    t.title,
    t.due_date,
    t.priority,
    u_owner.username AS eigenaar
FROM task t
JOIN "user" u_owner ON u_owner.id = t.user_id
WHERE t.status = 'open'
ORDER BY t.due_date ASC NULLS LAST;

-- --- Taken van één specifieke gebruiker (vervang 1 door het gewenste user-id) ---
SELECT t.id, t.title, t.status, t.priority, t.due_date
FROM task t
WHERE t.user_id = 1 OR t.assigned_to_id = 1
ORDER BY t.created_at DESC;

-- --- Taken die over de deadline zijn (status nog niet afgerond) ---
SELECT
    t.id,
    t.title,
    t.due_date,
    t.status,
    u_owner.username AS eigenaar
FROM task t
JOIN "user" u_owner ON u_owner.id = t.user_id
WHERE t.due_date < CURRENT_DATE
  AND t.status <> 'afgerond'
ORDER BY t.due_date ASC;

-- --- Takentelling per gebruiker ---
SELECT
    u.username,
    COUNT(t.id)                                              AS totaal,
    COUNT(t.id) FILTER (WHERE t.status = 'open')            AS open,
    COUNT(t.id) FILTER (WHERE t.status = 'bezig')           AS bezig,
    COUNT(t.id) FILTER (WHERE t.status = 'afgerond')        AS afgerond
FROM "user" u
LEFT JOIN task t ON t.user_id = u.id
GROUP BY u.id, u.username
ORDER BY totaal DESC;

-- --- Zoek taken op trefwoord in titel of omschrijving ---
SELECT id, title, status, priority
FROM task
WHERE title ILIKE '%zoekterm%'
   OR description ILIKE '%zoekterm%';

-- --- Nieuwe taak invoegen ---
INSERT INTO task (title, description, status, priority, due_date, user_id, assigned_to_id)
VALUES (
    'Taaknaam',
    'Omschrijving van de taak',
    'open',
    'normaal',
    '2026-04-01',   -- of NULL als geen deadline
    1,              -- user_id van de maker
    NULL            -- assigned_to_id, of een user-id
);

-- --- Status van een taak bijwerken ---
UPDATE task
SET status = 'afgerond'
WHERE id = 1;

-- --- Taak verwijderen ---
DELETE FROM task
WHERE id = 1;

-- --- Gebruikersprofiel bijwerken ---
UPDATE "user"
SET display_name = 'Martijn W.',
    bio          = 'Task manager gebruiker',
    avatar_color = '#2e86de'
WHERE id = 1;

-- --- Gebruiker verwijderen (taken worden meeverwijderd via ON DELETE CASCADE) ---
DELETE FROM "user"
WHERE id = 2;


-- ──────────────── 6. MIGRATIE (als tabel al bestaat zonder nieuwe kolommen) ────────────────

-- Voeg ontbrekende kolommen toe als je upgradet vanuit een oudere versie:
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS display_name  VARCHAR(100);
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS bio           TEXT;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_color  VARCHAR(7) NOT NULL DEFAULT '#ee653f';
ALTER TABLE task   ADD COLUMN IF NOT EXISTS assigned_to_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL;
