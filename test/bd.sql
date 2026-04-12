-- =============================================================================
-- FRAUDRETURN SHIELD v4.1 — БАЗОВАЯ СХЕМА (БЕЗ ОПТИМИЗАЦИЙ)
-- =============================================================================

CREATE TABLE IF NOT EXISTS clients (
    client_id SERIAL PRIMARY KEY,
    account_age_days INTEGER DEFAULT 0,
    total_orders INTEGER DEFAULT 0,
    total_returns INTEGER DEFAULT 0,
    global_return_rate DOUBLE PRECISION DEFAULT 0.0,
    avg_order_amount DOUBLE PRECISION DEFAULT 0.0,
    address_change_frequency INTEGER DEFAULT 0,
    category_returns_count TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id),
    order_amount DOUBLE PRECISION,
    items_count INTEGER DEFAULT 1,
    discount_amount DOUBLE PRECISION DEFAULT 0.0,
    payment_method VARCHAR(50),
    order_timestamp TIMESTAMP,
    amount_deviation DOUBLE PRECISION DEFAULT 0.0,
    orders_last_30d INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS returns (
    return_id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(order_id),
    client_id INTEGER REFERENCES clients(client_id),
    days_since_purchase INTEGER,
    days_since_last_return INTEGER DEFAULT 999,
    return_channel VARCHAR(50),
    has_receipt BOOLEAN DEFAULT TRUE,
    tags_removed BOOLEAN DEFAULT FALSE,
    missing_components BOOLEAN DEFAULT FALSE,
    returns_last_30d INTEGER DEFAULT 0,
    return_rate_last_30d DOUBLE PRECISION DEFAULT 0.0,
    refund_amount DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS client_sessions (
    session_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id),
    ip_address VARCHAR(45),
    device_fingerprint VARCHAR(100),
    is_emulator BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);