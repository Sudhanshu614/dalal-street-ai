-- Company Lifecycle Tracking System Schema

-- 1. MASTER_COMPANIES (Central Registry)
CREATE TABLE IF NOT EXISTS master_companies (
    company_id INTEGER PRIMARY KEY AUTOINCREMENT,
    current_symbol VARCHAR(20) NOT NULL,
    current_name VARCHAR(200) NOT NULL,
    original_symbol VARCHAR(20),          -- First known symbol
    original_name VARCHAR(200),           -- First known name
    isin_number VARCHAR(12),
    date_of_first_listing DATE,
    current_status VARCHAR(20),           -- LISTED, DELISTED, SUSPENDED, MERGED
    delisting_date DATE,
    delisting_reason TEXT,
    face_value DECIMAL(10,2),
    series VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_master_symbol ON master_companies(current_symbol);
CREATE INDEX IF NOT EXISTS idx_master_isin ON master_companies(isin_number);
CREATE INDEX IF NOT EXISTS idx_master_status ON master_companies(current_status);

-- 2. NAME_CHANGE_HISTORY (Temporal Name Tracking)
CREATE TABLE IF NOT EXISTS name_change_history (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    symbol_at_time VARCHAR(20) NOT NULL,
    previous_name VARCHAR(200) NOT NULL,
    new_name VARCHAR(200) NOT NULL,
    change_date DATE NOT NULL,
    source VARCHAR(50),                   -- 'namechange.csv', 'CF-CA', etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_name_company ON name_change_history(company_id);
CREATE INDEX IF NOT EXISTS idx_name_date ON name_change_history(change_date);

-- 3. SYMBOL_CHANGE_HISTORY (Temporal Symbol Tracking)
CREATE TABLE IF NOT EXISTS symbol_change_history (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    previous_symbol VARCHAR(20) NOT NULL,
    new_symbol VARCHAR(20) NOT NULL,
    name_at_time VARCHAR(200),
    change_date DATE NOT NULL,
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_symbol_company ON symbol_change_history(company_id);
CREATE INDEX IF NOT EXISTS idx_symbol_old ON symbol_change_history(previous_symbol);
CREATE INDEX IF NOT EXISTS idx_symbol_new ON symbol_change_history(new_symbol);

-- 4. CORPORATE_ACTIONS (All Corporate Events)
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    symbol_at_time VARCHAR(20) NOT NULL,
    purpose TEXT NOT NULL,
    action_type VARCHAR(50),              -- BONUS, DIVIDEND, SPLIT, etc.
    ex_date DATE,
    record_date DATE,
    book_closure_start DATE,
    book_closure_end DATE,
    face_value DECIMAL(10,2),
    details JSON,                         -- Additional structured data
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_ca_company ON corporate_actions(company_id);
CREATE INDEX IF NOT EXISTS idx_ca_type ON corporate_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_ca_ex_date ON corporate_actions(ex_date);

-- 5. IPO_LISTINGS
CREATE TABLE IF NOT EXISTS ipo_listings (
    ipo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,                   -- Can be NULL if not yet linked to master
    company_name_at_ipo VARCHAR(200) NOT NULL,
    symbol_at_ipo VARCHAR(20) NOT NULL,
    security_type VARCHAR(10),
    issue_start_date DATE,
    issue_end_date DATE,
    issue_price DECIMAL(10,2),
    price_range VARCHAR(50),
    date_of_listing DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_ipo_company ON ipo_listings(company_id);
CREATE INDEX IF NOT EXISTS idx_ipo_symbol ON ipo_listings(symbol_at_ipo);
CREATE INDEX IF NOT EXISTS idx_ipo_date ON ipo_listings(date_of_listing);

-- 6. MERGER_DEMERGER_EVENTS
CREATE TABLE IF NOT EXISTS merger_demerger_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type VARCHAR(20) NOT NULL,      -- MERGER, DEMERGER, AMALGAMATION
    event_date DATE NOT NULL,
    company_id_acquiring INTEGER,         -- For mergers
    company_id_target INTEGER,            -- For mergers
    description TEXT,
    resulting_companies JSON,             -- Array of company_ids for demergers
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id_acquiring) REFERENCES master_companies(company_id),
    FOREIGN KEY (company_id_target) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_md_type ON merger_demerger_events(event_type);
CREATE INDEX IF NOT EXISTS idx_md_date ON merger_demerger_events(event_date);

-- 7. DELISTING_EVENTS
CREATE TABLE IF NOT EXISTS delisting_events (
    delisting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    symbol_at_delisting VARCHAR(20) NOT NULL,
    name_at_delisting VARCHAR(200) NOT NULL,
    delisting_date DATE,
    delisting_reason VARCHAR(200),
    last_traded_price DECIMAL(10,2),
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES master_companies(company_id)
);

CREATE INDEX IF NOT EXISTS idx_delist_company ON delisting_events(company_id);
CREATE INDEX IF NOT EXISTS idx_delist_date ON delisting_events(delisting_date);
