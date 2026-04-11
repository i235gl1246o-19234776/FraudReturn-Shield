#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
ADD_FRAUD_USERS.py — Генератор тестовых данных для Fraud Detection System
27 паттернов мошенничества + легитимные пользователи
Адаптировано под PostgreSQL с автоматическим созданием схемы БД
=============================================================================
"""
import psycopg2
import psycopg2.extras
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Tuple, Union
import random
import string
import hashlib
import json
import sys

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('fraud_generator.log', encoding='utf-8', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация БД
DB_CONFIG = {
    "dbname": "fraud_db",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5432
}

# =============================================================================
# SQL СХЕМА (PostgreSQL)
# =============================================================================
PG_SCHEMA_SQL = """
-- 1. Системные пользователи
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'operator' CHECK (role IN ('admin', 'operator', 'viewer')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 2. Транзакции
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    transaction_id TEXT UNIQUE NOT NULL,
    user_id TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    currency TEXT DEFAULT 'RUB',
    merchant_id TEXT,
    category TEXT,
    transaction_time TIMESTAMP WITH TIME ZONE NOT NULL,
    ip_address TEXT,
    device_fingerprint TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined', 'chargeback')),
    risk_score DOUBLE PRECISION CHECK (risk_score BETWEEN 0 AND 1),
    model_version TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Логи ML-проверок
CREATE TABLE IF NOT EXISTS fraud_checks (
    id SERIAL PRIMARY KEY,
    transaction_id TEXT REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    check_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    input_features JSONB NOT NULL,
    prediction_result JSONB NOT NULL,
    decision TEXT CHECK (decision IN ('ALLOW', 'BLOCK', 'REVIEW')),
    operator_comment TEXT,
    operator_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Чёрный список
CREATE TABLE IF NOT EXISTS fraud_users (
    id SERIAL PRIMARY KEY,
    user_identifier TEXT NOT NULL,
    identifier_type TEXT NOT NULL CHECK (identifier_type IN ('user_id', 'email', 'phone', 'device', 'ip')),
    reason TEXT,
    risk_level TEXT DEFAULT 'HIGH' CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    source TEXT DEFAULT 'manual' CHECK (source IN ('manual', 'auto_ml', 'api')),
    added_by INTEGER REFERENCES users(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_fraud_user UNIQUE (user_identifier, identifier_type)
);

-- 5. Клиенты
CREATE TABLE IF NOT EXISTS clients (
    client_id SERIAL PRIMARY KEY,
    account_age_days INTEGER DEFAULT 0 CHECK (account_age_days >= 0),
    total_orders INTEGER DEFAULT 0 CHECK (total_orders >= 0),
    total_returns INTEGER DEFAULT 0 CHECK (total_returns >= 0),
    global_return_rate DOUBLE PRECISION DEFAULT 0.0 CHECK (global_return_rate BETWEEN 0 AND 1),
    avg_order_amount DOUBLE PRECISION DEFAULT 0.0,
    risk_flags JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. Возвраты
CREATE TABLE IF NOT EXISTS returns (
    return_id SERIAL PRIMARY KEY,
    transaction_id TEXT REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    days_since_purchase INTEGER NOT NULL CHECK (days_since_purchase >= 0),
    return_channel TEXT CHECK (return_channel IN ('online', 'pickup_point', 'courier', 'store')),
    has_receipt BOOLEAN DEFAULT TRUE,
    tags_removed BOOLEAN DEFAULT FALSE,
    missing_components BOOLEAN DEFAULT FALSE,
    condition_report TEXT,
    refund_amount DOUBLE PRECISION,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. Идентификаторы клиентов
CREATE TABLE IF NOT EXISTS client_identifiers (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL CHECK (identifier_type IN ('user_id', 'email', 'phone', 'device_id', 'ip')),
    identifier_value TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_client_ident UNIQUE (identifier_type, identifier_value)
);

-- 8. Сессии
CREATE TABLE IF NOT EXISTS client_sessions (
    session_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE CASCADE,
    ip_address TEXT,
    device_id TEXT,
    device_fingerprint TEXT,
    is_emulator BOOLEAN DEFAULT FALSE,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 9. Тикеты поддержки
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE SET NULL,
    order_id TEXT REFERENCES transactions(transaction_id) ON DELETE SET NULL,
    subject TEXT,
    message_text TEXT,
    sentiment_score DOUBLE PRECISION CHECK (sentiment_score BETWEEN -1 AND 1),
    has_threat BOOLEAN DEFAULT FALSE,
    has_legal_claim BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 10. Отзывы
CREATE TABLE IF NOT EXISTS product_reviews (
    review_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(client_id) ON DELETE SET NULL,
    order_id TEXT REFERENCES transactions(transaction_id) ON DELETE SET NULL,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    review_text TEXT,
    is_negative BOOLEAN,
    similarity_score DOUBLE PRECISION CHECK (similarity_score BETWEEN 0 AND 1),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Индексы (PostgreSQL compatible)
CREATE INDEX IF NOT EXISTS idx_txn_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_txn_time ON transactions(transaction_time DESC);
CREATE INDEX IF NOT EXISTS idx_txn_risk ON transactions(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_fraud_users_active ON fraud_users(user_identifier) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_returns_txn ON returns(transaction_id);
CREATE INDEX IF NOT EXISTS idx_returns_client ON returns(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_client ON client_sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_tickets_client ON support_tickets(client_id);
CREATE INDEX IF NOT EXISTS idx_reviews_client ON product_reviews(client_id);
CREATE INDEX IF NOT EXISTS idx_reviews_negative ON product_reviews(is_negative) WHERE is_negative = TRUE;
"""

# =============================================================================
# БАЗОВЫЙ ГЕНЕРАТОР — УТИЛИТЫ И ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
# =============================================================================
class BaseUserGenerator:
    """Базовый класс с утилитами для генерации данных"""
    def __init__(self, conn, seed: int = 42):
        self.conn = conn
        self.rng = np.random.default_rng(seed)
        random.seed(seed)
        self.now = datetime.now()
        self._init_pools()
        self.categories = ["Электроника", "Одежда", "Косметика", "Книги", "Спорттовары", "Дом и сад", "Автозапчасти", "Детские товары", "Продукты", "Услуги"]
        self.merchants = [f"MERCH_{i:03d}" for i in range(1, 21)] + ["ONLINE_STORE", "MARKETPLACE", "RETAIL_CHAIN"]
        self.threat_words = ["суд", "иск", "жалоба", "рпн", "прокуратура", "угрожаю", "заявление", "полиция", "возбуждение дела", "компенсация ущерба"]
        self.legal_words = ["судебный иск", "претензия", "компенсация", "закон о защите прав потребителей", "гражданский кодекс", "возмещение убытков", "моральный вред"]
        self.negative_phrases = ["ужасное качество", "не соответствует описанию", "брак", "не работает", "разочарован", "не рекомендую", "потеря денег"]
        self.positive_phrases = ["отличный товар", "рекомендую", "быстрая доставка", "соответствует описанию", "доволен покупкой", "качество на высоте"]

    def _init_pools(self):
        self.private_ips = [f"192.168.{i}.{j}" for i in range(10, 20) for j in range(1, 254)]
        self.private_ips += [f"10.0.{i}.{j}" for i in range(0, 10) for j in range(1, 254)]
        self.public_ips = [f"{i}.{j}.{k}.{l}" for i in range(45, 95) for j in range(256) for k in range(256) for l in range(256)][::1000]
        self.device_pool = [f"dev_{hashlib.md5(f'seed_{i}'.encode()).hexdigest()[:16]}" for i in range(20000)]
        self.phone_pool = [f"+79{i:09d}" for i in range(100000000, 100200000)]
        self.email_domains = ["gmail.com", "mail.ru", "yandex.ru", "tempmail.com", "10minutemail.com", "guerrillamail.com"]
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
            "Mozilla/5.0 (Linux; Android 13; SM-S908B) Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
            "okhttp/4.12.0", "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SP2A.220405.004)"
        ]

    def _get_cursor(self): return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    def _generate_transaction_id(self) -> str: return f"TXN_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self.rng.integers(100000, 999999)}"
    def _generate_user_id(self, client_id: int) -> str: return f"pay_user_{client_id:06d}_{hashlib.sha256(str(client_id).encode()).hexdigest()[:8]}"
    def _generate_email(self, client_id: int, is_temp: bool = False) -> str:
        domain = random.choice(["tempmail.com", "10minutemail.com", "guerrillamail.com"]) if is_temp else random.choice(["gmail.com", "mail.ru", "yandex.ru"])
        return f"user{client_id}_{self.rng.integers(1000,9999)}@{domain}"
    def _generate_phone(self) -> str: return f"+79{self.rng.integers(100000000, 999999999)}"
    def _generate_device_fingerprint(self, ip: str = None) -> str:
        seed = f"{ip}_{self.now.timestamp()}_{self.rng.integers(0, 1000000)}" if ip else str(self.rng.integers(0, 10**12))
        return f"fp_{hashlib.sha256(seed.encode()).hexdigest()[:20]}"
    def _get_random_ip(self, is_shared: bool = False, shared_ip: str = None) -> str:
        if shared_ip: return shared_ip
        return random.choice(self.private_ips[:100]) if is_shared else random.choice(self.private_ips + self.public_ips)
    def _gen_text(self, is_threat: bool = False, is_legal: bool = False, is_negative: bool = False, is_positive: bool = False, length: int = 50) -> str:
        parts = []
        if is_negative: parts.append(random.choice(self.negative_phrases) + ". ")
        elif is_positive: parts.append(random.choice(self.positive_phrases) + ". ")
        else: parts.append(random.choice(["Заказ получен. ", "Товар на рассмотрении. ", "Есть вопросы по заказу. "]))
        if is_threat: parts.append(f"Я {random.choice(self.threat_words)}! ")
        if is_legal: parts.append(f"Ссылаюсь на {random.choice(self.legal_words)}. ")
        if length > len(" ".join(parts)):
            parts.append(''.join(random.choices(string.ascii_lowercase + ' ', k=length))[:length - len(" ".join(parts))])
        return " ".join(parts).strip()

    # --- Методы вставки оставлены без изменений (полностью совместимы с новой схемой) ---
    def _insert_clients(self, clients_: List[Dict]) -> List[int]:
        if not clients_: return []
        cur = self._get_cursor()
        client_ids = []
        for c in clients_:
            cur.execute("""INSERT INTO clients (account_age_days, total_orders, total_returns, global_return_rate, avg_order_amount, risk_flags, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING client_id""",
                        (c.get("account_age_days", 365), c.get("total_orders", 0), c.get("total_returns", 0),
                         c.get("global_return_rate", 0.0), c.get("avg_order_amount", 0.0),
                         json.dumps(c.get("risk_flags", [])), c.get("created_at", self.now)))
            client_ids.append(cur.fetchone()['client_id'])
        self.conn.commit(); cur.close()
        return client_ids

    def _insert_client_identifiers(self, client_id: int, identifiers: List[Dict]):
        if not identifiers: return
        cur = self._get_cursor()
        for i, ident in enumerate(identifiers):
            try:
                cur.execute("""INSERT INTO client_identifiers (client_id, identifier_type, identifier_value, is_primary)
                               VALUES (%s, %s, %s, %s) ON CONFLICT (identifier_type, identifier_value) DO NOTHING""",
                            (client_id, ident['type'], ident['value'], i == 0))
            except psycopg2.IntegrityError: self.conn.rollback(); continue
        self.conn.commit(); cur.close()

    def _insert_transactions(self, transactions: List[Dict]) -> List[str]:
        if not transactions: return []
        cur = self._get_cursor(); txn_ids = []
        for t in transactions:
            txn_id = t.get('transaction_id') or self._generate_transaction_id()
            try:
                cur.execute("""INSERT INTO transactions (transaction_id, user_id, amount, currency, merchant_id, category,
                               transaction_time, ip_address, device_fingerprint, status, risk_score, model_version, created_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING transaction_id""",
                            (txn_id, t['user_id'], t['amount'], t.get('currency', 'RUB'), t.get('merchant_id', random.choice(self.merchants)),
                             t.get('category', random.choice(self.categories)), t['transaction_time'], t.get('ip_address'),
                             t.get('device_fingerprint'), t.get('status', 'approved'), t.get('risk_score'),
                             t.get('model_version', 'v1.0'), t.get('created_at', self.now)))
                txn_ids.append(cur.fetchone()['transaction_id'])
            except psycopg2.IntegrityError:
                self.conn.rollback(); txn_id = self._generate_transaction_id(); continue
        self.conn.commit(); cur.close(); return txn_ids

    def _insert_sessions(self, client_id: int, sessions: List[Dict]):
        if not sessions: return
        cur = self._get_cursor()
        for s in sessions:
            cur.execute("""INSERT INTO client_sessions (client_id, ip_address, device_id, device_fingerprint, is_emulator, user_agent, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (client_id, s.get('ip_address'), s.get('device_id'), s.get('device_fingerprint'),
                         s.get('is_emulator', False), s.get('user_agent', random.choice(self.user_agents)), s.get('created_at', self.now)))
        self.conn.commit(); cur.close()

    def _insert_returns(self, returns: List[Dict]):
        if not returns: return
        cur = self._get_cursor(); values = []
        for r in returns:
            values.append((r['transaction_id'], r['client_id'], r.get('days_since_purchase', 7), r.get('return_channel', 'online'),
                           r.get('has_receipt', True), r.get('tags_removed', False), r.get('missing_components', False),
                           r.get('condition_report'), r.get('refund_amount'), r.get('created_at', self.now)))
        psycopg2.extras.execute_values(cur, """INSERT INTO returns (transaction_id, client_id, days_since_purchase, return_channel,
                                       has_receipt, tags_removed, missing_components, condition_report, refund_amount, created_at) VALUES %s""", values)
        self.conn.commit(); cur.close()

    def _insert_fraud_checks(self, checks: List[Dict]):
        if not checks: return
        cur = self._get_cursor()
        for c in checks:
            cur.execute("""INSERT INTO fraud_checks (transaction_id, check_time, input_features, prediction_result, decision, operator_comment, operator_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (c['transaction_id'], c.get('check_time', self.now), json.dumps(c['input_features']),
                         json.dumps(c['prediction_result']), c.get('decision', 'REVIEW'), c.get('operator_comment'), c.get('operator_id')))
        self.conn.commit(); cur.close()

    def _insert_tickets(self, tickets: List[Dict]):
        if not tickets: return
        cur = self._get_cursor(); values = []
        for t in tickets:
            values.append((t['client_id'], t.get('transaction_id'), t.get('subject', 'Вопрос по заказу'), t['message_text'],
                           round(t.get('sentiment_score', -0.3), 2), t.get('has_threat', False), t.get('has_legal_claim', False), t.get('created_at', self.now)))
        psycopg2.extras.execute_values(cur, """INSERT INTO support_tickets (client_id, order_id, subject, message_text,
                                       sentiment_score, has_threat, has_legal_claim, created_at) VALUES %s""", values)
        self.conn.commit(); cur.close()

    def _insert_reviews(self, reviews: List[Dict]):
        if not reviews: return
        cur = self._get_cursor(); values = []
        for r in reviews:
            values.append((r['client_id'], r.get('transaction_id'), r['rating'], r['review_text'],
                           r.get('is_negative', False), round(r.get('similarity_score', 0.0), 4), r.get('created_at', self.now)))
        psycopg2.extras.execute_values(cur, """INSERT INTO product_reviews (client_id, order_id, rating, review_text,
                                       is_negative, similarity_score, created_at) VALUES %s""", values)
        self.conn.commit(); cur.close()

    def _insert_fraud_users(self, fraud_records: List[Dict], added_by: int = None):
        if not fraud_records: return
        cur = self._get_cursor()
        for f in fraud_records:
            try:
                cur.execute("""INSERT INTO fraud_users (user_identifier, identifier_type, reason, risk_level, source, added_by, is_active, created_at, expires_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (user_identifier, identifier_type) DO UPDATE SET
                               reason = EXCLUDED.reason, risk_level = EXCLUDED.risk_level, updated_at = CURRENT_TIMESTAMP""",
                            (f['identifier'], f['type'], f.get('reason', 'Detected by pattern'), f.get('risk_level', 'HIGH'),
                             f.get('source', 'auto_ml'), added_by, f.get('is_active', True), f.get('created_at', self.now), f.get('expires_at')))
            except psycopg2.IntegrityError: self.conn.rollback(); continue
        self.conn.commit(); cur.close()

# =============================================================================
# ГЕНЕРАТОР ФРОД-ПАТТЕРНОВ (27 паттернов)
# =============================================================================
class FraudPatternGenerator(BaseUserGenerator):
    def wardrobing(self, n_cases: int = 5) -> Dict:
        logger.info(f"👗 wardrobing: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(30, 180), "total_returns": self.rng.integers(1, 4), "risk_flags": ["wardrobing_suspect"], "created_at": self.now - timedelta(days=self.rng.integers(30, 180))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(5, 15)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(15000, 50000), "category": "Одежда", "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.4, 0.7), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(3, 10), "return_channel": "pickup_point", "has_receipt": True, "tags_removed": True, "condition_report": "Следы носки, бирки удалены", "created_at": txn_time + timedelta(days=self.rng.integers(3, 10))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "wardrobing"}

    def price_arbitrage(self, n_cases: int = 5) -> Dict:
        logger.info(f"💰 price_arbitrage: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 10), "risk_flags": ["price_arbitrage"], "created_at": self.now - timedelta(days=self.rng.integers(1, 10))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(1, 5)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(30000, 80000), "category": "Электроника", "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.6, 0.9), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 3), "missing_components": True, "condition_report": "Отсутствуют оригинальные комплектующие", "created_at": txn_time + timedelta(days=self.rng.integers(1, 3))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "price_arbitrage"}

    def shipping_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"📦 shipping_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 60), "created_at": self.now - timedelta(days=self.rng.integers(1, 60))} for _ in range(n_cases)])
        transactions, returns, tickets = [], [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(2, 7)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(20000, 60000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.5, 0.8), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": 0, "has_receipt": False, "return_channel": "online", "condition_report": "Клиент заявляет о неполучении", "created_at": txn_time + timedelta(hours=12)})
            tickets.append({"client_id": cid, "transaction_id": txn_id, "subject": "Не получил товар", "message_text": "Заказ не пришел, требую возврат денег!", "sentiment_score": -0.8, "has_threat": False, "created_at": txn_time + timedelta(hours=6)})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_tickets(tickets)
        return {"clients": client_ids, "pattern": "shipping_fraud"}

    def receipt_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🧾 receipt_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 30), "created_at": self.now - timedelta(days=self.rng.integers(1, 30))} for _ in range(n_cases)])
        transactions, returns, tickets = [], [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(1, 5)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(15000, 40000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.5, 0.75), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 3), "has_receipt": False, "condition_report": "Чек утерян", "created_at": txn_time + timedelta(days=self.rng.integers(1, 3))})
            tickets.append({"client_id": cid, "transaction_id": txn_id, "message_text": self._gen_text(is_threat=True), "has_threat": True, "sentiment_score": -0.9, "created_at": txn_time + timedelta(days=2)})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_tickets(tickets)
        return {"clients": client_ids, "pattern": "receipt_fraud"}

    def switch_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔄 switch_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(5, 45), "created_at": self.now - timedelta(days=self.rng.integers(5, 45))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(2, 8)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(25000, 70000), "category": "Электроника", "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.55, 0.85), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(2, 5), "missing_components": True, "condition_report": "Возвращён товар другой модели/комплектации", "created_at": txn_time + timedelta(days=self.rng.integers(2, 5))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "switch_fraud"}

    def multi_accounting(self, n_accounts: int = 5, shared_ip: str = None) -> Dict:
        logger.info(f"🔄 multi_accounting: {n_accounts} аккаунтов")
        shared_ip = shared_ip or self._get_random_ip(is_shared=True)
        shared_device = f"dev_{hashlib.md5(shared_ip.encode()).hexdigest()[:16]}"
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 7), "risk_flags": ["multi_account"], "created_at": self.now - timedelta(days=self.rng.integers(1, 7))} for _ in range(n_accounts)])
        for i, cid in enumerate(client_ids):
            self._insert_client_identifiers(cid, [{"type": "user_id", "value": f"pay_user_{cid}_{i}"}, {"type": "email", "value": self._generate_email(cid, is_temp=True)}, {"type": "phone", "value": self._generate_phone()}, {"type": "device_id", "value": shared_device}])
            self._insert_sessions(cid, [{"ip_address": shared_ip, "device_id": shared_device, "device_fingerprint": self._generate_device_fingerprint(shared_ip), "is_emulator": self.rng.random() < 0.6, "user_agent": random.choice(self.user_agents[:4]), "created_at": self.now - timedelta(hours=self.rng.integers(1, 12))}])
        transactions, returns, fraud_checks = [], [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(1, 3)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": f"pay_user_{cid}", "amount": self.rng.uniform(10000, 30000), "transaction_time": txn_time, "ip_address": shared_ip, "device_fingerprint": self._generate_device_fingerprint(shared_ip), "status": "approved", "risk_score": self.rng.uniform(0.7, 0.95), "model_version": "v2.1", "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 3), "return_channel": "pickup_point", "has_receipt": self.rng.random() < 0.3, "created_at": txn_time + timedelta(days=self.rng.integers(1, 3))})
            fraud_checks.append({"transaction_id": txn_id, "input_features": {"ip_reuse_count": n_accounts, "device_reuse_count": n_accounts, "account_age_days": 3, "first_order": True}, "prediction_result": {"fraud_probability": 0.89, "top_features": ["ip_cluster_risk", "device_match", "new_account"]}, "decision": "REVIEW"})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_fraud_checks(fraud_checks)
        self._insert_fraud_users([{"identifier": shared_ip, "type": "ip", "reason": "Multi-accounting cluster", "risk_level": "CRITICAL", "source": "auto_ml"}])
        return {"clients": client_ids, "transactions": [t['transaction_id'] for t in transactions], "shared_ip": shared_ip, "pattern": "multi_accounting"}

    def professional_refunder(self, n_accounts: int = 8) -> Dict:
        logger.info(f"👥 professional_refunder: {n_accounts} аккаунтов")
        shared_ips = [f"192.168.15.{i}" for i in range(10, 15)]
        shared_devices = [f"dev_{hashlib.md5(f'grp_{i}'.encode()).hexdigest()[:16]}" for i in range(5)]
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 30), "total_returns": self.rng.integers(3, 10), "risk_flags": ["professional_refunder", "organized_fraud"], "created_at": self.now - timedelta(days=self.rng.integers(1, 30))} for _ in range(n_accounts)])
        sessions = []
        for i, cid in enumerate(client_ids):
            sessions.append({"client_id": cid, "ip_address": shared_ips[i % len(shared_ips)], "device_id": shared_devices[i % len(shared_devices)], "device_fingerprint": self._generate_device_fingerprint(shared_ips[i % len(shared_ips)]), "is_emulator": True, "user_agent": random.choice(self.user_agents[4:]), "created_at": self.now - timedelta(hours=i)})
        for s in sessions: self._insert_sessions(s['client_id'], [s])
        transactions, returns, tickets = [], [], []
        for cid in client_ids:
            for j in range(self.rng.integers(2, 4)):
                txn_time = self.now - timedelta(days=j); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(15000, 40000), "transaction_time": txn_time, "ip_address": shared_ips[cid % len(shared_ips)], "device_fingerprint": self._generate_device_fingerprint(shared_ips[cid % len(shared_ips)]), "status": "approved", "risk_score": self.rng.uniform(0.75, 0.98), "created_at": txn_time})
                returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": 1, "created_at": txn_time + timedelta(days=1)})
                if self.rng.random() < 0.3:
                    tickets.append({"client_id": cid, "transaction_id": txn_id, "message_text": self._gen_text(is_threat=True, is_legal=True), "has_threat": True, "has_legal_claim": True, "sentiment_score": -0.95, "created_at": txn_time + timedelta(hours=12)})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_tickets(tickets)
        return {"clients": client_ids, "pattern": "professional_refunder"}

    def review_manipulation(self, n_reviews: int = 15, shared_ip: str = None, target_transaction: str = None) -> Dict:
        logger.info(f"⭐ review_manipulation: {n_reviews} отзывов")
        shared_ip = shared_ip or f"10.0.0.{self.rng.integers(1, 254)}"
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 14), "created_at": self.now - timedelta(days=self.rng.integers(1, 14))} for _ in range(n_reviews)])
        for cid in client_ids: self._insert_sessions(cid, [{"ip_address": shared_ip, "device_id": f"dev_{self.rng.integers(1000, 2000)}", "device_fingerprint": self._generate_device_fingerprint(shared_ip), "created_at": self.now - timedelta(hours=self.rng.integers(1, 12))}])
        if not target_transaction:
            base_txn_time = self.now - timedelta(days=5); target_transaction = self._generate_transaction_id()
            self._insert_transactions([{"transaction_id": target_transaction, "user_id": self._generate_user_id(client_ids[0]), "amount": 15000, "category": "Электроника", "transaction_time": base_txn_time, "ip_address": shared_ip, "device_fingerprint": self._generate_device_fingerprint(shared_ip), "status": "approved", "risk_score": 0.3, "created_at": base_txn_time}])
        reviews = []
        for i, cid in enumerate(client_ids):
            reviews.append({"client_id": cid, "transaction_id": target_transaction, "rating": 1, "review_text": "Ужасный товар! Не рекомендую! Обман! Деньги на ветер!", "is_negative": True, "similarity_score": self.rng.uniform(0.90, 0.99), "created_at": self.now - timedelta(hours=n_reviews - i)})
        self._insert_reviews(reviews)
        self._insert_fraud_users([{"identifier": f"user_{cid}", "type": "user_id", "reason": "Review manipulation campaign", "risk_level": "HIGH", "source": "auto_ml"} for cid in client_ids[:5]])
        return {"clients": client_ids, "target_transaction": target_transaction, "shared_ip": shared_ip, "pattern": "review_manipulation"}

    def bot_attack(self, n_bots: int = 20, shared_subnet: str = "192.168.100") -> Dict:
        logger.info(f"🤖 bot_attack: {n_bots} ботов")
        client_ids = self._insert_clients([{"account_age_days": 0, "risk_flags": ["bot", "automated"], "created_at": self.now - timedelta(minutes=self.rng.integers(1, 60))} for _ in range(n_bots)])
        transactions = []
        for i, cid in enumerate(client_ids):
            txn_time = self.now - timedelta(minutes=i); txn_id = self._generate_transaction_id(); ip = f"{shared_subnet}.{self.rng.integers(1, 254)}"
            transactions.append({"transaction_id": txn_id, "user_id": f"bot_user_{cid}", "amount": self.rng.uniform(100, 500), "transaction_time": txn_time, "ip_address": ip, "device_fingerprint": f"bot_fp_{i:04d}", "status": "declined", "risk_score": self.rng.uniform(0.95, 1.0), "model_version": "v3.0-bot-detector", "created_at": txn_time})
            self._insert_sessions(cid, [{"ip_address": ip, "device_id": f"bot_dev_{i:04d}", "device_fingerprint": f"bot_fp_{i:04d}", "is_emulator": True, "user_agent": "bot/1.0", "created_at": txn_time}])
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "bot_attack"}

    def review_blackmail(self, n_cases: int = 5) -> Dict:
        logger.info(f"💬 review_blackmail: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(10, 90), "created_at": self.now - timedelta(days=self.rng.integers(10, 90))} for _ in range(n_cases)])
        transactions, tickets, reviews = [], [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(3, 10)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(8000, 25000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.4, 0.65), "created_at": txn_time})
            tickets.append({"client_id": cid, "transaction_id": txn_id, "subject": "Требую компенсацию", "message_text": self._gen_text(is_threat=True, is_legal=True, is_negative=True), "has_threat": True, "has_legal_claim": True, "sentiment_score": -0.92, "created_at": txn_time + timedelta(hours=6)})
            reviews.append({"client_id": cid, "transaction_id": txn_id, "rating": 1, "review_text": self._gen_text(is_negative=True, length=100), "is_negative": True, "similarity_score": self.rng.uniform(0.85, 0.98), "created_at": txn_time + timedelta(days=1)})
        self._insert_transactions(transactions); self._insert_tickets(tickets); self._insert_reviews(reviews)
        return {"clients": client_ids, "pattern": "review_blackmail"}

    def chargeback_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"💳 chargeback_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 90), "created_at": self.now - timedelta(days=self.rng.integers(1, 90))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(15, 45)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(20000, 100000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "chargeback", "risk_score": self.rng.uniform(0.8, 0.99), "created_at": txn_time})
            self._insert_fraud_checks([{"transaction_id": txn_id, "input_features": {"chargeback_risk": 0.95, "user_history": "new"}, "prediction_result": {"fraud_probability": 0.97, "reason": "chargeback_pattern"}, "decision": "BLOCK", "operator_comment": "Confirmed chargeback fraud"}])
            self._insert_fraud_users([{"identifier": self._generate_user_id(cid), "type": "user_id", "reason": "Confirmed chargeback fraud", "risk_level": "CRITICAL", "source": "auto_ml", "is_active": True}])
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "chargeback_fraud"}

    def friendly_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"🤷 friendly_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(90, 365), "created_at": self.now - timedelta(days=self.rng.integers(90, 365))} for _ in range(n_cases)])
        transactions, tickets = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(30, 90)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(5000, 20000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "chargeback", "risk_score": self.rng.uniform(0.6, 0.85), "created_at": txn_time})
            tickets.append({"client_id": cid, "transaction_id": txn_id, "subject": "Не узнаю этот платеж", "message_text": "Я не совершал эту покупку, это не я!", "sentiment_score": -0.7, "has_threat": False, "created_at": txn_time + timedelta(days=45)})
        self._insert_transactions(transactions); self._insert_tickets(tickets)
        return {"clients": client_ids, "pattern": "friendly_fraud"}

    def bricking(self, n_cases: int = 3) -> Dict:
        logger.info(f"📱 bricking: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 20), "created_at": self.now - timedelta(days=self.rng.integers(1, 20))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(1, 4)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(40000, 100000), "category": "Электроника", "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.65, 0.9), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 3), "missing_components": True, "condition_report": "Заменены оригинальные компоненты на дешёвые аналоги", "created_at": txn_time + timedelta(days=self.rng.integers(1, 3))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "bricking"}

    def intentional_damage(self, n_cases: int = 3) -> Dict:
        logger.info(f"💥 intentional_damage: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(10, 90), "created_at": self.now - timedelta(days=self.rng.integers(10, 90))} for _ in range(n_cases)])
        transactions, returns, tickets = [], [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(5, 15)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(20000, 50000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.5, 0.75), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(7, 14), "condition_report": "Товар имеет следы намеренной порчи", "created_at": txn_time + timedelta(days=self.rng.integers(7, 14))})
            tickets.append({"client_id": cid, "transaction_id": txn_id, "message_text": self._gen_text(is_threat=True, is_legal=True), "has_threat": True, "has_legal_claim": True, "sentiment_score": -0.88, "created_at": txn_time + timedelta(days=10)})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_tickets(tickets)
        return {"clients": client_ids, "pattern": "intentional_damage"}

    def mass_try_on(self, n_cases: int = 5) -> Dict:
        logger.info(f"👔 mass_try_on: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(15, 120), "created_at": self.now - timedelta(days=self.rng.integers(15, 120))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(3, 7)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(25000, 60000), "category": "Одежда", "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.45, 0.7), "created_at": txn_time})
            returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 2), "tags_removed": True, "condition_report": "Множественные следы примерки, бирки удалены", "created_at": txn_time + timedelta(days=self.rng.integers(1, 2))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "mass_try_on"}

    def serial_refund(self, n_cases: int = 5) -> Dict:
        logger.info(f"🔁 serial_refund: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(30, 180), "total_returns": self.rng.integers(5, 15), "risk_flags": ["serial_refunder"], "created_at": self.now - timedelta(days=self.rng.integers(30, 180))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            for j in range(self.rng.integers(3, 6)):
                txn_time = self.now - timedelta(days=j * 3); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(10000, 30000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.55, 0.8), "created_at": txn_time})
                returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 4), "created_at": txn_time + timedelta(days=self.rng.integers(1, 4))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "serial_refund"}

    def coupon_abuse(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎫 coupon_abuse: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 14), "created_at": self.now - timedelta(days=self.rng.integers(1, 14))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            for j in range(self.rng.integers(3, 7)):
                txn_time = self.now - timedelta(days=j); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(1000, 5000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.6, 0.85), "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "coupon_abuse"}

    def account_takeover(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔐 account_takeover: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(180, 730), "created_at": self.now - timedelta(days=self.rng.integers(180, 730))} for _ in range(n_cases)])
        transactions, fraud_checks = [], []
        for cid in client_ids:
            for _ in range(3):
                txn_time = self.now - timedelta(days=self.rng.integers(30, 90)); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(3000, 15000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.1, 0.3), "created_at": txn_time})
            for _ in range(self.rng.integers(2, 4)):
                txn_time = self.now - timedelta(hours=self.rng.integers(1, 12)); txn_id = self._generate_transaction_id(); new_ip = self._get_random_ip(); new_device = self._generate_device_fingerprint(new_ip)
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(30000, 80000), "transaction_time": txn_time, "ip_address": new_ip, "device_fingerprint": new_device, "status": "declined", "risk_score": self.rng.uniform(0.9, 0.99), "model_version": "v2.5-ato-detector", "created_at": txn_time})
                fraud_checks.append({"transaction_id": txn_id, "input_features": {"ip_change": True, "device_change": True, "amount_spike": 5.2, "geo_anomaly": True}, "prediction_result": {"fraud_probability": 0.96, "reason": "account_takeover"}, "decision": "BLOCK", "operator_comment": "ATO pattern detected"})
        self._insert_transactions(transactions); self._insert_fraud_checks(fraud_checks)
        return {"clients": client_ids, "pattern": "account_takeover"}

    def triangulation_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔺 triangulation_fraud: {n_cases} случаев")
        victim_ids = self._insert_clients([{"account_age_days": self.rng.integers(90, 365), "created_at": self.now - timedelta(days=self.rng.integers(90, 365))} for _ in range(n_cases)])
        fraud_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 7), "risk_flags": ["triangulation"], "created_at": self.now - timedelta(days=self.rng.integers(1, 7))} for _ in range(n_cases)])
        transactions = []
        for vid, fid in zip(victim_ids, fraud_ids):
            txn_time = self.now - timedelta(days=self.rng.integers(1, 3)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(vid), "amount": self.rng.uniform(15000, 40000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.7, 0.9), "created_at": txn_time})
            txn_id2 = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id2, "user_id": self._generate_user_id(fid), "amount": self.rng.uniform(15000, 40000), "transaction_time": txn_time + timedelta(hours=1), "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.75, 0.92), "created_at": txn_time + timedelta(hours=1)})
        self._insert_transactions(transactions)
        return {"victims": victim_ids, "frauds": fraud_ids, "pattern": "triangulation_fraud"}

    def promo_stacking(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎁 promo_stacking: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 30), "created_at": self.now - timedelta(days=self.rng.integers(1, 30))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            for j in range(self.rng.integers(4, 8)):
                txn_time = self.now - timedelta(days=j); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(500, 2000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.55, 0.8), "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "promo_stacking"}

    def refund_loop(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔄 refund_loop: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(30, 120), "total_returns": self.rng.integers(8, 20), "risk_flags": ["refund_loop"], "created_at": self.now - timedelta(days=self.rng.integers(30, 120))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            for cycle in range(3):
                txn_time = self.now - timedelta(days=cycle * 5); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(10000, 25000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.65, 0.88), "created_at": txn_time})
                returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 3), "condition_report": "Стандартный возврат без дефектов", "created_at": txn_time + timedelta(days=self.rng.integers(1, 3))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "refund_loop"}

    def fake_identity(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎭 fake_identity: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 14), "risk_flags": ["fake_identity"], "created_at": self.now - timedelta(days=self.rng.integers(1, 14))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(1, 5)); txn_id = self._generate_transaction_id()
            fake_email = f"fake{self.rng.integers(10000,99999)}@{random.choice(['tempmail.com', '10minutemail.com'])}"
            fake_phone = f"+7900{self.rng.integers(1000000, 1111111)}"
            self._insert_client_identifiers(cid, [{"type": "email", "value": fake_email}, {"type": "phone", "value": fake_phone}])
            transactions.append({"transaction_id": txn_id, "user_id": f"fake_user_{cid}", "amount": self.rng.uniform(20000, 60000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "declined", "risk_score": self.rng.uniform(0.85, 0.99), "model_version": "v2.3-identity-check", "created_at": txn_time})
            self._insert_fraud_users([{"identifier": fake_email, "type": "email", "reason": "Fake identity detected", "risk_level": "CRITICAL", "source": "auto_ml"}, {"identifier": fake_phone, "type": "phone", "reason": "Fake identity detected", "risk_level": "CRITICAL", "source": "auto_ml"}])
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "fake_identity"}

    def velocity_attack(self, n_cases: int = 3) -> Dict:
        logger.info(f"⚡ velocity_attack: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 3), "risk_flags": ["velocity_attack"], "created_at": self.now - timedelta(days=self.rng.integers(1, 3))} for _ in range(n_cases)])
        transactions, fraud_checks = [], []
        for cid in client_ids:
            for j in range(self.rng.integers(10, 25)):
                txn_time = self.now - timedelta(minutes=j * 2); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(5000, 15000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "declined" if j > 5 else "approved", "risk_score": min(0.99, 0.3 + j * 0.05), "model_version": "v3.1-velocity", "created_at": txn_time})
                if j > 5: fraud_checks.append({"transaction_id": txn_id, "input_features": {"velocity_1h": j, "velocity_5m": 3}, "prediction_result": {"fraud_probability": 0.94, "reason": "velocity_threshold"}, "decision": "BLOCK", "operator_comment": f"Velocity limit exceeded: {j} txns/hour"})
        self._insert_transactions(transactions); self._insert_fraud_checks(fraud_checks)
        return {"clients": client_ids, "pattern": "velocity_attack"}

    def geo_anomaly(self, n_cases: int = 3) -> Dict:
        logger.info(f"🌍 geo_anomaly: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(60, 365), "created_at": self.now - timedelta(days=self.rng.integers(60, 365))} for _ in range(n_cases)])
        transactions, fraud_checks = [], []
        normal_ips = ["192.168.1.100", "10.0.0.50", "172.16.0.25"]; anomaly_ips = ["203.0.113.45", "198.51.100.78", "192.0.2.123"]
        for cid in client_ids:
            for _ in range(3):
                txn_time = self.now - timedelta(days=self.rng.integers(7, 30)); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(3000, 15000), "transaction_time": txn_time, "ip_address": random.choice(normal_ips), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.1, 0.3), "created_at": txn_time})
            for _ in range(self.rng.integers(2, 4)):
                txn_time = self.now - timedelta(hours=self.rng.integers(1, 6)); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(30000, 80000), "transaction_time": txn_time, "ip_address": random.choice(anomaly_ips), "device_fingerprint": self._generate_device_fingerprint(), "status": "declined", "risk_score": self.rng.uniform(0.85, 0.98), "model_version": "v2.4-geo", "created_at": txn_time})
                fraud_checks.append({"transaction_id": txn_id, "input_features": {"geo_distance_km": 5000, "time_since_last": 2, "amount_change": 4.5}, "prediction_result": {"fraud_probability": 0.93, "reason": "geo_anomaly"}, "decision": "BLOCK"})
        self._insert_transactions(transactions); self._insert_fraud_checks(fraud_checks)
        return {"clients": client_ids, "pattern": "geo_anomaly"}

    def device_fingerprint_spoofing(self, n_cases: int = 3) -> Dict:
        logger.info(f"🎭 device_spoofing: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 14), "risk_flags": ["device_spoofing"], "created_at": self.now - timedelta(days=self.rng.integers(1, 14))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            for j in range(self.rng.integers(3, 7)):
                txn_time = self.now - timedelta(days=j); txn_id = self._generate_transaction_id()
                shared_fp = f"spoofed_fp_{cid:04d}"
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(10000, 30000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": shared_fp, "status": "declined" if j > 2 else "approved", "risk_score": min(0.99, 0.4 + j * 0.15), "model_version": "v2.6-device-integrity", "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "device_fingerprint_spoofing"}

    def card_testing(self, n_cases: int = 5) -> Dict:
        logger.info(f"💳 card_testing: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": 0, "risk_flags": ["card_testing"], "created_at": self.now - timedelta(minutes=self.rng.integers(1, 30))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            for j in range(self.rng.integers(5, 15)):
                txn_time = self.now - timedelta(minutes=j); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": f"test_user_{cid}", "amount": self.rng.uniform(1, 10), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "declined", "risk_score": self.rng.uniform(0.92, 0.99), "model_version": "v3.2-card-test-detector", "created_at": txn_time})
                self._insert_fraud_checks([{"transaction_id": txn_id, "input_features": {"amount": 1.5, "velocity_10m": j+1, "new_account": True}, "prediction_result": {"fraud_probability": 0.97, "reason": "card_testing"}, "decision": "BLOCK"}])
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "card_testing"}

    def affiliate_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔗 affiliate_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 7), "risk_flags": ["affiliate_fraud"], "created_at": self.now - timedelta(days=self.rng.integers(1, 7))} for _ in range(n_cases)])
        transactions = []
        for cid in client_ids:
            for j in range(self.rng.integers(5, 12)):
                txn_time = self.now - timedelta(days=j); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(100, 500), "merchant_id": "AFFILIATE_PARTNER_001", "transaction_time": txn_time, "ip_address": self._get_random_ip(is_shared=True), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.65, 0.88), "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "affiliate_fraud"}

    def return_abuse(self, n_cases: int = 5) -> Dict:
        logger.info(f"📦 return_abuse: {n_cases} случаев")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(30, 180), "total_returns": self.rng.integers(10, 25), "global_return_rate": self.rng.uniform(0.6, 0.95), "risk_flags": ["return_abuse"], "created_at": self.now - timedelta(days=self.rng.integers(30, 180))} for _ in range(n_cases)])
        transactions, returns = [], []
        for cid in client_ids:
            n_orders = self.rng.integers(15, 30); n_returns = int(n_orders * self.rng.uniform(0.7, 0.95))
            for j in range(n_orders):
                txn_time = self.now - timedelta(days=j * 2); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(5000, 25000), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.5, 0.8), "created_at": txn_time})
                if j < n_returns: returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(1, 5), "condition_report": "Возврат без указания причины", "created_at": txn_time + timedelta(days=self.rng.integers(1, 5))})
        self._insert_transactions(transactions); self._insert_returns(returns)
        return {"clients": client_ids, "pattern": "return_abuse"}

# =============================================================================
# ГЕНЕРАТОР ЛЕГИТИМНЫХ ПОЛЬЗОВАТЕЛЕЙ
# =============================================================================
class NormalUserGenerator(BaseUserGenerator):
    def normal_shopper(self, n_users: int = 10) -> Dict:
        logger.info(f"🛒 normal_shopper: {n_users} пользователей")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(90, 730), "total_orders": self.rng.integers(3, 15), "total_returns": self.rng.integers(0, 2), "global_return_rate": self.rng.uniform(0.0, 0.15), "avg_order_amount": self.rng.uniform(3000, 20000), "created_at": self.now - timedelta(days=self.rng.integers(90, 730))} for _ in range(n_users)])
        transactions, returns, reviews = [], [], []
        for cid in client_ids:
            for _ in range(self.rng.integers(3, 10)):
                txn_time = self.now - timedelta(days=self.rng.integers(10, 365)); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(3000, 25000), "category": random.choice(self.categories), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.05, 0.35), "created_at": txn_time})
                if self.rng.random() < 0.15: returns.append({"transaction_id": txn_id, "client_id": cid, "days_since_purchase": self.rng.integers(5, 20), "has_receipt": True, "tags_removed": False, "condition_report": "Не подошёл размер/цвет", "created_at": txn_time + timedelta(days=self.rng.integers(5, 20))})
                if self.rng.random() < 0.4: reviews.append({"client_id": cid, "transaction_id": txn_id, "rating": self.rng.integers(3, 5), "review_text": self._gen_text(is_positive=self.rng.random() < 0.7), "is_negative": False, "similarity_score": self.rng.uniform(0.0, 0.3), "created_at": txn_time + timedelta(days=self.rng.integers(3, 14))})
        self._insert_transactions(transactions); self._insert_returns(returns); self._insert_reviews(reviews)
        return {"clients": client_ids, "pattern": "normal_shopper"}

    def loyal_customer(self, n_users: int = 5) -> Dict:
        logger.info(f"💎 loyal_customer: {n_users} пользователей")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(365, 730), "total_orders": self.rng.integers(20, 50), "total_returns": self.rng.integers(0, 3), "global_return_rate": self.rng.uniform(0.0, 0.08), "avg_order_amount": self.rng.uniform(5000, 40000), "risk_flags": ["loyal"], "created_at": self.now - timedelta(days=self.rng.integers(365, 730))} for _ in range(n_users)])
        transactions = []
        for cid in client_ids:
            for _ in range(self.rng.integers(10, 25)):
                txn_time = self.now - timedelta(days=self.rng.integers(1, 365)); txn_id = self._generate_transaction_id()
                transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(5000, 40000), "category": random.choice(self.categories), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.02, 0.25), "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "loyal_customer"}

    def new_legit_user(self, n_users: int = 10) -> Dict:
        logger.info(f"🆕 new_legit_user: {n_users} пользователей")
        client_ids = self._insert_clients([{"account_age_days": self.rng.integers(1, 14), "total_orders": 1, "total_returns": 0, "global_return_rate": 0.0, "avg_order_amount": self.rng.uniform(2000, 15000), "created_at": self.now - timedelta(days=self.rng.integers(1, 14))} for _ in range(n_users)])
        transactions = []
        for cid in client_ids:
            txn_time = self.now - timedelta(days=self.rng.integers(0, 7)); txn_id = self._generate_transaction_id()
            transactions.append({"transaction_id": txn_id, "user_id": self._generate_user_id(cid), "amount": self.rng.uniform(2000, 15000), "category": random.choice(self.categories), "transaction_time": txn_time, "ip_address": self._get_random_ip(), "device_fingerprint": self._generate_device_fingerprint(), "status": "approved", "risk_score": self.rng.uniform(0.15, 0.45), "created_at": txn_time})
        self._insert_transactions(transactions)
        return {"clients": client_ids, "pattern": "new_legit_user"}

# =============================================================================
# МЕНЕДЖЕР БАЗЫ ДАННЫХ — УДОБНЫЙ ИНТЕРФЕЙС
# =============================================================================
class FraudDBManager:
    def __init__(self, db_config: Dict = DB_CONFIG):
        self.conn = psycopg2.connect(**db_config)
        self.fraud_gen = FraudPatternGenerator(self.conn)
        self.normal_gen = NormalUserGenerator(self.conn)
        logger.info("✅ FraudDBManager инициализирован")

    def init_database(self, recreate: bool = False):
        """Создание или полное пересоздание схемы БД"""
        cur = self.conn.cursor()
        try:
            if recreate:
                logger.warning("⚠️ Режим очистки: удаляю существующие таблицы...")
                drop_tables = ["product_reviews", "support_tickets", "client_sessions", "client_identifiers",
                               "returns", "fraud_users", "fraud_checks", "transactions", "clients", "users"]
                for tbl in drop_tables:
                    cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
                logger.info("🗑️ Старые таблицы удалены")
            cur.execute(PG_SCHEMA_SQL)
            self.conn.commit()
            logger.info("✅ Схема БД успешно создана/обновлена")
        except Exception as e:
            self.conn.rollback()
            logger.error(f"❌ Ошибка создания схемы: {e}")
            raise
        finally:
            cur.close()

    def add_fraud_pattern(self, pattern_name: str, **kwargs) -> Dict:
        if not hasattr(self.fraud_gen, pattern_name):
            available = [m for m in dir(self.fraud_gen) if not m.startswith('_') and callable(getattr(self.fraud_gen, m))]
            raise ValueError(f"❌ Паттерн '{pattern_name}' не найден.\nДоступные: {available}")
        method = getattr(self.fraud_gen, pattern_name)
        result = method(**kwargs)
        logger.info(f"✅ Паттерн '{pattern_name}': {result}")
        return result

    def add_normal_users(self, user_type: str = "normal_shopper", **kwargs) -> Dict:
        if not hasattr(self.normal_gen, user_type):
            available = [m for m in dir(self.normal_gen) if not m.startswith('_') and callable(getattr(self.normal_gen, m))]
            raise ValueError(f"❌ Тип '{user_type}' не найден.\nДоступные: {available}")
        method = getattr(self.normal_gen, user_type)
        result = method(**kwargs)
        logger.info(f"✅ Пользователи '{user_type}': {result}")
        return result

    def add_scenario(self, scenario_name: str) -> List[Dict]:
        scenarios = {
            "organized_fraud_ring": [("professional_refunder", {"n_accounts": 10}), ("multi_accounting", {"n_accounts": 8}), ("review_manipulation", {"n_reviews": 15})],
            "retail_fraud": [("wardrobing", {"n_cases": 10}), ("mass_try_on", {"n_cases": 8}), ("price_arbitrage", {"n_cases": 5})],
            "mixed_traffic": [("normal_shopper", {"n_users": 50}), ("wardrobing", {"n_cases": 5}), ("loyal_customer", {"n_users": 10}), ("multi_accounting", {"n_accounts": 6})],
            "technical_attacks": [("bot_attack", {"n_bots": 20}), ("velocity_attack", {"n_cases": 3}), ("card_testing", {"n_cases": 5}), ("device_fingerprint_spoofing", {"n_cases": 3})],
            "social_engineering": [("review_blackmail", {"n_cases": 5}), ("chargeback_fraud", {"n_cases": 5}), ("friendly_fraud", {"n_cases": 5})]
        }
        if scenario_name not in scenarios:
            raise ValueError(f"❌ Сценарий '{scenario_name}' не найден.\nДоступные: {list(scenarios.keys())}")
        results = []
        for pattern_name, kwargs in scenarios[scenario_name]:
            method = getattr(self.fraud_gen, pattern_name, None) or getattr(self.normal_gen, pattern_name, None)
            if method:
                results.append(method(**kwargs))
            else:
                logger.warning(f"⚠️ Паттерн '{pattern_name}' не найден, пропускаю")
        return results

    def get_stats(self) -> Dict[str, int]:
        cur = self.conn.cursor(); stats = {}
        queries = {
            "clients": "SELECT COUNT(*) FROM clients",
            "transactions": "SELECT COUNT(*) FROM transactions",
            "returns": "SELECT COUNT(*) FROM returns",
            "fraud_users_active": "SELECT COUNT(*) FROM fraud_users WHERE is_active = TRUE",
            "threat_tickets": "SELECT COUNT(*) FROM support_tickets WHERE has_threat = TRUE",
            "negative_reviews": "SELECT COUNT(*) FROM product_reviews WHERE is_negative = TRUE",
            "high_risk_txns": "SELECT COUNT(*) FROM transactions WHERE risk_score > 0.8",
            "chargebacks": "SELECT COUNT(*) FROM transactions WHERE status = 'chargeback'",
            "declined_txns": "SELECT COUNT(*) FROM transactions WHERE status = 'declined'"
        }
        for key, query in queries.items():
            try: cur.execute(query); stats[key] = cur.fetchone()[0]
            except Exception as e: stats[key] = f"Error: {e}"
        cur.close(); return stats

    def get_pattern_distribution(self) -> Dict:
        cur = self.conn.cursor()
        cur.execute("""SELECT jsonb_array_elements_text(risk_flags) as flag, COUNT(*) as client_count
                       FROM clients WHERE risk_flags != '[]'::jsonb GROUP BY jsonb_array_elements_text(risk_flags) ORDER BY client_count DESC""")
        result = {row['flag']: row['client_count'] for row in cur.fetchall()}
        cur.close(); return result

    def close(self):
        self.conn.close()
        logger.info("🔌 Подключение к БД закрыто")

# =============================================================================
# ТОЧКА ВХОДА — ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ
# =============================================================================
if __name__ == "__main__":
    manager = FraudDBManager()
    try:
        print("\n" + "="*70)
        print("🛡️  FRAUD DATA GENERATOR — ЗАПУСК")
        print("="*70 + "\n")

        # 🔑 Инициализация схемы (раскомментируйте recreate=True для полного сброса)
        print("🔧 Инициализация схемы БД...")
        manager.init_database(recreate=False)

        # === БЛОК 1: Отдельные паттерны фрода ===
        print("\n📋 БЛОК 1: Отдельные паттерны мошенничества")
        print("-"*70)
        for pattern_name, kwargs in [
            ("multi_accounting", {"n_accounts": 5}), ("wardrobing", {"n_cases": 10}),
            ("review_manipulation", {"n_reviews": 20}), ("professional_refunder", {"n_accounts": 8}),
            ("velocity_attack", {"n_cases": 3}), ("card_testing", {"n_cases": 5})
        ]:
            try:
                result = manager.add_fraud_pattern(pattern_name, **kwargs)
                print(f"   ✅ {pattern_name}: {len(result.get('clients', []))} клиентов")
            except Exception as e: print(f"   ❌ {pattern_name}: {e}")

        # === БЛОК 2: Легитимные пользователи ===
        print("\n📋 БЛОК 2: Легитимные пользователи")
        print("-"*70)
        for utype, n in [("normal_shopper", 30), ("loyal_customer", 10), ("new_legit_user", 15)]:
            res = manager.add_normal_users(utype, **{"n_users": n})
            print(f"   ✅ {utype}: {len(res['clients'])} клиентов")

        # === БЛОК 3: Готовые сценарии ===
        print("\n📋 БЛОК 3: Готовые сценарии")
        print("-"*70)
        for scenario in ["mixed_traffic", "technical_attacks"]:
            try:
                results = manager.add_scenario(scenario)
                print(f"   ✅ {scenario}: {len(results)} паттернов выполнено")
            except Exception as e: print(f"   ❌ {scenario}: {e}")

        # === БЛОК 4: Статистика ===
        print("\n📊 ИТОГОВАЯ СТАТИСТИКА")
        print("-"*70)
        for key, value in manager.get_stats().items():
            print(f"   {key:25s}: {value:>8,}")
        print("\n🔍 Распределение паттернов по клиентам:")
        for flag, count in manager.get_pattern_distribution().items():
            print(f"   {flag:30s}: {count:>5,} клиентов")
        print("\n" + "="*70)
        print("🎉 Генерация данных завершена успешно!")
        print("="*70 + "\n")
    except KeyboardInterrupt: print("\n⚠️  Прервано пользователем")
    except Exception as e: logger.error(f"❌ Ошибка: {e}", exc_info=True); sys.exit(1)
    finally: manager.close()