import sys
import io

# Принудительно устанавливаем UTF-8 для stdout и stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import sys
sys.stdout.reconfigure(errors='replace')

import psycopg2
from psycopg2.extras import execute_batch
from faker import Faker
import hashlib
import random
import math
from datetime import datetime, timedelta

# ==========================================
# КОНФИГУРАЦИЯ БД
# ==========================================
Conn_params = {
    'host': 'localhost',  # или IP-адрес сервера
    'port': 5432,  # стандартный порт PostgreSQL
    'database': 'fraud_return_db',  # имя базы данных
    'user': 'postgres',  # имя пользователя
    'password': 'OmegaBloody13'  # пароль
}

# Инициализация Faker
fake = Faker('ru_RU')


# Для консистентности можно зафиксировать seed
# fake.seed_instance(42)

def get_connection():
    return psycopg2.connect(**Conn_params)


def create_tables(cur):
    """Создает таблицы согласно предоставленной схеме."""
    schema_sql = """
    -- 1. Клиенты
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

    -- Views
    CREATE OR REPLACE VIEW tickets AS SELECT * FROM support_tickets;
    CREATE OR REPLACE VIEW reviews AS SELECT * FROM product_reviews;

    -- Индексы
    CREATE INDEX IF NOT EXISTS idx_orders_client_time ON orders(client_id, order_timestamp);
    CREATE INDEX IF NOT EXISTS idx_returns_client_time ON returns(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_returns_order_id ON returns(order_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_client_time ON client_sessions(client_id, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_sessions_ip_time ON client_sessions(ip_address, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_sessions_device_time ON client_sessions(device_id, login_timestamp);
    CREATE INDEX IF NOT EXISTS idx_tickets_client_time ON support_tickets(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_reviews_client_time ON product_reviews(client_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_chargebacks_client_time ON chargebacks(client_id, chargeback_date);
    """
    cur.execute(schema_sql)


def haversine_distance(lat1, lon1, lat2, lon2):
    """Расчет расстояния между двумя точками в км."""
    R = 6371  # Радиус Земли в км
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(
        d_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def generate_clients(cur, count=1000):
    """Генерирует клиентов и возвращает список их ID и данных для связи."""
    print(f"Генерация {count} клиентов...")
    clients_data = []
    now = datetime.now()

    for _ in range(count):
        phone = fake.phone_number()
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()

        # Координаты (примерно центр России + разброс)
        lat = random.uniform(50.0, 60.0)
        lng = random.uniform(30.0, 40.0)

        account_age = random.randint(1, 1000)
        total_orders = random.randint(0, 50)
        total_returns = random.randint(0, max(1, total_orders // 3))

        clients_data.append((
            account_age,
            total_orders,
            total_returns,
            round(random.uniform(0.0, 0.3), 2),  # global_return_rate
            round(random.uniform(1000, 50000), 2),  # avg_order_amount
            round(random.uniform(0.0, 5.0), 2),  # address_change_frequency
            random.randint(0, 5),  # category_returns_count
            fake.city(),
            lat,
            lng,
            phone_hash,
            now - timedelta(days=account_age)  # created_at
        ))

    insert_query = """
        INSERT INTO clients (
            account_age_days, total_orders, total_returns, global_return_rate, 
            avg_order_amount, address_change_frequency, category_returns_count,
            registration_city, client_lat, client_lng, phone_hash, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING client_id, client_lat, client_lng, registration_city
    """

    # Execute batch doesn't easily support RETURNING for all rows in one go efficiently without complex handling
    # So we insert then fetch or use a loop for small batches. For 1000, loop is acceptable or bulk insert + select.
    # Let's do bulk insert first.

    simple_insert = """
        INSERT INTO clients (
            account_age_days, total_orders, total_returns, global_return_rate, 
            avg_order_amount, address_change_frequency, category_returns_count,
            registration_city, client_lat, client_lng, phone_hash, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    execute_batch(cur, simple_insert, clients_data)

    # Fetch generated IDs and coords to link with orders
    cur.execute(
        "SELECT client_id, client_lat, client_lng, registration_city FROM clients ORDER BY client_id DESC LIMIT %s",
        (count,))
    created_clients = cur.fetchall()
    return created_clients


def generate_orders(cur, clients_list):
    """Генерирует заказы для клиентов."""
    print("Генерация заказов...")
    orders_data = []
    order_ids_map = {}  # client_id -> [order_ids]

    categories = ['Electronics', 'Clothing', 'Home', 'Books', 'Toys']
    regions = ['Moscow', 'SPB', 'Siberia', 'South', 'FarEast']
    address_types = ['home', 'office', 'pickup', 'post_office']

    for client_id, c_lat, c_lng, reg_city in clients_list:
        # Генерируем от 1 до 5 заказов на клиента для разнообразия
        num_orders = random.randint(1, 5)
        client_order_ids = []

        for _ in range(num_orders):
            order_ts = fake.date_time_between(start_date='-1y', end_date='now')
            amount = round(random.uniform(500, 100000), 2)
            cat = random.choice(categories)
            is_elec = cat == 'Electronics'

            # Delivery coords slightly offset from registration

            c_lat = float(c_lat) if c_lat is not None else 0.0
            c_lng = float(c_lng) if c_lng is not None else 0.0

            d_lat = c_lat + random.uniform(-0.5, 0.5)
            d_lng = c_lng + random.uniform(-0.5, 0.5)
            dist = haversine_distance(c_lat, c_lng, d_lat, d_lng)

            card_country = random.choice(['RU', 'US', 'CN', 'KZ'])
            issuing_country = random.choice(['RU', 'RU', 'RU', 'US'])  # Bias towards RU
            mismatch = card_country != issuing_country

            orders_data.append((
                client_id,
                amount,
                random.randint(1, 10),  # items_count
                round(random.uniform(0, amount * 0.2), 2),  # discount
                random.choice(['card', 'sbp', 'cash']),  # payment
                order_ts,
                round(random.uniform(-10, 10), 2),  # deviation
                random.randint(0, 5),  # orders_last_30d
                cat,
                is_elec,
                random.choice(regions),
                round(random.uniform(0, 9.99), 2),  # region_risk
                fake.city(),  # delivery_city
                d_lat,
                d_lng,
                round(dist, 2),
                str(random.randint(100000, 999999)),  # bin
                issuing_country,
                mismatch,
                random.choice(address_types),
                round(random.uniform(0.5, 1.0), 2),  # address_match_score
                True if random.random() > 0.1 else False,  # is_address_match
                order_ts  # created_at same as order_ts roughly
            ))

    insert_query = """
        INSERT INTO orders (
            client_id, order_amount, items_count, discount_amount, payment_method,
            order_timestamp, amount_deviation, orders_last_30d, product_category,
            is_electronics, shipping_region, region_risk_score, delivery_city,
            delivery_lat, delivery_lng, distance_from_registration_km,
            payment_card_bin, card_issuing_country, card_country_mismatch,
            delivery_address_type, address_match_score, is_address_match, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING order_id, client_id
    """

    # To get IDs back efficiently, we might need to insert and then query,
    # but since we need order_id for returns, let's insert and fetch last N.
    # However, concurrent inserts make "last N" risky.
    # Better approach for script: Insert all, then select all orders joined with clients to map them?
    # Or simply insert and assume serial IDs are contiguous (risky in prod, ok for script if single user).

    execute_batch(cur, insert_query.replace("RETURNING order_id, client_id", ""), orders_data)

    # Get all orders to map them
    cur.execute("SELECT order_id, client_id FROM orders")
    all_orders = cur.fetchall()

    # Group by client
    for oid, cid in all_orders:
        if cid not in order_ids_map:
            order_ids_map[cid] = []
        order_ids_map[cid].append(oid)

    return order_ids_map


def generate_related_data(cur, clients_list, order_map):
    """Генерирует сессии, возвраты, тикеты, отзывы, чарджбэки."""
    print("Генерация связанных данных (возвраты, сессии, тикеты)...")

    sessions_data = []
    returns_data = []
    tickets_data = []
    reviews_data = []
    chargebacks_data = []

    for client_id, _, _, _ in clients_list:
        # 1. Sessions
        num_sessions = random.randint(1, 10)
        for _ in range(num_sessions):
            login_ts = fake.date_time_between(start_date='-1y', end_date='now')
            sessions_data.append((
                client_id,
                fake.ipv4(),
                hashlib.md5(fake.user_agent().encode()).hexdigest(),  # device_id
                fake.user_agent(),  # fingerprint proxy
                random.random() < 0.05,  # is_emulator
                fake.user_agent(),
                login_ts,
                random.random() < 0.2,  # is_new_device
                login_ts - timedelta(hours=random.randint(1, 100)),  # first_seen
                login_ts
            ))

        # 2. Returns, Tickets, Reviews, Chargebacks based on orders
        if client_id in order_map:
            for order_id in order_map[client_id]:
                # Return?
                if random.random() < 0.2:  # 20% chance of return
                    days_since = random.randint(1, 30)
                    ret_date = datetime.now() - timedelta(days=random.randint(0, 10))
                    returns_data.append((
                        order_id,
                        client_id,
                        random.randint(0, 3),  # returns_last_30d
                        round(random.uniform(0, 0.5), 2),
                        random.randint(0, 60),  # days_since_last_return
                        days_since,
                        random.choice(['courier', 'post', 'dropoff']),
                        random.random() > 0.1,  # has_receipt
                        random.random() < 0.1,  # tags_removed
                        random.random() < 0.05,  # missing_components
                        random.choice(['Defective', 'Wrong Size', 'Not as described', 'Changed mind']),
                        ret_date
                    ))

                # Ticket?
                if random.random() < 0.1:
                    has_threat = random.random() < 0.1
                    tickets_data.append((
                        client_id,
                        order_id,
                        fake.sentence(nb_words=6),
                        fake.text(max_nb_chars=200),
                        round(random.uniform(-1.0, 1.0), 2),  # sentiment
                        has_threat,
                        random.random() < 0.05,  # legal claim
                        has_threat,  # threat_language
                        random.random() < 0.05,  # legal_claim_threat
                        fake.date_time_between(start_date='-1y', end_date='now')
                    ))

                # Review?
                if random.random() < 0.3:
                    rating = random.randint(1, 5)
                    reviews_data.append((
                        client_id,
                        order_id,
                        rating,
                        fake.text(max_nb_chars=150),
                        rating <= 2,  # is_negative
                        round(random.uniform(0, 1), 4),  # similarity
                        fake.date_time_between(start_date='-1y', end_date='now')
                    ))

                # Chargeback?
                if random.random() < 0.02:
                    chargebacks_data.append((
                        order_id,
                        client_id,
                        round(random.uniform(1000, 50000), 2),
                        random.choice(['Fraud', 'Not Received', 'Duplicate']),
                        random.choice(['PENDING', 'RESOLVED', 'DECLINED']),
                        fake.date_time_between(start_date='-6m', end_date='now'),
                        fake.date_time_between(start_date='-6m', end_date='now')
                    ))

    # Batch Inserts
    if sessions_data:
        execute_batch(cur, """
            INSERT INTO client_sessions (client_id, ip_address, device_id, device_fingerprint, is_emulator, user_agent, login_timestamp, is_new_device, device_first_seen_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, sessions_data)

    if returns_data:
        execute_batch(cur, """
            INSERT INTO returns (order_id, client_id, returns_last_30d, return_rate_last_30d, days_since_last_return, days_since_purchase, return_channel, has_receipt, tags_removed, missing_components, claimed_reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, returns_data)

    if tickets_data:
        execute_batch(cur, """
            INSERT INTO support_tickets (client_id, order_id, subject, message_text, sentiment_score, has_threat, has_legal_claim, threat_language_detected, legal_claim_threat, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, tickets_data)

    if reviews_data:
        execute_batch(cur, """
            INSERT INTO product_reviews (client_id, order_id, rating, review_text, is_negative, similarity_score, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, reviews_data)

    if chargebacks_data:
        execute_batch(cur, """
            INSERT INTO chargebacks (order_id, client_id, chargeback_amount, chargeback_reason, status, chargeback_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, chargebacks_data)


def main():
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        print("Подключение к БД успешно.")

        # 1. Create Tables
        print("Создание таблиц...")
        create_tables(cur)
        conn.commit()

        # Clear existing data if re-running (optional, careful with CASCADE)
        # cur.execute("TRUNCATE TABLE clients CASCADE;")
        # conn.commit()

        # 2. Generate Clients
        clients_list = generate_clients(cur, count=1000)
        conn.commit()

        # 3. Generate Orders
        order_map = generate_orders(cur, clients_list)
        conn.commit()

        # 4. Generate Related Data
        generate_related_data(cur, clients_list, order_map)
        conn.commit()

        print("Генерация данных завершена успешно!")

        # Verification counts
        cur.execute("SELECT count(*) FROM clients")
        print(f"Клиентов: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM orders")
        print(f"Заказов: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM returns")
        print(f"Возвратов: {cur.fetchone()[0]}")

        cur.close()

    except Exception as e:
        print(f"Ошибка: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()