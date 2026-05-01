import base64
import hashlib
import sqlite3
import threading
from contextlib import contextmanager

from cryptography.fernet import Fernet

from config import DB_NAME, ENCRYPTION_KEY


FILE_COLUMNS = (
    "id, message_id, chat_id, file_id, local_path, storage_mode, "
    "file_unique_id, file_name_enc, caption_enc, file_size, mime_type, "
    "access_key, is_encrypted, encryption_key, backup_message_id, "
    "backup_chat_id, upload_date"
)


def get_fernet_key(key_string):
    digest = hashlib.sha256(key_string.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


cipher = Fernet(get_fernet_key(ENCRYPTION_KEY or "development-unsafe-key"))


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        self._lock = threading.RLock()
        self.init_db()

    @contextmanager
    def _transaction(self):
        with self._lock:
            yield
            self.conn.commit()

    def _columns(self, table_name):
        self.cursor.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in self.cursor.fetchall()}

    def _add_column(self, table_name, column_name, column_type):
        if column_name in self._columns(table_name):
            return
        self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def encrypt_text(self, text):
        if text is None:
            return None
        return cipher.encrypt(str(text).encode("utf-8")).decode("utf-8")

    def decrypt_text(self, encrypted_text):
        if encrypted_text is None:
            return None
        try:
            return cipher.decrypt(str(encrypted_text).encode("utf-8")).decode("utf-8")
        except Exception:
            # Backward compatibility for rows that were saved before encryption existed.
            return encrypted_text

    def init_db(self):
        with self._transaction():
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER DEFAULT 0,
                    chat_id INTEGER DEFAULT 0,
                    file_id TEXT,
                    local_path TEXT,
                    storage_mode TEXT DEFAULT 'local',
                    file_unique_id TEXT NOT NULL,
                    file_name_enc TEXT,
                    caption_enc TEXT,
                    file_size INTEGER DEFAULT 0,
                    mime_type TEXT,
                    access_key TEXT UNIQUE,
                    is_encrypted BOOLEAN DEFAULT 0,
                    encryption_key TEXT,
                    backup_message_id INTEGER DEFAULT 0,
                    backup_chat_id INTEGER DEFAULT 0,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    status TEXT DEFAULT 'active',
                    accepted_terms BOOLEAN DEFAULT 0,
                    ban_until TIMESTAMP,
                    ban_reason TEXT,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    access_key TEXT NOT NULL UNIQUE,
                    owner_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(owner_id, name)
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS collection_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL,
                    file_id INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(collection_id, file_id),
                    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS download_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_key TEXT NOT NULL UNIQUE,
                    owner_id INTEGER NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    source_title TEXT,
                    start_message_id INTEGER,
                    limit_count INTEGER DEFAULT 0,
                    dest TEXT DEFAULT 'collection',
                    collection_id INTEGER,
                    collection_key TEXT,
                    status TEXT DEFAULT 'pending',
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    last_message_id INTEGER,
                    error_summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS source_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    file_db_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_chat_id, source_message_id),
                    FOREIGN KEY(file_db_id) REFERENCES files(id) ON DELETE CASCADE
                )
                """
            )

            for column_name, column_type in [
                ("local_path", "TEXT"),
                ("storage_mode", "TEXT DEFAULT 'local'"),
                ("access_key", "TEXT"),
                ("is_encrypted", "BOOLEAN DEFAULT 0"),
                ("encryption_key", "TEXT"),
                ("backup_message_id", "INTEGER DEFAULT 0"),
                ("backup_chat_id", "INTEGER DEFAULT 0"),
                ("upload_date", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ]:
                self._add_column("files", column_name, column_type)

            for column_name, column_type in [
                ("accepted_terms", "BOOLEAN DEFAULT 0"),
                ("ban_until", "TIMESTAMP"),
                ("ban_reason", "TEXT"),
                ("last_active", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ]:
                self._add_column("users", column_name, column_type)

            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_access_key ON files(access_key)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_file_id ON files(file_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_cache_lookup ON source_cache(source_chat_id, source_message_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_collections_owner ON collections(owner_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_files_collection ON collection_files(collection_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_tasks_owner ON download_tasks(owner_id, created_at)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_tasks_key ON download_tasks(task_key)")

    def add_file(
        self,
        message_id,
        chat_id,
        file_id,
        file_unique_id,
        file_name,
        caption,
        file_size,
        mime_type,
        local_path=None,
        storage_mode="local",
        access_key=None,
        is_encrypted=False,
        encryption_key=None,
        backup_message_id=0,
        backup_chat_id=0,
    ):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO files (
                    message_id, chat_id, file_id, local_path, storage_mode,
                    file_unique_id, file_name_enc, caption_enc, file_size,
                    mime_type, access_key, is_encrypted, encryption_key,
                    backup_message_id, backup_chat_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    chat_id,
                    file_id,
                    local_path,
                    storage_mode,
                    file_unique_id,
                    self.encrypt_text(file_name),
                    self.encrypt_text(caption),
                    file_size or 0,
                    mime_type,
                    access_key,
                    1 if is_encrypted else 0,
                    encryption_key,
                    backup_message_id or 0,
                    backup_chat_id or 0,
                ),
            )
            return self.cursor.lastrowid

    def _file_from_row(self, row):
        if not row:
            return None
        return {
            "id": row[0],
            "message_id": row[1],
            "chat_id": row[2],
            "file_id": row[3],
            "local_path": row[4],
            "storage_mode": row[5],
            "file_unique_id": row[6],
            "file_name": self.decrypt_text(row[7]),
            "caption": self.decrypt_text(row[8]),
            "file_size": row[9],
            "mime_type": row[10],
            "access_key": row[11],
            "is_encrypted": row[12],
            "encryption_key": row[13],
            "backup_message_id": row[14] if len(row) > 14 else 0,
            "backup_chat_id": row[15] if len(row) > 15 else 0,
            "upload_date": row[16] if len(row) > 16 else None,
        }

    def get_file_by_key(self, key):
        self.cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE access_key = ?", (key,))
        return self._file_from_row(self.cursor.fetchone())

    def get_file_by_id(self, file_id):
        self.cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id = ?", (file_id,))
        return self._file_from_row(self.cursor.fetchone())

    def get_file_name_by_access_key(self, access_key):
        file_info = self.get_file_by_key(access_key)
        return file_info["file_name"] if file_info else None

    def get_cached_file_for_source(self, source_chat_id, source_message_id):
        self.cursor.execute(
            """
            SELECT file_db_id
            FROM source_cache
            WHERE source_chat_id = ? AND source_message_id = ?
            """,
            (source_chat_id, source_message_id),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return self.get_file_by_id(row[0])

    def cache_source_file(self, source_chat_id, source_message_id, file_db_id):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO source_cache (source_chat_id, source_message_id, file_db_id)
                VALUES (?, ?, ?)
                ON CONFLICT(source_chat_id, source_message_id)
                DO UPDATE SET file_db_id = excluded.file_db_id
                """,
                (source_chat_id, source_message_id, file_db_id),
            )

    def search_files(self, keyword, limit=50):
        self.cursor.execute(f"SELECT {FILE_COLUMNS} FROM files ORDER BY upload_date DESC")
        results = []
        keyword = keyword.lower()
        for row in self.cursor.fetchall():
            file_info = self._file_from_row(row)
            name = file_info.get("file_name") or ""
            caption = file_info.get("caption") or ""
            if keyword in name.lower() or keyword in caption.lower():
                results.append(file_info)
                if len(results) >= limit:
                    break
        return results

    def search_user_files(self, owner_id, keyword, limit=20):
        self.cursor.execute(
            f"""
            SELECT {", ".join(f"f.{column.strip()}" for column in FILE_COLUMNS.split(","))}, c.name
            FROM files f
            JOIN collection_files cf ON f.id = cf.file_id
            JOIN collections c ON cf.collection_id = c.id
            WHERE c.owner_id = ?
            ORDER BY cf.added_at DESC
            """,
            (owner_id,),
        )
        results = []
        keyword = keyword.lower()
        for row in self.cursor.fetchall():
            collection_name = row[-1]
            file_row = row[:-1]
            file_info = self._file_from_row(file_row)
            name = file_info.get("file_name") or ""
            caption = file_info.get("caption") or ""
            if keyword in name.lower() or keyword in caption.lower():
                file_info["collection_name"] = collection_name
                results.append(file_info)
                if len(results) >= limit:
                    break
        return results

    def get_all_files(self):
        self.cursor.execute(f"SELECT {FILE_COLUMNS} FROM files ORDER BY upload_date DESC LIMIT 50")
        return [self._file_from_row(row) for row in self.cursor.fetchall()]

    def init_collections_table(self):
        # Kept for backward compatibility with older imports.
        return None

    def create_collection(self, name, access_key, owner_id):
        try:
            with self._transaction():
                self.cursor.execute(
                    """
                    INSERT INTO collections (name, access_key, owner_id)
                    VALUES (?, ?, ?)
                    """,
                    (name, access_key, owner_id),
                )
                return self.cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    def get_collection_by_key(self, access_key):
        self.cursor.execute("SELECT * FROM collections WHERE access_key = ?", (access_key,))
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "access_key": row[2],
            "owner_id": row[3],
            "created_at": row[4],
        }

    def get_user_collections(self, owner_id):
        self.cursor.execute(
            """
            SELECT c.*, COUNT(cf.file_id) AS file_count
            FROM collections c
            LEFT JOIN collection_files cf ON c.id = cf.collection_id
            WHERE c.owner_id = ?
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """,
            (owner_id,),
        )
        return [
            {
                "id": row[0],
                "name": row[1],
                "access_key": row[2],
                "owner_id": row[3],
                "created_at": row[4],
                "file_count": row[5],
            }
            for row in self.cursor.fetchall()
        ]

    def add_file_to_collection(self, collection_id, file_id):
        try:
            with self._transaction():
                self.cursor.execute(
                    """
                    INSERT OR IGNORE INTO collection_files (collection_id, file_id)
                    VALUES (?, ?)
                    """,
                    (collection_id, file_id),
                )
            return True
        except sqlite3.Error:
            return False

    def get_collection_files(self, collection_id):
        self.cursor.execute(
            f"""
            SELECT {", ".join(f"f.{column.strip()}" for column in FILE_COLUMNS.split(","))}
            FROM files f
            JOIN collection_files cf ON f.id = cf.file_id
            WHERE cf.collection_id = ?
            ORDER BY cf.added_at
            """,
            (collection_id,),
        )
        return [self._file_from_row(row) for row in self.cursor.fetchall()]

    def get_last_file_id(self):
        self.cursor.execute("SELECT id FROM files ORDER BY id DESC LIMIT 1")
        row = self.cursor.fetchone()
        return row[0] if row else None

    def get_collection_by_name(self, name, owner_id):
        self.cursor.execute(
            "SELECT * FROM collections WHERE name = ? AND owner_id = ?",
            (name, owner_id),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "access_key": row[2],
            "owner_id": row[3],
            "created_at": row[4],
        }

    def update_user(self, user_id, username, first_name):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO users (id, username, first_name, last_active)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_active = CURRENT_TIMESTAMP
                """,
                (user_id, username, first_name),
            )

    def get_user(self, user_id):
        self.cursor.execute(
            """
            SELECT id, username, first_name, status, ban_until, accepted_terms, ban_reason, last_active
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "first_name": row[2],
            "status": row[3],
            "ban_until": row[4],
            "accepted_terms": row[5],
            "ban_reason": row[6],
            "last_active": row[7],
        }

    def accept_terms(self, user_id):
        self.update_user_terms(user_id, True)

    def get_all_users(self):
        self.cursor.execute(
            """
            SELECT id, username, first_name, status, ban_until, accepted_terms, ban_reason, last_active
            FROM users
            ORDER BY last_active DESC
            """
        )
        return [
            {
                "id": row[0],
                "username": row[1],
                "first_name": row[2],
                "status": row[3],
                "ban_until": row[4],
                "accepted_terms": row[5],
                "ban_reason": row[6],
                "last_active": row[7],
            }
            for row in self.cursor.fetchall()
        ]

    def set_user_ban(self, user_id, status, ban_until=None, reason=None):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO users (id, status, ban_until, ban_reason, last_active)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    ban_until = excluded.ban_until,
                    ban_reason = excluded.ban_reason,
                    last_active = CURRENT_TIMESTAMP
                """,
                (user_id, status, ban_until, reason),
            )

    def update_user_terms(self, user_id, accepted=True):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO users (id, accepted_terms, last_active)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    accepted_terms = excluded.accepted_terms,
                    last_active = CURRENT_TIMESTAMP
                """,
                (user_id, 1 if accepted else 0),
            )

    def _download_task_from_row(self, row):
        if not row:
            return None
        return {
            "id": row[0],
            "task_key": row[1],
            "owner_id": row[2],
            "source_chat_id": row[3],
            "source_title": row[4],
            "start_message_id": row[5],
            "limit_count": row[6],
            "dest": row[7],
            "collection_id": row[8],
            "collection_key": row[9],
            "status": row[10],
            "success_count": row[11],
            "fail_count": row[12],
            "last_message_id": row[13],
            "error_summary": row[14],
            "created_at": row[15],
            "updated_at": row[16],
        }

    def create_download_task(
        self,
        task_key,
        owner_id,
        source_chat_id,
        source_title,
        start_message_id,
        limit_count,
        dest,
        collection_id=None,
        collection_key=None,
        status="pending",
    ):
        with self._transaction():
            self.cursor.execute(
                """
                INSERT INTO download_tasks (
                    task_key, owner_id, source_chat_id, source_title,
                    start_message_id, limit_count, dest, collection_id,
                    collection_key, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_key,
                    owner_id,
                    source_chat_id,
                    source_title,
                    start_message_id,
                    limit_count,
                    dest,
                    collection_id,
                    collection_key,
                    status,
                ),
            )
            return self.cursor.lastrowid

    def update_download_task(self, task_key, **fields):
        allowed = {
            "status",
            "success_count",
            "fail_count",
            "last_message_id",
            "error_summary",
            "collection_id",
            "collection_key",
        }
        updates = [(key, value) for key, value in fields.items() if key in allowed]
        if not updates:
            return False
        set_clause = ", ".join(f"{key} = ?" for key, _ in updates)
        values = [value for _, value in updates]
        values.append(task_key)
        with self._transaction():
            self.cursor.execute(
                f"""
                UPDATE download_tasks
                SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ?
                """,
                values,
            )
            return self.cursor.rowcount > 0

    def get_download_task_by_key(self, task_key):
        self.cursor.execute(
            """
            SELECT id, task_key, owner_id, source_chat_id, source_title,
                   start_message_id, limit_count, dest, collection_id,
                   collection_key, status, success_count, fail_count,
                   last_message_id, error_summary, created_at, updated_at
            FROM download_tasks
            WHERE task_key = ?
            """,
            (task_key,),
        )
        return self._download_task_from_row(self.cursor.fetchone())

    def get_user_download_tasks(self, owner_id, limit=10):
        self.cursor.execute(
            """
            SELECT id, task_key, owner_id, source_chat_id, source_title,
                   start_message_id, limit_count, dest, collection_id,
                   collection_key, status, success_count, fail_count,
                   last_message_id, error_summary, created_at, updated_at
            FROM download_tasks
            WHERE owner_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (owner_id, limit),
        )
        return [self._download_task_from_row(row) for row in self.cursor.fetchall()]


db = Database()
db.init_collections_table()
