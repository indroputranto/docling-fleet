#!/usr/bin/env python3
"""
One-shot database migration script.

Safe to re-run: each ALTER TABLE is wrapped in a try/except so it skips
columns that already exist (SQLite raises OperationalError on duplicates).

Run with:
    python migrate_db.py
or from the project root with the venv active:
    python migrate_db.py
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform.db")


def col_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def run():
    print(f"Connecting to {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # ── client_configs: Chat UX columns ──────────────────────────────────────
    chat_ux_columns = [
        ("welcome_message",     "TEXT"),
        ("suggested_questions", "TEXT"),
        ("default_theme",       "VARCHAR(10) NOT NULL DEFAULT 'dark'"),
        ("show_mode_toggle",    "BOOLEAN NOT NULL DEFAULT 1"),
    ]

    for col, typedef in chat_ux_columns:
        if col_exists(cur, "client_configs", col):
            print(f"  [skip] client_configs.{col} already exists")
        else:
            cur.execute(f"ALTER TABLE client_configs ADD COLUMN {col} {typedef}")
            print(f"  [+] client_configs.{col} ({typedef})")

    # ── client_configs: rate limiting ────────────────────────────────────────
    if col_exists(cur, "client_configs", "daily_request_limit"):
        print("  [skip] client_configs.daily_request_limit already exists")
    else:
        cur.execute("ALTER TABLE client_configs ADD COLUMN daily_request_limit INTEGER NOT NULL DEFAULT 0")
        print("  [+] client_configs.daily_request_limit (INTEGER NOT NULL DEFAULT 0)")

    # ── usage_logs table ──────────────────────────────────────────────────────
    if table_exists(cur, "usage_logs"):
        print("  [skip] usage_logs table already exists")
    else:
        cur.execute("""
            CREATE TABLE usage_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    VARCHAR(100) NOT NULL,
                user_email   VARCHAR(255),
                timestamp    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                date         DATE     NOT NULL,
                tokens_in    INTEGER  NOT NULL DEFAULT 0,
                tokens_out   INTEGER  NOT NULL DEFAULT 0,
                model        VARCHAR(100),
                response_ms  INTEGER
            )
        """)
        cur.execute("CREATE INDEX ix_usage_logs_client_id ON usage_logs (client_id)")
        cur.execute("CREATE INDEX ix_usage_logs_date ON usage_logs (date)")
        print("  [+] usage_logs table created")

    # ── documents: group_name column ─────────────────────────────────────────
    if col_exists(cur, "documents", "group_name"):
        print("  [skip] documents.group_name already exists")
    else:
        try:
            cur.execute("ALTER TABLE documents ADD COLUMN group_name VARCHAR(500)")
            print("  [+] documents.group_name (VARCHAR(500))")
        except Exception:
            pass  # table may not exist yet — handled below

    # ── documents table ───────────────────────────────────────────────────────
    if table_exists(cur, "documents"):
        print("  [skip] documents table already exists")
    else:
        cur.execute("""
            CREATE TABLE documents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id     VARCHAR(100) NOT NULL,
                filename      VARCHAR(500) NOT NULL,
                file_type     VARCHAR(10)  NOT NULL,
                status        VARCHAR(20)  NOT NULL DEFAULT 'draft',
                chunk_count   INTEGER      NOT NULL DEFAULT 0,
                error_message TEXT,
                group_name    VARCHAR(500),
                uploaded_by   VARCHAR(255),
                uploaded_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at  DATETIME
            )
        """)
        cur.execute("CREATE INDEX ix_documents_client_id ON documents (client_id)")
        print("  [+] documents table created")

    # ── document_chunks table ─────────────────────────────────────────────────
    if table_exists(cur, "document_chunks"):
        print("  [skip] document_chunks table already exists")
    else:
        cur.execute("""
            CREATE TABLE document_chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                position    INTEGER NOT NULL,
                title       VARCHAR(500),
                body        TEXT    NOT NULL,
                pinecone_id VARCHAR(300)
            )
        """)
        cur.execute(
            "CREATE INDEX ix_document_chunks_document_id ON document_chunks (document_id)"
        )
        print("  [+] document_chunks table created")

    # ── documents: vessel_id FK ───────────────────────────────────────────────
    if col_exists(cur, "documents", "vessel_id"):
        print("  [skip] documents.vessel_id already exists")
    else:
        cur.execute(
            "ALTER TABLE documents ADD COLUMN vessel_id INTEGER REFERENCES vessels(id) ON DELETE SET NULL"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ix_documents_vessel_id ON documents (vessel_id)")
        print("  [+] documents.vessel_id (INTEGER FK → vessels.id)")

    # ── vessels table ─────────────────────────────────────────────────────────
    if table_exists(cur, "vessels"):
        print("  [skip] vessels table already exists")
    else:
        cur.execute("""
            CREATE TABLE vessels (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id        VARCHAR(100) NOT NULL,
                name             VARCHAR(500) NOT NULL,
                imo_number       VARCHAR(50),
                call_sign        VARCHAR(50),
                flag_state       VARCHAR(100),
                port_of_registry VARCHAR(100),
                vessel_type      VARCHAR(50),
                year_built       VARCHAR(10),
                gross_tonnage    VARCHAR(50),
                dwat             VARCHAR(50),
                loa              VARCHAR(50),
                notes            TEXT,
                created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_vessels_client_id ON vessels (client_id)")
        print("  [+] vessels table created")

    # ── Backfill documents.vessel_id from group_name ──────────────────────────
    # For every document whose group_name matches a vessel.name (same client),
    # set vessel_id if it is not already set.
    if table_exists(cur, "vessels") and col_exists(cur, "documents", "vessel_id"):
        cur.execute("""
            UPDATE documents
            SET vessel_id = (
                SELECT vessels.id FROM vessels
                WHERE vessels.client_id = documents.client_id
                  AND vessels.name      = documents.group_name
                LIMIT 1
            )
            WHERE vessel_id IS NULL
              AND group_name IS NOT NULL
        """)
        backfilled = cur.rowcount
        if backfilled:
            print(f"  [+] Backfilled vessel_id on {backfilled} document(s) from group_name")
        else:
            print("  [skip] No documents needed vessel_id backfill")

    con.commit()
    con.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    run()
