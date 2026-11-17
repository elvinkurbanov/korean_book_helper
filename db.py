"""
Database layer for the korean Book bot.

Everything is stored in ONE file (korean_books.db) using SQLite,
which is built into Python, so there is nothing extra to install.

Tables:
  books            - the catalog (editable by admins)
  customers        - answers each user gave (name, age, country, level, aim)
  recommendations  - which books were suggested to which customer, and when
  requests         - "I want this book" clicks (status: 'sent' if files were delivered, else 'new')
  book_files       - PDF / audio files attached to a book (stored as Telegram file IDs)
"""

import csv
import os
import sqlite3
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "korean_books.db")

# Allowed values. The bot's buttons map onto these.
LEVELS = ["beginner", "intermediate", "advanced", "all"]
AIMS = ["topik", "conversation", "work", "university", "culture"]

BOOK_COLUMNS = ["id", "title", "author", "level", "aims", "price", "in_stock", "description"]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us use row["title"] instead of row[1]
    return conn


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def init_db(seed_csv: str = "books.csv") -> None:
    """Create tables if they don't exist. Load books.csv the first time only."""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                author      TEXT,
                level       TEXT NOT NULL,          -- beginner / intermediate / advanced / all
                aims        TEXT NOT NULL,          -- comma list, e.g. "topik,conversation" or "all"
                price       TEXT,
                in_stock    INTEGER NOT NULL DEFAULT 1,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS customers (
                telegram_id INTEGER PRIMARY KEY,
                username    TEXT,
                name        TEXT,
                age         INTEGER,
                country     TEXT,
                level       TEXT,
                aim         TEXT,
                created_at  TEXT,
                updated_at  TEXT
            );

            -- book_title is copied in, so history stays readable
            -- even if the book is later edited or deleted.
            CREATE TABLE IF NOT EXISTS recommendations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                book_id     INTEGER,
                book_title  TEXT,
                created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                book_id     INTEGER,
                book_title  TEXT,
                status      TEXT DEFAULT 'new',
                created_at  TEXT
            );

            -- We don't store the files themselves. When an admin uploads a file,
            -- Telegram gives it a file_id; sending that id re-sends the same file
            -- instantly, with no size limit problems.
            CREATE TABLE IF NOT EXISTS book_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     INTEGER NOT NULL,
                kind        TEXT NOT NULL,          -- 'pdf' or 'audio'
                send_as     TEXT NOT NULL,          -- 'document' or 'audio'
                file_id     TEXT NOT NULL,
                file_name   TEXT,
                added_at    TEXT
            );
            """
        )
        empty = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0

    if empty and os.path.exists(seed_csv):
        with open(seed_csv, encoding="utf-8-sig") as f:
            import_books_csv(f.read())


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------

def save_customer(telegram_id, username, name, age, country, level, aim) -> None:
    """Insert a new customer, or update their answers if they come back."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO customers (telegram_id, username, name, age, country, level, aim, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username, name=excluded.name, age=excluded.age,
                country=excluded.country, level=excluded.level, aim=excluded.aim,
                updated_at=excluded.updated_at
            """,
            (telegram_id, username, name, age, country, level, aim, now(), now()),
        )


def get_customer(telegram_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM customers WHERE telegram_id=?", (telegram_id,)).fetchone()


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------

def recommend_books(level: str, aim: str, limit: int = 5):
    """
    Pick in-stock books for this level. Books that also match the aim come first.
    A book with level 'all' or aims 'all' fits everyone.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM books WHERE in_stock=1 AND (level=? OR level='all')",
            (level,),
        ).fetchall()

    def aim_matches(book) -> bool:
        tags = [t.strip() for t in (book["aims"] or "").split(",")]
        return aim in tags or "all" in tags

    # Sort: aim matches first (True > False), then a level-specific book before an 'all' book
    rows = sorted(rows, key=lambda b: (not aim_matches(b), b["level"] == "all", b["id"]))
    return rows[:limit]


def save_recommendations(telegram_id, books) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO recommendations (telegram_id, book_id, book_title, created_at) VALUES (?, ?, ?, ?)",
            [(telegram_id, b["id"], b["title"], now()) for b in books],
        )


def save_request(telegram_id, book, status: str = "new") -> None:
    """Customer clicked 'I want this book'. status is 'sent' if we delivered files."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO requests (telegram_id, book_id, book_title, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (telegram_id, book["id"], book["title"], status, now()),
        )


# --------------------------------------------------------------------------
# Book files (PDF / audio)
# --------------------------------------------------------------------------

def add_file(book_id, kind, send_as, file_id, file_name) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO book_files (book_id, kind, send_as, file_id, file_name, added_at) VALUES (?, ?, ?, ?, ?, ?)",
            (book_id, kind, send_as, file_id, file_name, now()),
        )


def get_files(book_id):
    """PDFs first, then audio, in upload order."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM book_files WHERE book_id=? ORDER BY kind='audio', id", (book_id,)
        ).fetchall()


def file_kinds(book_id) -> set:
    """Which kinds of files a book has, e.g. {'pdf', 'audio'}."""
    return {f["kind"] for f in get_files(book_id)}


def delete_files(book_id) -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM book_files WHERE book_id=?", (book_id,)).rowcount


# --------------------------------------------------------------------------
# Books (admin editing)
# --------------------------------------------------------------------------

def get_book(book_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()


def list_books():
    with connect() as conn:
        return conn.execute("SELECT * FROM books ORDER BY id").fetchall()


def validate_book(level: str, aims: str) -> str | None:
    """Return an error message, or None if the values are OK."""
    if level not in LEVELS:
        return f"Level must be one of: {', '.join(LEVELS)}"
    tags = [t.strip() for t in aims.split(",") if t.strip()]
    if not tags:
        return "Aims cannot be empty (use 'all' to match every aim)"
    bad = [t for t in tags if t not in AIMS + ["all"]]
    if bad:
        return f"Unknown aim(s): {', '.join(bad)}. Allowed: {', '.join(AIMS)}, all"
    return None


def add_book(title, author, level, aims, price="", description="") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, level, aims, price, in_stock, description) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (title, author, level, aims, price, description),
        )
        return cur.lastrowid


def delete_book(book_id) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM book_files WHERE book_id=?", (book_id,))
        return conn.execute("DELETE FROM books WHERE id=?", (book_id,)).rowcount > 0


def set_stock(book_id, in_stock: bool) -> bool:
    with connect() as conn:
        return conn.execute("UPDATE books SET in_stock=? WHERE id=?", (int(in_stock), book_id)).rowcount > 0


def import_books_csv(text: str) -> int:
    """
    Replace the whole catalog with the rows of a CSV file.
    Rows that keep their id keep it; rows with an empty id get a new one.
    Raises ValueError (and changes nothing) if any row is invalid.
    """
    reader = csv.DictReader(text.splitlines())
    missing = {"title", "level", "aims"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"CSV is missing column(s): {', '.join(sorted(missing))}")

    rows = []
    for line_no, row in enumerate(reader, start=2):  # line 1 is the header
        title = (row.get("title") or "").strip()
        if not title:
            continue  # skip blank lines
        level = (row.get("level") or "").strip().lower()
        aims = (row.get("aims") or "").strip().lower().replace(" ", "")
        error = validate_book(level, aims)
        if error:
            raise ValueError(f"Line {line_no} ({title}): {error}")
        stock_raw = (row.get("in_stock") or "1").strip().lower()
        rows.append(
            (
                int(row["id"]) if (row.get("id") or "").strip() else None,
                title,
                (row.get("author") or "").strip(),
                level,
                aims,
                (row.get("price") or "").strip(),
                0 if stock_raw in ("0", "no", "false") else 1,
                (row.get("description") or "").strip(),
            )
        )

    with connect() as conn:  # one transaction: all rows or nothing
        conn.execute("DELETE FROM books")
        conn.executemany(
            "INSERT INTO books (id, title, author, level, aims, price, in_stock, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        # Remove files of books that are no longer in the catalog
        conn.execute("DELETE FROM book_files WHERE book_id NOT IN (SELECT id FROM books)")
    return len(rows)


# --------------------------------------------------------------------------
# Export (CSV files the admin can open in Excel)
# --------------------------------------------------------------------------

EXPORT_QUERIES = {
    "books": "SELECT " + ", ".join(BOOK_COLUMNS) + " FROM books ORDER BY id",
    "customers": "SELECT * FROM customers ORDER BY created_at",
    "recommendations": """
        SELECT r.created_at, r.telegram_id, c.name, c.username, r.book_id, r.book_title
        FROM recommendations r LEFT JOIN customers c ON c.telegram_id = r.telegram_id
        ORDER BY r.created_at""",
    # Every delivered copy, with who got it (for licence records)
    "deliveries": """
        SELECT q.created_at AS sent_at, q.book_id, q.book_title, q.telegram_id,
               c.name, c.username, c.country, c.age
        FROM requests q LEFT JOIN customers c ON c.telegram_id = q.telegram_id
        WHERE q.status = 'sent'
        ORDER BY q.created_at""",
    "requests": """
        SELECT q.created_at, q.status, q.telegram_id, c.name, c.username, q.book_id, q.book_title
        FROM requests q LEFT JOIN customers c ON c.telegram_id = q.telegram_id
        ORDER BY q.created_at""",
}


def export_csv(table: str, folder: str = "exports") -> str:
    """Write a table to a CSV file and return its path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{table}.csv")
    with connect() as conn:
        cur = conn.execute(EXPORT_QUERIES[table])
        headers = [d[0] for d in cur.description]
        rows = cur.fetchall()
    # utf-8-sig adds a BOM so Excel shows Korean text correctly
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def licence_report(month: str | None = None):
    """
    Copies sent per book. month is 'YYYY-MM' (e.g. '2026-11') or None for all time.
    'copies' counts every delivery; 'customers' counts different people.
    """
    query = """
        SELECT book_id, book_title,
               COUNT(*) AS copies,
               COUNT(DISTINCT telegram_id) AS customers,
               MIN(created_at) AS first_sent,
               MAX(created_at) AS last_sent
        FROM requests
        WHERE status = 'sent' AND created_at LIKE ?
        GROUP BY book_id, book_title
        ORDER BY copies DESC"""
    with connect() as conn:
        return conn.execute(query, ((month or "") + "%",)).fetchall()


def deliveries(month: str | None = None):
    """Every delivered copy (optionally for one month 'YYYY-MM'), with customer details."""
    query = EXPORT_QUERIES["deliveries"].replace(
        "WHERE q.status = 'sent'", "WHERE q.status = 'sent' AND q.created_at LIKE ?"
    )
    with connect() as conn:
        return conn.execute(query, ((month or "") + "%",)).fetchall()


def export_rows(rows, name: str, folder: str = "exports") -> str:
    """Write any list of rows to a CSV file (used for the licence report)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows(rows)
    return path


def stats() -> dict:
    with connect() as conn:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("books", "book_files", "customers", "recommendations", "requests")
        }
