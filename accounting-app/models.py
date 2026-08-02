import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import g

DB_PATH = Path(__file__).parent / "accounting.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    migrate()


def migrate():
    """既存DBに後から追加したカラムを反映する（SQLiteはADD COLUMN IF NOT EXISTSが無いため個別チェック）"""
    conn = sqlite3.connect(DB_PATH)
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "pin_code" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN pin_code TEXT NOT NULL DEFAULT '0000'")
    tx_cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "created_by" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN created_by INTEGER REFERENCES users(id)")
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- users ----------

def list_users(active_only=False):
    db = get_db()
    q = "SELECT * FROM users"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY id"
    return db.execute(q).fetchall()


def add_user(name, role="member", pin_code="0000"):
    db = get_db()
    db.execute(
        "INSERT INTO users (name, role, active, pin_code) VALUES (?, ?, 1, ?)",
        (name, role, pin_code),
    )
    db.commit()


def get_user(user_id):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def find_user_by_pin(user_id, pin_code):
    db = get_db()
    return db.execute(
        "SELECT * FROM users WHERE id = ? AND pin_code = ? AND active = 1",
        (user_id, pin_code),
    ).fetchone()


def update_user(user_id, name, role):
    db = get_db()
    db.execute("UPDATE users SET name = ?, role = ? WHERE id = ?", (name, role, user_id))
    db.commit()


def set_user_pin(user_id, pin_code):
    db = get_db()
    db.execute("UPDATE users SET pin_code = ? WHERE id = ?", (pin_code, user_id))
    db.commit()


def set_user_active(user_id, active):
    db = get_db()
    db.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
    db.commit()


# ---------- categories ----------

def list_categories(type_=None):
    db = get_db()
    if type_:
        return db.execute(
            "SELECT * FROM categories WHERE type = ? ORDER BY id", (type_,)
        ).fetchall()
    return db.execute("SELECT * FROM categories ORDER BY type, id").fetchall()


def add_category(name, type_, tax_rate):
    db = get_db()
    db.execute(
        "INSERT INTO categories (name, type, tax_rate) VALUES (?, ?, ?)",
        (name, type_, tax_rate),
    )
    db.commit()


# ---------- transactions ----------

def add_transaction(type_, amount, category_id, paid_by, date, memo, receipt_path, created_by):
    db = get_db()
    db.execute(
        """INSERT INTO transactions
           (type, amount, category_id, paid_by, date, memo, receipt_path, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (type_, amount, category_id, paid_by, date, memo, receipt_path, now_iso(), created_by),
    )
    db.commit()


def get_transaction(transaction_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
    ).fetchone()


def update_transaction(transaction_id, type_, amount, category_id, paid_by, date, memo, receipt_path=None):
    db = get_db()
    if receipt_path is not None:
        db.execute(
            """UPDATE transactions
               SET type = ?, amount = ?, category_id = ?, paid_by = ?, date = ?, memo = ?, receipt_path = ?
               WHERE id = ?""",
            (type_, amount, category_id, paid_by, date, memo, receipt_path, transaction_id),
        )
    else:
        db.execute(
            """UPDATE transactions
               SET type = ?, amount = ?, category_id = ?, paid_by = ?, date = ?, memo = ?
               WHERE id = ?""",
            (type_, amount, category_id, paid_by, date, memo, transaction_id),
        )
    db.commit()


def delete_transaction(transaction_id):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    db.commit()


def list_transactions(limit=200):
    db = get_db()
    return db.execute(
        """SELECT t.*, c.name AS category_name, u.name AS paid_by_name
           FROM transactions t
           JOIN categories c ON c.id = t.category_id
           LEFT JOIN users u ON u.id = t.paid_by
           ORDER BY t.date DESC, t.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def unsettled_expenses():
    db = get_db()
    return db.execute(
        """SELECT * FROM transactions
           WHERE type = 'expense' AND is_settled = 0 AND paid_by IS NOT NULL"""
    ).fetchall()


def mark_settled(transaction_ids, settlement_id):
    db = get_db()
    db.executemany(
        "UPDATE transactions SET is_settled = 1, settlement_id = ? WHERE id = ?",
        [(settlement_id, tid) for tid in transaction_ids],
    )
    db.commit()


# ---------- settlements ----------

def create_settlement(from_user, to_user, amount, memo=""):
    db = get_db()
    cur = db.execute(
        """INSERT INTO settlements (from_user, to_user, amount, settled_at, memo)
           VALUES (?, ?, ?, ?, ?)""",
        (from_user, to_user, amount, now_iso(), memo),
    )
    db.commit()
    return cur.lastrowid


def list_settlements(limit=100):
    db = get_db()
    return db.execute(
        """SELECT s.*, fu.name AS from_name, tu.name AS to_name
           FROM settlements s
           JOIN users fu ON fu.id = s.from_user
           JOIN users tu ON tu.id = s.to_user
           ORDER BY s.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


# ---------- personal expenses（個人の割り勘、旅館の収支には計上しない） ----------

def add_personal_expense(date, payer_id, memo, items, created_by):
    """items: [(user_id, amount), ...]"""
    db = get_db()
    cur = db.execute(
        """INSERT INTO personal_expenses (date, payer_id, memo, created_by, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (date, payer_id, memo, created_by, now_iso()),
    )
    expense_id = cur.lastrowid
    db.executemany(
        "INSERT INTO personal_expense_items (personal_expense_id, user_id, amount) VALUES (?, ?, ?)",
        [(expense_id, uid, amount) for uid, amount in items],
    )
    db.commit()
    return expense_id


def list_personal_expenses(limit=100):
    db = get_db()
    expenses = db.execute(
        """SELECT pe.*, u.name AS payer_name
           FROM personal_expenses pe
           JOIN users u ON u.id = pe.payer_id
           ORDER BY pe.date DESC, pe.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    result = []
    for e in expenses:
        items = db.execute(
            """SELECT pei.*, u.name AS user_name
               FROM personal_expense_items pei
               JOIN users u ON u.id = pei.user_id
               WHERE pei.personal_expense_id = ?
               ORDER BY pei.id""",
            (e["id"],),
        ).fetchall()
        result.append({"expense": e, "lines": items, "total": sum(i["amount"] for i in items)})
    return result


def get_personal_expense(expense_id):
    db = get_db()
    expense = db.execute(
        "SELECT * FROM personal_expenses WHERE id = ?", (expense_id,)
    ).fetchone()
    if expense is None:
        return None
    items = db.execute(
        "SELECT * FROM personal_expense_items WHERE personal_expense_id = ? ORDER BY id",
        (expense_id,),
    ).fetchall()
    return {"expense": expense, "lines": items}


def update_personal_expense(expense_id, date, payer_id, memo, items):
    """items: [(user_id, amount), ...]。既存の内訳はいったん削除して入れ直す。"""
    db = get_db()
    db.execute(
        "UPDATE personal_expenses SET date = ?, payer_id = ?, memo = ? WHERE id = ?",
        (date, payer_id, memo, expense_id),
    )
    db.execute("DELETE FROM personal_expense_items WHERE personal_expense_id = ?", (expense_id,))
    db.executemany(
        "INSERT INTO personal_expense_items (personal_expense_id, user_id, amount) VALUES (?, ?, ?)",
        [(expense_id, uid, amount) for uid, amount in items],
    )
    db.commit()


def delete_personal_expense(expense_id):
    db = get_db()
    db.execute("DELETE FROM personal_expense_items WHERE personal_expense_id = ?", (expense_id,))
    db.execute("DELETE FROM personal_expenses WHERE id = ?", (expense_id,))
    db.commit()


def unsettled_personal_items():
    db = get_db()
    return db.execute(
        """SELECT pei.*, pe.payer_id AS payer_id
           FROM personal_expense_items pei
           JOIN personal_expenses pe ON pe.id = pei.personal_expense_id
           WHERE pei.is_settled = 0"""
    ).fetchall()


def mark_personal_settled(item_ids, settlement_id):
    db = get_db()
    db.executemany(
        "UPDATE personal_expense_items SET is_settled = 1, settlement_id = ? WHERE id = ?",
        [(settlement_id, iid) for iid in item_ids],
    )
    db.commit()


def create_personal_settlement(from_user, to_user, amount):
    db = get_db()
    cur = db.execute(
        """INSERT INTO personal_settlements (from_user, to_user, amount, settled_at)
           VALUES (?, ?, ?, ?)""",
        (from_user, to_user, amount, now_iso()),
    )
    db.commit()
    return cur.lastrowid


def list_personal_settlements(limit=100):
    db = get_db()
    return db.execute(
        """SELECT s.*, fu.name AS from_name, tu.name AS to_name
           FROM personal_settlements s
           JOIN users fu ON fu.id = s.from_user
           JOIN users tu ON tu.id = s.to_user
           ORDER BY s.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


# ---------- dashboard / reports ----------

def month_summary(year_month):
    """year_month: 'YYYY-MM'"""
    db = get_db()
    row = db.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) AS income,
             COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS expense
           FROM transactions
           WHERE date LIKE ?""",
        (f"{year_month}%",),
    ).fetchone()
    return {
        "income": row["income"],
        "expense": row["expense"],
        "net": row["income"] - row["expense"],
    }


def category_breakdown(year_month, type_):
    db = get_db()
    return db.execute(
        """SELECT c.name AS category, SUM(t.amount) AS total
           FROM transactions t
           JOIN categories c ON c.id = t.category_id
           WHERE t.date LIKE ? AND t.type = ?
           GROUP BY c.name
           ORDER BY total DESC""",
        (f"{year_month}%", type_),
    ).fetchall()


def monthly_trend(months=6):
    db = get_db()
    return db.execute(
        """SELECT substr(date, 1, 7) AS ym,
             COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) AS income,
             COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS expense
           FROM transactions
           GROUP BY ym
           ORDER BY ym DESC
           LIMIT ?""",
        (months,),
    ).fetchall()


# ---------- closings ----------

def is_closed(year_month):
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM closings WHERE year_month = ?", (year_month,)
    ).fetchone()
    return row is not None


def close_month(year_month, closed_by):
    db = get_db()
    db.execute(
        "INSERT INTO closings (year_month, closed_by, closed_at) VALUES (?, ?, ?)",
        (year_month, closed_by, now_iso()),
    )
    db.execute(
        "UPDATE transactions SET locked = 1 WHERE date LIKE ?", (f"{year_month}%",)
    )
    db.commit()


def list_closings():
    db = get_db()
    return db.execute(
        """SELECT cl.*, u.name AS closed_by_name
           FROM closings cl
           LEFT JOIN users u ON u.id = cl.closed_by
           ORDER BY cl.year_month DESC"""
    ).fetchall()
