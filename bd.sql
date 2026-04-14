CREATE TABLE IF NOT EXISTS clients (
        client_id SERIAL PRIMARY KEY,
        account_age_days INTEGER NOT NULL DEFAULT 0,
        total_orders INTEGER NOT NULL DEFAULT 0,
        total_returns INTEGER NOT NULL DEFAULT 0,
        global_return_rate DECIMAL(5,2) NOT NULL DEFAULT 0.00,
        avg_order_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
        address_change_frequency DECIMAL(4,2) NOT NULL DEFAULT 0.00,
        category_returns_count INTEGER NOT NULL DEFAULT 0,
        registration_city VARCHAR(100),
        client_lat DECIMAL(9,6),
        client_lng DECIMAL(9,6),
        phone_hash VARCHAR(64),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 2. Заказы
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
        product_category VARCHAR(100),
        is_electronics BOOLEAN DEFAULT FALSE,
        shipping_region VARCHAR(50),
        region_risk_score DECIMAL(3,2) DEFAULT 0.00,
        delivery_city VARCHAR(100),
        delivery_lat DECIMAL(9,6),
        delivery_lng DECIMAL(9,6),
        distance_from_registration_km DECIMAL(6,2),
        payment_card_bin VARCHAR(8),
        card_issuing_country VARCHAR(3),
        card_country_mismatch BOOLEAN DEFAULT FALSE,
        delivery_address_type VARCHAR(50),
        address_match_score DECIMAL(3,2) DEFAULT 0.00,
        is_address_match BOOLEAN DEFAULT TRUE,
        order_status VARCHAR(30) DEFAULT 'completed',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 3. Возвраты
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
        claimed_reason VARCHAR(150),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 4. Сессии
    CREATE TABLE IF NOT EXISTS client_sessions (
        session_id SERIAL PRIMARY KEY,
        client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
        ip_address VARCHAR(45),
        device_id VARCHAR(100),
        device_fingerprint TEXT,
        is_emulator BOOLEAN DEFAULT FALSE,
        user_agent TEXT,
        login_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_new_device BOOLEAN DEFAULT FALSE,
        device_first_seen_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 5. Обращения в поддержку
    CREATE TABLE IF NOT EXISTS support_tickets (
        ticket_id SERIAL PRIMARY KEY,
        client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
        order_id INTEGER REFERENCES orders(order_id) ON DELETE SET NULL,
        subject VARCHAR(200),
        message_text TEXT,
        sentiment_score DECIMAL(3,2) DEFAULT 0.00,
        has_threat BOOLEAN DEFAULT FALSE,
        has_legal_claim BOOLEAN DEFAULT FALSE,
        threat_language_detected BOOLEAN DEFAULT FALSE,
        legal_claim_threat BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 6. Отзывы
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

    -- 7. Чарджбэки
    CREATE TABLE IF NOT EXISTS chargebacks (
        chargeback_id SERIAL PRIMARY KEY,
        order_id INTEGER REFERENCES orders(order_id) ON DELETE CASCADE,
        client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
        chargeback_amount DECIMAL(10,2) NOT NULL,
        chargeback_reason VARCHAR(150),
        status VARCHAR(30) DEFAULT 'PENDING',
        chargeback_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 8. Пользователи
    CREATE TABLE IF NOT EXISTS user_accounts (
    client_id INTEGER PRIMARY KEY REFERENCES clients(client_id) ON DELETE CASCADE,
    login VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'client',
    CONSTRAINT check_role_values CHECK (role IN ('client', 'admin'))
);

    -- Views
    CREATE OR REPLACE VIEW tickets AS SELECT * FROM support_tickets;
    CREATE OR REPLACE VIEW reviews AS SELECT * FROM product_reviews;

    -- Индексы
    CREATE INDEX IF NOT EXISTS idx_user_accounts_login ON user_accounts(login);
    CREATE INDEX IF NOT EXISTS idx_orders_client_time ON orders(client_id, order_timestamp);
    CREATE INDEX IF NOT EXISTS idx_returns_client_time ON returns(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_returns_order_id ON returns(order_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_client_time ON client_sessions(client_id, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_sessions_ip_time ON client_sessions(ip_address, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_sessions_device_time ON client_sessions(device_id, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_tickets_client_time ON support_tickets(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_reviews_client_time ON product_reviews(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_chargebacks_client_time ON chargebacks(client_id, chargeback_date);