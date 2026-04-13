-- ============================================
-- FRAUDRETURN SHIELD — ПОЛНАЯ СХЕМА БД
-- ============================================

-- 1. Таблица клиентов
CREATE TABLE IF NOT EXISTS clients (
    client_id SERIAL PRIMARY KEY,
    account_age_days INTEGER NOT NULL DEFAULT 0,
    total_orders INTEGER NOT NULL DEFAULT 0,
    total_returns INTEGER NOT NULL DEFAULT 0,
    global_return_rate DECIMAL(5,2) NOT NULL DEFAULT 0.00,
    avg_order_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    address_change_frequency DECIMAL(4,2) NOT NULL DEFAULT 0.00,
    category_returns_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Таблица заказов
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    order_amount DECIMAL(10,2) NOT NULL,
    items_count INTEGER NOT NULL DEFAULT 1,
    discount_amount DECIMAL(8,2) DEFAULT 0.00,
    payment_method VARCHAR(50),
    order_timestamp TIMESTAMP,
    amount_deviation DECIMAL(6,2) DEFAULT 0.00,
    orders_last_30d INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Таблица возвратов
CREATE TABLE IF NOT EXISTS returns (
    return_id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(order_id) ON DELETE CASCADE,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    returns_last_30d INTEGER DEFAULT 0,
    return_rate_last_30d DECIMAL(5,2) DEFAULT 0.00,
    days_since_last_return INTEGER DEFAULT 0,
    days_since_purchase INTEGER NOT NULL DEFAULT 0,
    return_channel VARCHAR(50),
    has_receipt BOOLEAN DEFAULT TRUE,
    tags_removed BOOLEAN DEFAULT FALSE,
    missing_components BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Таблица сессий/IP (дополнительная)
CREATE TABLE IF NOT EXISTS client_sessions (
    session_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    ip_address VARCHAR(45),
    device_id VARCHAR(100),
    device_fingerprint TEXT,
    is_emulator BOOLEAN DEFAULT FALSE,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Таблица обращений в поддержку (дополнительная)
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    order_id INTEGER REFERENCES orders(order_id) ON DELETE SET NULL,
    subject VARCHAR(200),
    message_text TEXT,
    sentiment_score DECIMAL(3,2) DEFAULT 0.00,
    has_threat BOOLEAN DEFAULT FALSE,
    has_legal_claim BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Таблица отзывов (дополнительная)
CREATE TABLE IF NOT EXISTS product_reviews (
    review_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    order_id INTEGER REFERENCES orders(order_id) ON DELETE SET NULL,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    review_text TEXT,
    is_negative BOOLEAN DEFAULT FALSE,
    similarity_score DECIMAL(5,4) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- ИНДЕКСЫ ДЛЯ УСКОРЕНИЯ ЗАПРОСОВ
-- ============================================

CREATE INDEX IF NOT EXISTS idx_orders_client_id ON orders(client_id);
CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(order_timestamp);
CREATE INDEX IF NOT EXISTS idx_returns_order_id ON returns(order_id);
CREATE INDEX IF NOT EXISTS idx_returns_client_id ON returns(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_client_id ON client_sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_ip ON client_sessions(ip_address);
CREATE INDEX IF NOT EXISTS idx_tickets_client_id ON support_tickets(client_id);
CREATE INDEX IF NOT EXISTS idx_reviews_client_id ON product_reviews(client_id);
CREATE INDEX IF NOT EXISTS idx_reviews_order_id ON product_reviews(order_id);
