CREATE TABLE IF NOT EXISTS cities (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, ascii TEXT NOT NULL, name_key TEXT NOT NULL, ascii_key TEXT NOT NULL,
  country TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, population INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS cities_big_lat ON cities (lat) WHERE population >= 100000;
CREATE INDEX IF NOT EXISTS cities_name_key ON cities (name_key);
CREATE INDEX IF NOT EXISTS cities_ascii_key ON cities (ascii_key);
CREATE TABLE IF NOT EXISTS countries (code TEXT PRIMARY KEY, name TEXT NOT NULL);
