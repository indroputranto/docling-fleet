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

    # ── documents.document_category ──────────────────────────────────────────
    if col_exists(cur, "documents", "document_category"):
        print("  [skip] documents.document_category already exists")
    else:
        cur.execute("ALTER TABLE documents ADD COLUMN document_category VARCHAR(100)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_documents_document_category ON documents (document_category)")
        print("  [+] documents.document_category (VARCHAR(100))")

    # ── dossier_section_configs table ─────────────────────────────────────────
    if table_exists(cur, "dossier_section_configs"):
        print("  [skip] dossier_section_configs table already exists")
    else:
        cur.execute("""
            CREATE TABLE dossier_section_configs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id VARCHAR(100) NOT NULL
                          REFERENCES client_configs(client_id) ON DELETE CASCADE,
                slug      VARCHAR(50)  NOT NULL,
                label     VARCHAR(100),
                active    BOOLEAN      NOT NULL DEFAULT 1,
                UNIQUE (client_id, slug)
            )
        """)
        cur.execute(
            "CREATE INDEX ix_dossier_section_configs_client_id "
            "ON dossier_section_configs (client_id)"
        )
        print("  [+] dossier_section_configs table created")

    # ── documents: coverage columns ───────────────────────────────────────────
    for col, typedef in [("coverage_pct", "INTEGER"), ("coverage_notes", "TEXT")]:
        if col_exists(cur, "documents", col):
            print(f"  [skip] documents.{col} already exists")
        else:
            cur.execute(f"ALTER TABLE documents ADD COLUMN {col} {typedef}")
            print(f"  [+] documents.{col} ({typedef})")

    # ── documents: object storage key ────────────────────────────────────────
    if col_exists(cur, "documents", "storage_key"):
        print("  [skip] documents.storage_key already exists")
    else:
        cur.execute("ALTER TABLE documents ADD COLUMN storage_key VARCHAR(1000)")
        print("  [+] documents.storage_key (VARCHAR(1000))")

    # ── documents: skip_ai_enrichment ─────────────────────────────────────────
    if col_exists(cur, "documents", "skip_ai_enrichment"):
        print("  [skip] documents.skip_ai_enrichment already exists")
    else:
        cur.execute(
            "ALTER TABLE documents ADD COLUMN skip_ai_enrichment BOOLEAN NOT NULL DEFAULT 0"
        )
        print("  [+] documents.skip_ai_enrichment (BOOLEAN NOT NULL DEFAULT 0)")

    # ── documents: force_reocr ────────────────────────────────────────────────
    if col_exists(cur, "documents", "force_reocr"):
        print("  [skip] documents.force_reocr already exists")
    else:
        cur.execute(
            "ALTER TABLE documents ADD COLUMN force_reocr BOOLEAN NOT NULL DEFAULT 0"
        )
        print("  [+] documents.force_reocr (BOOLEAN NOT NULL DEFAULT 0)")

    # ── chat_sessions table ───────────────────────────────────────────────────
    if table_exists(cur, "chat_sessions"):
        print("  [skip] chat_sessions table already exists")
    else:
        cur.execute("""
            CREATE TABLE chat_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email      VARCHAR(255) NOT NULL,
                client_id       VARCHAR(100) NOT NULL,
                label           VARCHAR(500) NOT NULL DEFAULT 'New chat',
                conversation_id VARCHAR(100),
                created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_chat_sessions_user_email ON chat_sessions (user_email)")
        cur.execute("CREATE INDEX ix_chat_sessions_client_id  ON chat_sessions (client_id)")
        print("  [+] chat_sessions table created")

    # ── chat_messages table ───────────────────────────────────────────────────
    if table_exists(cur, "chat_messages"):
        print("  [skip] chat_messages table already exists")
    else:
        cur.execute("""
            CREATE TABLE chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role       VARCHAR(20)  NOT NULL,
                content    TEXT         NOT NULL,
                position   INTEGER      NOT NULL,
                created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(
            "CREATE INDEX ix_chat_messages_session_id ON chat_messages (session_id)"
        )
        print("  [+] chat_messages table created")

    # ── vessels: cargo-pipeline hold-geometry columns ────────────────────────
    for col, typedef in [
        ("holds_json",           "TEXT"),
        ("hold_capacity_m3",     "FLOAT"),
        ("double_bottom_height", "FLOAT"),
    ]:
        if col_exists(cur, "vessels", col):
            print(f"  [skip] vessels.{col} already exists")
        else:
            try:
                cur.execute(f"ALTER TABLE vessels ADD COLUMN {col} {typedef}")
                print(f"  [+] vessels.{col} ({typedef})")
            except Exception:
                pass  # table may not exist yet — handled by db.create_all

    # ── vessel_trips table (Cargo pipeline) ──────────────────────────────────
    if table_exists(cur, "vessel_trips"):
        print("  [skip] vessel_trips table already exists")
    else:
        cur.execute("""
            CREATE TABLE vessel_trips (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       VARCHAR(100) NOT NULL,
                vessel_id       INTEGER      NOT NULL
                                REFERENCES vessels(id) ON DELETE CASCADE,
                label           VARCHAR(500) NOT NULL DEFAULT 'Untitled trip',
                departure_port  VARCHAR(255),
                arrival_port    VARCHAR(255),
                departure_date  DATE,
                arrival_date    DATE,
                status          VARCHAR(20)  NOT NULL DEFAULT 'planned',
                notes           TEXT,
                created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_vessel_trips_client_id ON vessel_trips (client_id)")
        cur.execute("CREATE INDEX ix_vessel_trips_vessel_id ON vessel_trips (vessel_id)")
        print("  [+] vessel_trips table created")

    # ── cargo_manifests table ────────────────────────────────────────────────
    if table_exists(cur, "cargo_manifests"):
        print("  [skip] cargo_manifests table already exists")
    else:
        cur.execute("""
            CREATE TABLE cargo_manifests (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id        VARCHAR(100) NOT NULL,
                vessel_id        INTEGER      NOT NULL
                                 REFERENCES vessels(id) ON DELETE CASCADE,
                trip_id          INTEGER
                                 REFERENCES vessel_trips(id) ON DELETE SET NULL,
                filename         VARCHAR(500) NOT NULL,
                file_type        VARCHAR(10)  NOT NULL,
                storage_key      VARCHAR(1000),
                status           VARCHAR(20)  NOT NULL DEFAULT 'draft',
                error_message    TEXT,
                voyage_label     VARCHAR(500),
                departure_port   VARCHAR(255),
                arrival_port     VARCHAR(255),
                departure_date   DATE,
                total_items      INTEGER      NOT NULL DEFAULT 0,
                total_weight_kg  FLOAT        NOT NULL DEFAULT 0,
                total_volume_m3  FLOAT        NOT NULL DEFAULT 0,
                placed_count     INTEGER      NOT NULL DEFAULT 0,
                unplaced_count   INTEGER      NOT NULL DEFAULT 0,
                balance_score    FLOAT,
                layout_json      TEXT,
                uploaded_by      VARCHAR(255),
                uploaded_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                packed_at        DATETIME,
                updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_cargo_manifests_client_id ON cargo_manifests (client_id)")
        cur.execute("CREATE INDEX ix_cargo_manifests_vessel_id ON cargo_manifests (vessel_id)")
        cur.execute("CREATE INDEX ix_cargo_manifests_trip_id   ON cargo_manifests (trip_id)")
        print("  [+] cargo_manifests table created")

    # ── cargo_items table ────────────────────────────────────────────────────
    if table_exists(cur, "cargo_items"):
        print("  [skip] cargo_items table already exists")
    else:
        cur.execute("""
            CREATE TABLE cargo_items (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                manifest_id            INTEGER      NOT NULL
                                       REFERENCES cargo_manifests(id) ON DELETE CASCADE,
                position               INTEGER      NOT NULL,
                item_id                VARCHAR(255) NOT NULL,
                description            TEXT,
                packing_type           VARCHAR(100),
                length_m               FLOAT        NOT NULL DEFAULT 0,
                width_m                FLOAT        NOT NULL DEFAULT 0,
                height_m               FLOAT        NOT NULL DEFAULT 0,
                volume_m3              FLOAT        NOT NULL DEFAULT 0,
                net_weight_kg          FLOAT,
                gross_weight_kg        FLOAT        NOT NULL DEFAULT 0,
                imo_flag               BOOLEAN      NOT NULL DEFAULT 0,
                can_stack              BOOLEAN      NOT NULL DEFAULT 1,
                can_rotate_horizontal  BOOLEAN      NOT NULL DEFAULT 1,
                color_hex              VARCHAR(20),
                raw_row_json           TEXT,
                created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_cargo_items_manifest_id ON cargo_items (manifest_id)")
        print("  [+] cargo_items table created")

    # ── cargo_placements table ───────────────────────────────────────────────
    if table_exists(cur, "cargo_placements"):
        print("  [skip] cargo_placements table already exists")
    else:
        cur.execute("""
            CREATE TABLE cargo_placements (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                manifest_id     INTEGER NOT NULL
                                REFERENCES cargo_manifests(id) ON DELETE CASCADE,
                item_id         INTEGER NOT NULL UNIQUE
                                REFERENCES cargo_items(id) ON DELETE CASCADE,
                is_placed       BOOLEAN NOT NULL DEFAULT 0,
                hold_id         INTEGER,
                level           VARCHAR(20),
                x_m             FLOAT,
                y_m             FLOAT,
                z_m             FLOAT,
                rotation_deg    INTEGER NOT NULL DEFAULT 0,
                unplaced_reason VARCHAR(255)
            )
        """)
        cur.execute(
            "CREATE INDEX ix_cargo_placements_manifest_id "
            "ON cargo_placements (manifest_id)"
        )
        print("  [+] cargo_placements table created")

    # ── cargo_placements: is_pinned column (manual-move override) ────────────
    if table_exists(cur, "cargo_placements"):
        if col_exists(cur, "cargo_placements", "is_pinned"):
            print("  [skip] cargo_placements.is_pinned already exists")
        else:
            cur.execute(
                "ALTER TABLE cargo_placements "
                "ADD COLUMN is_pinned BOOLEAN NOT NULL DEFAULT 0"
            )
            print("  [+] cargo_placements.is_pinned (BOOLEAN NOT NULL DEFAULT 0)")

    con.commit()
    con.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    run()
