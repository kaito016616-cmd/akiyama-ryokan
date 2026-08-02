-- 秋山旅館 経理・精算アプリ DBスキーマ

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',   -- 'admin' or 'member'
    active INTEGER NOT NULL DEFAULT 1,
    pin_code TEXT NOT NULL DEFAULT '0000'  -- ログイン用の簡易PIN（4桁）
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('income','expense')),
    tax_rate REAL NOT NULL DEFAULT 0.10    -- 0.08(軽減税率) or 0.10
);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER NOT NULL REFERENCES users(id),
    to_user INTEGER NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL,
    settled_at TEXT NOT NULL,
    memo TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('income','expense')),
    amount INTEGER NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    paid_by INTEGER REFERENCES users(id),
    date TEXT NOT NULL,                    -- 'YYYY-MM-DD'
    memo TEXT,
    receipt_path TEXT,
    is_settled INTEGER NOT NULL DEFAULT 0,
    settlement_id INTEGER REFERENCES settlements(id),
    created_at TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,     -- 月次締め後は1（編集不可）
    created_by INTEGER REFERENCES users(id)  -- 入力した人（編集権限の判定に使用）
);

CREATE TABLE IF NOT EXISTS closings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month TEXT NOT NULL UNIQUE,       -- 'YYYY-MM'
    closed_by INTEGER REFERENCES users(id),
    closed_at TEXT NOT NULL
);

INSERT INTO categories (name, type, tax_rate) SELECT '宿泊売上', 'income', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '宿泊売上');
INSERT INTO categories (name, type, tax_rate) SELECT '飲食売上', 'income', 0.08
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '飲食売上');
INSERT INTO categories (name, type, tax_rate) SELECT 'その他売上', 'income', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'その他売上');
INSERT INTO categories (name, type, tax_rate) SELECT '食材費', 'expense', 0.08
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '食材費');
INSERT INTO categories (name, type, tax_rate) SELECT '消耗品', 'expense', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '消耗品');
INSERT INTO categories (name, type, tax_rate) SELECT '水道光熱費', 'expense', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '水道光熱費');
INSERT INTO categories (name, type, tax_rate) SELECT '備品', 'expense', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '備品');
INSERT INTO categories (name, type, tax_rate) SELECT '交通費', 'expense', 0.10
    WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '交通費');
