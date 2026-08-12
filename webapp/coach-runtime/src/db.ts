import Database from "better-sqlite3";

export type SqliteDb = Database.Database;

let dbInstance: SqliteDb | null | undefined;

/**
 * Open the shared SQLite database that the Python backend also uses.
 *
 * The DATABASE_URL env var uses Python's sqlalchemy format
 * (e.g. "sqlite+aiosqlite:///C:/Users/.../aiming_cookie.db").
 * We strip the scheme prefix and pass the raw path to better-sqlite3.
 *
 * Returns null when DATABASE_URL is not set (e.g. in unit tests or
 * when the sidecar runs without a backing database). Callers should
 * fall back to the HTTP tool bridge in that case.
 */
export function getDb(): SqliteDb | null {
  if (dbInstance !== undefined) return dbInstance;
  const raw = process.env.DATABASE_URL;
  if (!raw) {
    dbInstance = null;
    return null;
  }
  // Strip Python sqlalchemy prefix: "sqlite+aiosqlite:///path" → "path"
  const path = raw.replace(/^sqlite(\+\w+)?:\/\/+/, "");
  if (!path) {
    dbInstance = null;
    return null;
  }
  const db = new Database(path, { readonly: false, fileMustExist: true });
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  dbInstance = db;
  return db;
}

/** Reset the cached instance (for tests). */
export function resetDbForTest(): void {
  if (dbInstance) dbInstance.close();
  dbInstance = undefined;
}
