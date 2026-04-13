#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
ADD_FRAUD_USERS.py — Генератор тестовых данных для FraudReturn Shield v4.1
27 паттернов мошенничества + легитимные пользователи (98% / 2% распределение)
АДАПТИРОВАНО ПОД СХЕМУ: clients → orders → returns
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
from collections import defaultdict

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
    "dbname": "fraud_return_db",
    "user": "postgres",
    "password": "OmegaBloody13",
    "host": "localhost",
    "port": 5432
}

# =============================================================================
# КОНСТАНТЫ И МАППИНГИ (совместимы с DatabaseToModelMapper)
# =============================================================================
CATEGORIES = ["Электроника", "Одежда", "Косметика", "Книги", "Спорттовары", "Дом и сад", "Автозапчасти",
              "Детские товары", "Продукты", "Услуги"]
PAYMENT_METHODS = ["card", "cash", "electronic_wallet", "crypto", "invoice"]
RETURN_CHANNELS = ["online", "store", "pickup_point", "courier"]
MERCHANTS = [f"MERCH_{i:03d}" for i in range(1, 21)] + ["ONLINE_STORE", "MARKETPLACE", "RETAIL_CHAIN"]

# Распределение: 98% легитимные, 2% фрод
LEGIT_RATIO = 0.98
FRAUD_RATIO = 0.02


# =============================================================================
# БАЗОВЫЙ ГЕНЕРАТОР — УТИЛИТЫ
# =============================================================================
class BaseUserGenerator:
    """Базовый класс с утилитами для генерации данных под схему clients→orders→returns"""

    def __init__(self, conn, seed: int = 42):
        self.conn = conn
        self.rng = np.random.default_rng(seed)
        random.seed(seed)
        self.now = datetime.now()
        self._init_pools()

    def _init_pools(self):
        self.private_ips = [f"192.168.{i}.{j}" for i in range(10, 20) for j in range(1, 254)]
        self.private_ips += [f"10.0.{i}.{j}" for i in range(0, 10) for j in range(1, 254)]
        self.public_ips = []
        for _ in range(1000):
            i = random.randint(45, 94)
            j = random.randint(0, 255)
            k = random.randint(0, 255)
            l = random.randint(0, 255)
            self.public_ips.append(f"{i}.{j}.{k}.{l}")
        self.device_pool = [f"dev_{hashlib.md5(f'seed_{i}'.encode()).hexdigest()[:16]}" for i in range(20000)]
        self.email_domains = ["gmail.com", "mail.ru", "yandex.ru", "tempmail.com", "10minutemail.com"]
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
            "Mozilla/5.0 (Linux; Android 13; SM-S908B) Mobile Safari/537.36",
        ]
        self.threat_words = ["суд", "иск", "жалоба", "угрожаю", "заявление", "полиция"]
        self.legal_words = ["претензия", "компенсация", "закон о защите прав потребителей", "возмещение убытков"]
        self.negative_phrases = ["ужасное качество", "брак", "не работает", "разочарован"]
        self.positive_phrases = ["отличный товар", "рекомендую", "быстрая доставка", "доволен покупкой"]

    def _get_cursor(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _generate_email(self, client_id: int, is_temp: bool = False) -> str:
        domain = random.choice(["tempmail.com", "10minutemail.com"]) if is_temp else random.choice(
            ["gmail.com", "mail.ru", "yandex.ru"])
        return f"user{client_id}_{int(self.rng.integers(1000, 9999))}@{domain}"

    def _generate_phone(self) -> str:
        return f"+79{int(self.rng.integers(100000000, 999999999))}"

    def _generate_device_fingerprint(self, ip: str = None) -> str:
        seed = f"{ip}_{self.now.timestamp()}_{int(self.rng.integers(0, 1000000))}" if ip else str(
            int(self.rng.integers(0, 10 ** 12)))
        return f"fp_{hashlib.sha256(seed.encode()).hexdigest()[:20]}"

    def _get_random_ip(self, is_shared: bool = False, shared_ip: str = None) -> str:
        if shared_ip:
            return shared_ip
        return random.choice(self.private_ips[:100]) if is_shared else random.choice(self.private_ips + self.public_ips)

    def _gen_text(self, is_threat: bool = False, is_legal: bool = False, is_negative: bool = False,
                  is_positive: bool = False, length: int = 50) -> str:
        parts = []
        if is_negative:
            parts.append(random.choice(self.negative_phrases) + ". ")
        elif is_positive:
            parts.append(random.choice(self.positive_phrases) + ". ")
        else:
            parts.append("Заказ получен. ")
        if is_threat:
            parts.append(f"Я {random.choice(self.threat_words)}! ")
        if is_legal:
            parts.append(f"Ссылаюсь на {random.choice(self.legal_words)}. ")
        if length > len(" ".join(parts)):
            parts.append(
                ''.join(random.choices(string.ascii_lowercase + ' ', k=length))[:length - len(" ".join(parts))])
        return " ".join(parts).strip()

    # -------------------------------------------------------------------------
    # МЕТОДЫ ВСТАВКИ — АДАПТИРОВАНЫ ПОД НОВУЮ СХЕМУ
    # -------------------------------------------------------------------------
    def _insert_clients(self, clients_: List[Dict]) -> List[int]:
        """Вставка в clients + обновление aggregates"""
        if not clients_:
            return []
        cur = self._get_cursor()
        client_ids = []
        for c in clients_:
            cur.execute("""
                INSERT INTO clients (
                    account_age_days, total_orders, total_returns, global_return_rate,
                    avg_order_amount, address_change_frequency, category_returns_count,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING client_id
            """, (
                c.get("account_age_days", 365),
                c.get("total_orders", 0),
                c.get("total_returns", 0),
                c.get("global_return_rate", 0.0),
                c.get("avg_order_amount", 0.0),
                c.get("address_change_frequency", 0),
                json.dumps(c.get("category_returns_count", {})),
                c.get("created_at", self.now)
            ))
            client_ids.append(cur.fetchone()['client_id'])
        self.conn.commit()
        cur.close()
        return client_ids

    def _insert_orders(self, orders_: List[Dict]) -> List[int]:
        """Вставка в orders (основная таблица для модели)"""
        if not orders_:
            return []
        cur = self._get_cursor()
        order_ids = []
        for o in orders_:
            cur.execute("""
                INSERT INTO orders (
                    client_id, order_amount, items_count, discount_amount,
                    payment_method, order_timestamp, amount_deviation, orders_last_30d,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING order_id
            """, (
                o['client_id'],
                o['order_amount'],
                o.get('items_count', 1),
                o.get('discount_amount', 0.0),
                o.get('payment_method', 'card'),
                o['order_timestamp'],
                o.get('amount_deviation', 0.0),
                o.get('orders_last_30d', 0),
                o.get('created_at', self.now)
            ))
            order_ids.append(cur.fetchone()['order_id'])
        self.conn.commit()
        cur.close()
        return order_ids

    def _insert_returns(self, returns_: List[Dict]) -> List[int]:
        """Вставка в returns (теперь ссылается на order_id, не transaction_id)"""
        if not returns_:
            return []
        cur = self._get_cursor()
        return_ids = []
        for r in returns_:
            cur.execute("""
                INSERT INTO returns (
                    order_id, client_id, days_since_purchase, days_since_last_return,
                    return_channel, has_receipt, tags_removed, missing_components,
                    returns_last_30d, return_rate_last_30d, refund_amount,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING return_id
            """, (
                r['order_id'],  # ВАЖНО: order_id, не transaction_id!
                r['client_id'],
                r.get('days_since_purchase', 7),
                r.get('days_since_last_return', 999),
                r.get('return_channel', 'online'),
                r.get('has_receipt', True),
                r.get('tags_removed', False),
                r.get('missing_components', False),
                r.get('returns_last_30d', 0),
                r.get('return_rate_last_30d', 0.0),
                r.get('refund_amount'),
                r.get('created_at', self.now)
            ))
            return_ids.append(cur.fetchone()['return_id'])
        self.conn.commit()
        cur.close()
        return return_ids

    def _insert_sessions(self, client_id: int, sessions: List[Dict]):
        """Вставка в client_sessions для IP/device features"""
        if not sessions:
            return
        cur = self._get_cursor()
        for s in sessions:
            cur.execute("""
                INSERT INTO client_sessions (
                    client_id, ip_address, device_id, device_fingerprint,
                    is_emulator, user_agent, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                client_id,
                s.get('ip_address'),
                s.get('device_id'),
                s.get('device_fingerprint'),
                s.get('is_emulator', False),
                s.get('user_agent', random.choice(self.user_agents)),
                s.get('created_at', self.now)
            ))
        self.conn.commit()
        cur.close()

    def _insert_shared_identifiers(self, identifiers: List[Dict]):
        """Вставка в shared_identifiers для velocity-признаков"""
        if not identifiers:
            return
        cur = self._get_cursor()
        for ident in identifiers:
            cur.execute("""
                INSERT INTO shared_identifiers (
                    identifier_type, identifier_value, unique_clients_count,
                    unique_orders_count, first_seen, last_seen,
                    is_proxy, is_vpn, is_datacenter
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (identifier_type, identifier_value) DO UPDATE
                SET unique_clients_count = EXCLUDED.unique_clients_count,
                    last_seen = EXCLUDED.last_seen
            """, (
                ident['type'],
                ident['value'],
                ident.get('unique_clients_count', 1),
                ident.get('unique_orders_count', 0),
                ident.get('first_seen', self.now),
                ident.get('last_seen', self.now),
                ident.get('is_proxy', False),
                ident.get('is_vpn', False),
                ident.get('is_datacenter', False)
            ))
        self.conn.commit()
        cur.close()

    def _insert_tickets(self, tickets: List[Dict]):
        """Вставка в support_tickets"""
        if not tickets:
            return
        cur = self._get_cursor()
        values = []
        for t in tickets:
            values.append((
                t['client_id'],
                t.get('order_id'),  # Адаптировано: order_id вместо transaction_id
                t.get('subject', 'Вопрос по заказу'),
                t['message_text'],
                round(t.get('sentiment_score', -0.3), 2),
                t.get('has_threat', False),
                t.get('has_legal_claim', False),
                t.get('created_at', self.now)
            ))
        psycopg2.extras.execute_values(cur, """
            INSERT INTO support_tickets (
                client_id, order_id, subject, message_text,
                sentiment_score, has_threat, has_legal_claim, created_at
            ) VALUES %s
        """, values)
        self.conn.commit()
        cur.close()

    def _insert_reviews(self, reviews: List[Dict]):
        """Вставка в product_reviews"""
        if not reviews:
            return
        cur = self._get_cursor()
        values = []
        for r in reviews:
            values.append((
                r['client_id'],
                r.get('order_id'),  # Адаптировано: order_id
                r['rating'],
                r['review_text'],
                r.get('is_negative', False),
                round(r.get('similarity_score', 0.0), 4),
                r.get('created_at', self.now)
            ))
        psycopg2.extras.execute_values(cur, """
            INSERT INTO product_reviews (
                client_id, order_id, rating, review_text,
                is_negative, similarity_score, created_at
            ) VALUES %s
        """, values)
        self.conn.commit()
        cur.close()


# =============================================================================
# ГЕНЕРАТОР ФРОД-ПАТТЕРНОВ (27 паттернов) — АДАПТИРОВАН
# =============================================================================
class FraudPatternGenerator(BaseUserGenerator):

    def _create_client_order_return_chain(self, client_id: int, order_params: Dict, return_params: Dict) -> Tuple[
        int, int, int]:
        """Вспомогательный метод: создаёт цепочку client → order → return"""
        order_params['client_id'] = client_id
        order_params.setdefault('created_at', self.now)
        order_id = self._insert_orders([order_params])[0]

        return_params['order_id'] = order_id  # КЛЮЧЕВОЕ: order_id, не transaction_id!
        return_params['client_id'] = client_id
        return_params.setdefault('created_at', self.now)
        return_id = self._insert_returns([return_params])[0]

        return order_id, return_id

    # === ПАТТЕРН 1: Wardrobing (примерка с возвратом) ===
    def wardrobing(self, n_cases: int = 5) -> Dict:
        logger.info(f"👗 wardrobing: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(30, 180)),
            "total_orders": int(self.rng.integers(3, 8)),
            "total_returns": int(self.rng.integers(1, 4)),
            "global_return_rate": float(self.rng.uniform(0.15, 0.35)),
            "avg_order_amount": float(self.rng.uniform(8000, 25000)),
            "risk_flags": ["wardrobing_suspect"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(30, 180)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(5, 15)))
            order_params = {
                "order_amount": float(self.rng.uniform(15000, 50000)),
                "items_count": int(self.rng.integers(2, 5)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.5, 2.0)),
                "orders_last_30d": int(self.rng.integers(1, 4))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(3, 10)),
                "return_channel": "pickup_point",
                "has_receipt": True,
                "tags_removed": True,  # КЛЮЧЕВОЙ признак
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": int(self.rng.integers(1, 3)),
                "return_rate_last_30d": float(self.rng.uniform(0.2, 0.5))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "wardrobing", "count": n_cases}

    # === ПАТТЕРН 2: Price Arbitrage ===
    def price_arbitrage(self, n_cases: int = 5) -> Dict:
        logger.info(f"💰 price_arbitrage: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 10)),
            "risk_flags": ["price_arbitrage"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 10)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 5)))
            order_params = {
                "order_amount": float(self.rng.uniform(30000, 80000)),
                "items_count": 1,
                "payment_method": "crypto",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(2.0, 5.0)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(1, 3)),
                "return_channel": "online",
                "has_receipt": False,
                "tags_removed": False,
                "missing_components": True,  # КЛЮЧЕВОЙ признак
                "refund_amount": order_params["order_amount"] * 0.9,
                "returns_last_30d": int(self.rng.integers(1, 2)),
                "return_rate_last_30d": float(self.rng.uniform(0.5, 0.9))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "price_arbitrage", "count": n_cases}

    # === ПАТТЕРН 3: Shipping Fraud ===
    def shipping_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"📦 shipping_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 60)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 60)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(2, 7)))
            order_params = {
                "order_amount": float(self.rng.uniform(20000, 60000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.3, 1.5)),
                "orders_last_30d": int(self.rng.integers(1, 2))
            }
            return_params = {
                "days_since_purchase": 0,  # Возврат в день заказа
                "return_channel": "online",
                "has_receipt": False,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 1,
                "return_rate_last_30d": 1.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

            # Тикет поддержки
            self._insert_tickets([{
                "client_id": cid,
                "subject": "Не получил товар",
                "message_text": "Заказ не пришел, требую возврат денег!",
                "sentiment_score": -0.8,
                "has_threat": False,
                "created_at": order_time + timedelta(hours=6)
            }])

        return {"clients": client_ids, "pattern": "shipping_fraud", "count": n_cases}

    # === ПАТТЕРН 4: Receipt Fraud ===
    def receipt_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🧾 receipt_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 30)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 30)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 5)))
            order_params = {
                "order_amount": float(self.rng.uniform(15000, 40000)),
                "items_count": int(self.rng.integers(1, 4)),
                "payment_method": "cash",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.2, 1.0)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(1, 3)),
                "return_channel": "store",
                "has_receipt": False,  # КЛЮЧЕВОЙ признак
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": int(self.rng.integers(1, 2)),
                "return_rate_last_30d": float(self.rng.uniform(0.3, 0.7))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

            self._insert_tickets([{
                "client_id": cid,
                "message_text": self._gen_text(is_threat=True),
                "has_threat": True,
                "sentiment_score": -0.9,
                "created_at": order_time + timedelta(days=2)
            }])

        return {"clients": client_ids, "pattern": "receipt_fraud", "count": n_cases}

    # === ПАТТЕРН 5: Switch Fraud ===
    def switch_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔄 switch_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(5, 45)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(5, 45)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(2, 8)))
            order_params = {
                "order_amount": float(self.rng.uniform(25000, 70000)),
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(1.0, 3.0)),
                "orders_last_30d": int(self.rng.integers(1, 2))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(2, 5)),
                "return_channel": "courier",
                "has_receipt": True,
                "tags_removed": False,
                "missing_components": True,  # КЛЮЧЕВОЙ признак
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 1,
                "return_rate_last_30d": 1.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "switch_fraud", "count": n_cases}

    # === ПАТТЕРН 6: Multi-Accounting ===
    def multi_accounting(self, n_accounts: int = 5, shared_ip: str = None) -> Dict:
        logger.info(f"🔄 multi_accounting: {n_accounts} аккаунтов")
        shared_ip = shared_ip or self._get_random_ip(is_shared=True)
        shared_device = f"dev_{hashlib.md5(shared_ip.encode()).hexdigest()[:16]}"

        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 7)),
            "risk_flags": ["multi_account"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 7)))
        } for _ in range(n_accounts)])

        # Сессии с общим IP/device
        for cid in client_ids:
            self._insert_sessions(cid, [{
                "ip_address": shared_ip,
                "device_id": shared_device,
                "device_fingerprint": self._generate_device_fingerprint(shared_ip),
                "is_emulator": self.rng.random() < 0.6,
                "user_agent": random.choice(self.user_agents),
                "created_at": self.now - timedelta(hours=int(self.rng.integers(1, 12)))
            }])

        # Shared identifier для velocity
        self._insert_shared_identifiers([{
            "type": "ip",
            "value": shared_ip,
            "unique_clients_count": n_accounts,
            "unique_orders_count": n_accounts,
            "first_seen": self.now - timedelta(days=1),
            "last_seen": self.now
        }])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 3)))
            order_params = {
                "order_amount": float(self.rng.uniform(10000, 30000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "electronic_wallet",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.5, 2.0)),
                "orders_last_30d": 1
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(1, 3)),
                "return_channel": "pickup_point",
                "has_receipt": self.rng.random() < 0.3,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 1,
                "return_rate_last_30d": 1.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {
            "clients": client_ids,
            "pattern": "multi_accounting",
            "count": n_accounts,
            "shared_ip": shared_ip
        }

    # === ПАТТЕРН 7: Professional Refunder ===
    def professional_refunder(self, n_accounts: int = 8) -> Dict:
        logger.info(f"👥 professional_refunder: {n_accounts} аккаунтов")
        shared_ips = [f"192.168.15.{i}" for i in range(10, 15)]
        shared_devices = [f"dev_{hashlib.md5(f'grp_{i}'.encode()).hexdigest()[:16]}" for i in range(5)]

        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 30)),
            "total_orders": int(self.rng.integers(5, 15)),
            "total_returns": int(self.rng.integers(3, 10)),
            "global_return_rate": float(self.rng.uniform(0.4, 0.8)),
            "risk_flags": ["professional_refunder", "organized_fraud"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 30)))
        } for _ in range(n_accounts)])

        for i, cid in enumerate(client_ids):
            self._insert_sessions(cid, [{
                "ip_address": shared_ips[i % len(shared_ips)],
                "device_id": shared_devices[i % len(shared_devices)],
                "device_fingerprint": self._generate_device_fingerprint(shared_ips[i % len(shared_ips)]),
                "is_emulator": True,
                "user_agent": self.user_agents[-1],
                "created_at": self.now - timedelta(hours=i)
            }])

        for cid in client_ids:
            for j in range(int(self.rng.integers(2, 4))):
                order_time = self.now - timedelta(days=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(15000, 40000)),
                    "items_count": int(self.rng.integers(1, 3)),
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.3, 1.5)),
                    "orders_last_30d": int(self.rng.integers(3, 8))
                }
                return_params = {
                    "days_since_purchase": 1,
                    "return_channel": "online",
                    "has_receipt": True,
                    "tags_removed": False,
                    "missing_components": False,
                    "refund_amount": order_params["order_amount"],
                    "returns_last_30d": int(self.rng.integers(2, 5)),
                    "return_rate_last_30d": float(self.rng.uniform(0.5, 0.9))
                }
                self._create_client_order_return_chain(cid, order_params, return_params)

                if self.rng.random() < 0.3:
                    self._insert_tickets([{
                        "client_id": cid,
                        "message_text": self._gen_text(is_threat=True, is_legal=True),
                        "has_threat": True,
                        "has_legal_claim": True,
                        "sentiment_score": -0.95,
                        "created_at": order_time + timedelta(hours=12)
                    }])

        return {"clients": client_ids, "pattern": "professional_refunder", "count": n_accounts}

    # === ПАТТЕРН 8: Review Manipulation ===
    def review_manipulation(self, n_reviews: int = 15, shared_ip: str = None, target_order_id: int = None) -> Dict:
        logger.info(f"⭐ review_manipulation: {n_reviews} отзывов")
        shared_ip = shared_ip or f"10.0.0.{int(self.rng.integers(1, 254))}"

        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 14)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 14)))
        } for _ in range(n_reviews)])

        for cid in client_ids:
            self._insert_sessions(cid, [{
                "ip_address": shared_ip,
                "device_id": f"dev_{int(self.rng.integers(1000, 2000))}",
                "device_fingerprint": self._generate_device_fingerprint(shared_ip),
                "created_at": self.now - timedelta(hours=int(self.rng.integers(1, 12)))
            }])

        # Создаём целевой заказ если не передан
        if not target_order_id:
            base_cid = client_ids[0]
            base_txn_time = self.now - timedelta(days=5)
            order_params = {
                "client_id": base_cid,
                "order_amount": 15000,
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": base_txn_time,
                "amount_deviation": 0.0,
                "orders_last_30d": 1
            }
            target_order_id = self._insert_orders([order_params])[0]

        # Генерируем отзывы
        reviews = []
        for i, cid in enumerate(client_ids):
            reviews.append({
                "client_id": cid,
                "order_id": target_order_id,
                "rating": 1,
                "review_text": "Ужасный товар! Не рекомендую! Обман!",
                "is_negative": True,
                "similarity_score": float(self.rng.uniform(0.90, 0.99)),
                "created_at": self.now - timedelta(hours=n_reviews - i)
            })
        self._insert_reviews(reviews)

        return {
            "clients": client_ids,
            "target_order_id": target_order_id,
            "shared_ip": shared_ip,
            "pattern": "review_manipulation",
            "count": n_reviews
        }

    # === ПАТТЕРН 9: Bot Attack ===
    def bot_attack(self, n_bots: int = 20, shared_subnet: str = "192.168.100") -> Dict:
        logger.info(f"🤖 bot_attack: {n_bots} ботов")
        client_ids = self._insert_clients([{
            "account_age_days": 0,
            "risk_flags": ["bot", "automated"],
            "created_at": self.now - timedelta(minutes=int(self.rng.integers(1, 60)))
        } for _ in range(n_bots)])

        for i, cid in enumerate(client_ids):
            order_time = self.now - timedelta(minutes=i)
            ip = f"{shared_subnet}.{int(self.rng.integers(1, 254))}"
            order_params = {
                "order_amount": float(self.rng.uniform(100, 500)),
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": 0.0,
                "orders_last_30d": 1
            }
            # Боты обычно не делают возвраты, но если делают — сразу фрод
            if self.rng.random() < 0.3:
                return_params = {
                    "days_since_purchase": 0,
                    "return_channel": "online",
                    "has_receipt": False,
                    "tags_removed": False,
                    "missing_components": True,
                    "refund_amount": order_params["order_amount"],
                    "returns_last_30d": 1,
                    "return_rate_last_30d": 1.0
                }
                self._create_client_order_return_chain(cid, order_params, return_params)
            else:
                order_params['client_id'] = cid
                self._insert_orders([order_params])

            self._insert_sessions(cid, [{
                "ip_address": ip,
                "device_id": f"bot_dev_{i:04d}",
                "device_fingerprint": f"bot_fp_{i:04d}",
                "is_emulator": True,
                "user_agent": "bot/1.0",
                "created_at": order_time
            }])

        return {"clients": client_ids, "pattern": "bot_attack", "count": n_bots}

    # === ПАТТЕРН 10: Review Blackmail ===
    def review_blackmail(self, n_cases: int = 5) -> Dict:
        logger.info(f"💬 review_blackmail: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(10, 90)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(10, 90)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(3, 10)))
            order_params = {
                "order_amount": float(self.rng.uniform(8000, 25000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.2, 1.0)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(2, 5)),
                "return_channel": "online",
                "has_receipt": True,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": int(self.rng.integers(1, 2)),
                "return_rate_last_30d": float(self.rng.uniform(0.2, 0.5))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

            self._insert_tickets([{
                "client_id": cid,
                "subject": "Требую компенсацию",
                "message_text": self._gen_text(is_threat=True, is_legal=True, is_negative=True),
                "has_threat": True,
                "has_legal_claim": True,
                "sentiment_score": -0.92,
                "created_at": order_time + timedelta(hours=6)
            }])

            self._insert_reviews([{
                "client_id": cid,
                "order_id": order_params.get('order_id', 0),
                "rating": 1,
                "review_text": self._gen_text(is_negative=True, length=100),
                "is_negative": True,
                "similarity_score": float(self.rng.uniform(0.85, 0.98)),
                "created_at": order_time + timedelta(days=1)
            }])

        return {"clients": client_ids, "pattern": "review_blackmail", "count": n_cases}

    # === ПАТТЕРН 11: Chargeback Fraud ===
    def chargeback_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"💳 chargeback_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 90)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 90)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(15, 45)))
            order_params = {
                "order_amount": float(self.rng.uniform(20000, 100000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(1.0, 4.0)),
                "orders_last_30d": int(self.rng.integers(1, 2))
            }
            # Chargeback — возврат через банк, не через нашу систему
            # Но фиксируем в returns для истории
            return_params = {
                "days_since_purchase": int(self.rng.integers(30, 60)),
                "return_channel": "online",
                "has_receipt": False,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 0,
                "return_rate_last_30d": 0.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "chargeback_fraud", "count": n_cases}

    # === ПАТТЕРН 12: Friendly Fraud ===
    def friendly_fraud(self, n_cases: int = 5) -> Dict:
        logger.info(f"🤷 friendly_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(90, 365)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(90, 365)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(30, 90)))
            order_params = {
                "order_amount": float(self.rng.uniform(5000, 20000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.1, 0.8)),
                "orders_last_30d": int(self.rng.integers(1, 4))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(45, 90)),
                "return_channel": "online",
                "has_receipt": False,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 1,
                "return_rate_last_30d": 1.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

            self._insert_tickets([{
                "client_id": cid,
                "subject": "Не узнаю этот платеж",
                "message_text": "Я не совершал эту покупку, это не я!",
                "sentiment_score": -0.7,
                "has_threat": False,
                "created_at": order_time + timedelta(days=45)
            }])

        return {"clients": client_ids, "pattern": "friendly_fraud", "count": n_cases}

    # === ПАТТЕРН 13: Bricking ===
    def bricking(self, n_cases: int = 3) -> Dict:
        logger.info(f"📱 bricking: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 20)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 20)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 4)))
            order_params = {
                "order_amount": float(self.rng.uniform(40000, 100000)),
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(2.0, 5.0)),
                "orders_last_30d": 1
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(1, 3)),
                "return_channel": "courier",
                "has_receipt": True,
                "tags_removed": False,
                "missing_components": True,  # КЛЮЧЕВОЙ признак
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": 1,
                "return_rate_last_30d": 1.0
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "bricking", "count": n_cases}

    # === ПАТТЕРН 14: Intentional Damage ===
    def intentional_damage(self, n_cases: int = 3) -> Dict:
        logger.info(f"💥 intentional_damage: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(10, 90)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(10, 90)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(5, 15)))
            order_params = {
                "order_amount": float(self.rng.uniform(20000, 50000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.3, 1.5)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(7, 14)),
                "return_channel": "store",
                "has_receipt": True,
                "tags_removed": False,
                "missing_components": False,
                "refund_amount": order_params["order_amount"],
                "returns_last_30d": int(self.rng.integers(1, 2)),
                "return_rate_last_30d": float(self.rng.uniform(0.2, 0.5))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

            self._insert_tickets([{
                "client_id": cid,
                "message_text": self._gen_text(is_threat=True, is_legal=True),
                "has_threat": True,
                "has_legal_claim": True,
                "sentiment_score": -0.88,
                "created_at": order_time + timedelta(days=10)
            }])

        return {"clients": client_ids, "pattern": "intentional_damage", "count": n_cases}

    # === ПАТТЕРН 15: Mass Try-On ===
    def mass_try_on(self, n_cases: int = 5) -> Dict:
        logger.info(f"👔 mass_try_on: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(15, 120)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(15, 120)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(3, 7)))
            order_params = {
                "order_amount": float(self.rng.uniform(25000, 60000)),
                "items_count": int(self.rng.integers(5, 15)),  # Много товаров
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.5, 2.0)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            return_params = {
                "days_since_purchase": int(self.rng.integers(1, 2)),
                "return_channel": "pickup_point",
                "has_receipt": True,
                "tags_removed": True,  # КЛЮЧЕВОЙ признак
                "missing_components": False,
                "refund_amount": order_params["order_amount"] * 0.8,  # Частичный возврат
                "returns_last_30d": int(self.rng.integers(1, 3)),
                "return_rate_last_30d": float(self.rng.uniform(0.3, 0.7))
            }
            self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "mass_try_on", "count": n_cases}

    # === ПАТТЕРН 16: Serial Refund ===
    def serial_refund(self, n_cases: int = 5) -> Dict:
        logger.info(f"🔁 serial_refund: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(30, 180)),
            "total_orders": int(self.rng.integers(10, 25)),
            "total_returns": int(self.rng.integers(5, 15)),
            "global_return_rate": float(self.rng.uniform(0.3, 0.6)),
            "risk_flags": ["serial_refunder"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(30, 180)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for j in range(int(self.rng.integers(3, 6))):
                order_time = self.now - timedelta(days=j * 3)
                order_params = {
                    "order_amount": float(self.rng.uniform(10000, 30000)),
                    "items_count": int(self.rng.integers(1, 3)),
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.2, 1.0)),
                    "orders_last_30d": int(self.rng.integers(3, 8))
                }
                return_params = {
                    "days_since_purchase": int(self.rng.integers(1, 4)),
                    "return_channel": random.choice(RETURN_CHANNELS),
                    "has_receipt": self.rng.random() < 0.8,
                    "tags_removed": False,
                    "missing_components": False,
                    "refund_amount": order_params["order_amount"],
                    "returns_last_30d": int(self.rng.integers(2, 6)),
                    "return_rate_last_30d": float(self.rng.uniform(0.4, 0.8))
                }
                self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "serial_refund", "count": n_cases}

    # === ПАТТЕРН 17: Coupon Abuse ===
    def coupon_abuse(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎫 coupon_abuse: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 14)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 14)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for j in range(self.rng.integers(3, 7)):
                order_time = self.now - timedelta(days=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(1000, 5000)),
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "discount_amount": float(self.rng.uniform(500, 2000)),  # Большой дисконт
                    "amount_deviation": float(self.rng.uniform(0.1, 0.5)),
                    "orders_last_30d": int(self.rng.integers(3, 7))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "coupon_abuse", "count": n_cases}

    # === ПАТТЕРН 18: Account Takeover ===
    def account_takeover(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔐 account_takeover: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(180, 730)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(180, 730)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            # Нормальная история
            for _ in range(3):
                order_time = self.now - timedelta(days=int(self.rng.integers(30, 90)))
                order_params = {
                    "order_amount": float(self.rng.uniform(3000, 15000)),
                    "items_count": int(self.rng.integers(1, 3)),
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.1, 0.5)),
                    "orders_last_30d": int(self.rng.integers(1, 4))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

            # Подозрительная активность
            for _ in range(int(self.rng.integers(2, 4))):
                order_time = self.now - timedelta(hours=int(self.rng.integers(1, 12)))
                new_ip = self._get_random_ip()
                order_params = {
                    "order_amount": float(self.rng.uniform(30000, 80000)),  # Резкий рост
                    "items_count": 1,
                    "payment_method": "crypto",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(3.0, 8.0)),
                    "orders_last_30d": 1
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])
                self._insert_sessions(cid, [{
                    "ip_address": new_ip,
                    "device_fingerprint": self._generate_device_fingerprint(new_ip),
                    "is_emulator": False,
                    "created_at": order_time
                }])

        return {"clients": client_ids, "pattern": "account_takeover", "count": n_cases}

    # === ПАТТЕРН 19: Triangulation Fraud ===
    def triangulation_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔺 triangulation_fraud: {n_cases} случаев")
        victim_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(90, 365)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(90, 365)))
        } for _ in range(n_cases)])
        fraud_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 7)),
            "risk_flags": ["triangulation"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 7)))
        } for _ in range(n_cases)])

        for vid, fid in zip(victim_ids, fraud_ids):
            # Заказ от жертвы
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 3)))
            order_params = {
                "order_amount": float(self.rng.uniform(15000, 40000)),
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(0.3, 1.5)),
                "orders_last_30d": int(self.rng.integers(1, 3))
            }
            order_params['client_id'] = vid
            self._insert_orders([order_params])

            # Заказ от фродера (тот же товар)
            order_params['client_id'] = fid
            order_params['order_timestamp'] = order_time + timedelta(hours=1)
            self._insert_orders([order_params])

        return {
            "victims": victim_ids,
            "frauds": fraud_ids,
            "pattern": "triangulation_fraud",
            "count": n_cases
        }

    # === ПАТТЕРН 20: Promo Stacking ===
    def promo_stacking(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎁 promo_stacking: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 30)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 30)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for j in range(int(self.rng.integers(4, 8))):
                order_time = self.now - timedelta(days=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(500, 2000)),
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "discount_amount": float(self.rng.uniform(200, 800)),
                    "amount_deviation": float(self.rng.uniform(0.1, 0.4)),
                    "orders_last_30d": int(self.rng.integers(4, 8))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "promo_stacking", "count": n_cases}

    # === ПАТТЕРН 21: Refund Loop ===
    def refund_loop(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔄 refund_loop: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(30, 120)),
            "total_orders": int(self.rng.integers(15, 30)),
            "total_returns": int(self.rng.integers(8, 20)),
            "global_return_rate": float(self.rng.uniform(0.5, 0.85)),
            "risk_flags": ["refund_loop"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(30, 120)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for cycle in range(3):
                order_time = self.now - timedelta(days=cycle * 5)
                order_params = {
                    "order_amount": float(self.rng.uniform(10000, 25000)),
                    "items_count": int(self.rng.integers(1, 3)),
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.2, 1.0)),
                    "orders_last_30d": int(self.rng.integers(3, 8))
                }
                return_params = {
                    "days_since_purchase": int(self.rng.integers(1, 3)),
                    "return_channel": "online",
                    "has_receipt": True,
                    "tags_removed": False,
                    "missing_components": False,
                    "refund_amount": order_params["order_amount"],
                    "returns_last_30d": int(self.rng.integers(3, 8)),
                    "return_rate_last_30d": float(self.rng.uniform(0.5, 0.9))
                }
                self._create_client_order_return_chain(cid, order_params, return_params)

        return {"clients": client_ids, "pattern": "refund_loop", "count": n_cases}

    # === ПАТТЕРН 22: Fake Identity ===
    def fake_identity(self, n_cases: int = 5) -> Dict:
        logger.info(f"🎭 fake_identity: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 14)),
            "risk_flags": ["fake_identity"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 14)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(1, 5)))
            fake_email = f"fake{int(self.rng.integers(10000, 99999))}@{random.choice(['tempmail.com', '10minutemail.com'])}"
            fake_phone = f"+7900{int(self.rng.integers(1000000, 1111111))}"

            order_params = {
                "order_amount": float(self.rng.uniform(20000, 60000)),
                "items_count": 1,
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": float(self.rng.uniform(1.0, 4.0)),
                "orders_last_30d": 1
            }
            order_params['client_id'] = cid
            self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "fake_identity", "count": n_cases}

    # === ПАТТЕРН 23: Velocity Attack ===
    def velocity_attack(self, n_cases: int = 3) -> Dict:
        logger.info(f"⚡ velocity_attack: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 3)),
            "risk_flags": ["velocity_attack"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 3)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for j in range(int(self.rng.integers(10, 25))):
                order_time = self.now - timedelta(minutes=j * 2)
                order_params = {
                    "order_amount": float(self.rng.uniform(5000, 15000)),
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": 0.0,
                    "orders_last_30d": j + 1  # Растущий счётчик
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "velocity_attack", "count": n_cases}

    # === ПАТТЕРН 24: Geo Anomaly ===
    def geo_anomaly(self, n_cases: int = 3) -> Dict:
        logger.info(f"🌍 geo_anomaly: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(60, 365)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(60, 365)))
        } for _ in range(n_cases)])

        normal_ips = ["192.168.1.100", "10.0.0.50", "172.16.0.25"]
        anomaly_ips = ["203.0.113.45", "198.51.100.78", "192.0.2.123"]

        for cid in client_ids:
            # Нормальные заказы
            for _ in range(3):
                order_time = self.now - timedelta(days=int(self.rng.integers(7, 30)))
                order_params = {
                    "order_amount": float(self.rng.uniform(3000, 15000)),
                    "items_count": int(self.rng.integers(1, 3)),
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.1, 0.5)),
                    "orders_last_30d": int(self.rng.integers(1, 4))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])
                self._insert_sessions(cid, [{
                    "ip_address": random.choice(normal_ips),
                    "created_at": order_time
                }])

            # Подозрительные заказы с других гео
            for _ in range(int(self.rng.integers(2, 4))):
                order_time = self.now - timedelta(hours=int(self.rng.integers(1, 6)))
                order_params = {
                    "order_amount": float(self.rng.uniform(30000, 80000)),
                    "items_count": 1,
                    "payment_method": "crypto",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(3.0, 8.0)),
                    "orders_last_30d": 1
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])
                self._insert_sessions(cid, [{
                    "ip_address": random.choice(anomaly_ips),
                    "created_at": order_time
                }])

        return {"clients": client_ids, "pattern": "geo_anomaly", "count": n_cases}

    # === ПАТТЕРН 25: Device Fingerprint Spoofing ===
    def device_fingerprint_spoofing(self, n_cases: int = 3) -> Dict:
        logger.info(f"🎭 device_spoofing: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 14)),
            "risk_flags": ["device_spoofing"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 14)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            shared_fp = f"spoofed_fp_{cid:04d}"
            for j in range(int(self.rng.integers(3, 7))):
                order_time = self.now - timedelta(days=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(10000, 30000)),
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.3, 1.5)),
                    "orders_last_30d": j + 1
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])
                self._insert_sessions(cid, [{
                    "device_fingerprint": shared_fp,
                    "is_emulator": True,
                    "created_at": order_time
                }])

        return {"clients": client_ids, "pattern": "device_fingerprint_spoofing", "count": n_cases}

    # === ПАТТЕРН 26: Card Testing ===
    def card_testing(self, n_cases: int = 5) -> Dict:
        logger.info(f"💳 card_testing: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": 0,
            "risk_flags": ["card_testing"],
            "created_at": self.now - timedelta(minutes=int(self.rng.integers(1, 30)))
        } for _ in range(n_cases)])

        for cid in client_ids:
            for j in range(int(self.rng.integers(5, 15))):
                order_time = self.now - timedelta(minutes=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(1, 10)),  # Микро-суммы
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": 0.0,
                    "orders_last_30d": j + 1
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "card_testing", "count": n_cases}

    # === ПАТТЕРН 27: Affiliate Fraud ===
    def affiliate_fraud(self, n_cases: int = 3) -> Dict:
        logger.info(f"🔗 affiliate_fraud: {n_cases} случаев")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 7)),
            "risk_flags": ["affiliate_fraud"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 7)))
        } for _ in range(n_cases)])

        shared_ip = self._get_random_ip(is_shared=True)
        for cid in client_ids:
            for j in range(int(self.rng.integers(5, 12))):
                order_time = self.now - timedelta(days=j)
                order_params = {
                    "order_amount": float(self.rng.uniform(100, 500)),
                    "items_count": 1,
                    "payment_method": "card",
                    "order_timestamp": order_time,
                    "amount_deviation": 0.0,
                    "orders_last_30d": int(self.rng.integers(5, 12))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])
                self._insert_sessions(cid, [{
                    "ip_address": shared_ip,
                    "created_at": order_time
                }])

        return {"clients": client_ids, "pattern": "affiliate_fraud", "count": n_cases}


# =============================================================================
# ГЕНЕРАТОР ЛЕГИТИМНЫХ ПОЛЬЗОВАТЕЛЕЙ
# =============================================================================
class NormalUserGenerator(BaseUserGenerator):

    def normal_shopper(self, n_users: int = 10) -> Dict:
        logger.info(f"🛒 normal_shopper: {n_users} пользователей")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(90, 730)),
            "total_orders": int(self.rng.integers(3, 15)),
            "total_returns": int(self.rng.integers(0, 2)),
            "global_return_rate": float(self.rng.uniform(0.0, 0.15)),
            "avg_order_amount": float(self.rng.uniform(3000, 20000)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(90, 730)))
        } for _ in range(n_users)])

        for cid in client_ids:
            n_orders = int(self.rng.integers(3, 10))
            for _ in range(n_orders):
                order_time = self.now - timedelta(days=int(self.rng.integers(10, 365)))
                order_params = {
                    "order_amount": float(self.rng.uniform(3000, 25000)),
                    "items_count": int(self.rng.integers(1, 5)),
                    "payment_method": random.choice(PAYMENT_METHODS),
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.1, 1.0)),
                    "orders_last_30d": int(self.rng.integers(1, 5))
                }
                order_params['client_id'] = cid
                order_id = self._insert_orders([order_params])[0]

                # Редкие возвраты (15%)
                if self.rng.random() < 0.15:
                    return_params = {
                        "days_since_purchase": int(self.rng.integers(5, 20)),
                        "return_channel": random.choice(RETURN_CHANNELS),
                        "has_receipt": True,
                        "tags_removed": False,
                        "missing_components": False,
                        "refund_amount": order_params["order_amount"],
                        "returns_last_30d": int(self.rng.integers(0, 2)),
                        "return_rate_last_30d": float(self.rng.uniform(0.0, 0.2))
                    }
                    return_params['order_id'] = order_id
                    return_params['client_id'] = cid
                    self._insert_returns([return_params])

                # Отзывы (40%)
                if self.rng.random() < 0.4:
                    self._insert_reviews([{
                        "client_id": cid,
                        "order_id": order_id,
                        "rating": int(self.rng.integers(3, 5)),
                        "review_text": self._gen_text(is_positive=self.rng.random() < 0.7),
                        "is_negative": False,
                        "similarity_score": float(self.rng.uniform(0.0, 0.3)),
                        "created_at": order_time + timedelta(days=int(self.rng.integers(3, 14)))
                    }])

        return {"clients": client_ids, "pattern": "normal_shopper", "count": n_users}

    def loyal_customer(self, n_users: int = 5) -> Dict:
        logger.info(f"💎 loyal_customer: {n_users} пользователей")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(365, 730)),
            "total_orders": int(self.rng.integers(20, 50)),
            "total_returns": int(self.rng.integers(0, 3)),
            "global_return_rate": float(self.rng.uniform(0.0, 0.08)),
            "avg_order_amount": float(self.rng.uniform(5000, 40000)),
            "risk_flags": ["loyal"],
            "created_at": self.now - timedelta(days=int(self.rng.integers(365, 730)))
        } for _ in range(n_users)])

        for cid in client_ids:
            for _ in range(int(self.rng.integers(10, 25))):
                order_time = self.now - timedelta(days=int(self.rng.integers(1, 365)))
                order_params = {
                    "order_amount": float(self.rng.uniform(5000, 40000)),
                    "items_count": int(self.rng.integers(1, 5)),
                    "payment_method": random.choice(PAYMENT_METHODS),
                    "order_timestamp": order_time,
                    "amount_deviation": float(self.rng.uniform(0.1, 0.8)),
                    "orders_last_30d": int(self.rng.integers(2, 8))
                }
                order_params['client_id'] = cid
                self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "loyal_customer", "count": n_users}

    def new_legit_user(self, n_users: int = 10) -> Dict:
        logger.info(f"🆕 new_legit_user: {n_users} пользователей")
        client_ids = self._insert_clients([{
            "account_age_days": int(self.rng.integers(1, 14)),
            "total_orders": 1,
            "total_returns": 0,
            "global_return_rate": 0.0,
            "avg_order_amount": float(self.rng.uniform(2000, 15000)),
            "created_at": self.now - timedelta(days=int(self.rng.integers(1, 14)))
        } for _ in range(n_users)])

        for cid in client_ids:
            order_time = self.now - timedelta(days=int(self.rng.integers(0, 7)))
            order_params = {
                "order_amount": float(self.rng.uniform(2000, 15000)),
                "items_count": int(self.rng.integers(1, 3)),
                "payment_method": "card",
                "order_timestamp": order_time,
                "amount_deviation": 0.0,
                "orders_last_30d": 1
            }
            order_params['client_id'] = cid
            self._insert_orders([order_params])

        return {"clients": client_ids, "pattern": "new_legit_user", "count": n_users}


# =============================================================================
# МЕНЕДЖЕР БАЗЫ ДАННЫХ
# =============================================================================
class FraudDBManager:
    def __init__(self, db_config: Dict = DB_CONFIG):
        self.conn = psycopg2.connect(**db_config)
        self.fraud_gen = FraudPatternGenerator(self.conn)
        self.normal_gen = NormalUserGenerator(self.conn)
        logger.info("✅ FraudDBManager инициализирован")

    def init_database(self, recreate: bool = False):
        """Создание схемы — используется внешняя миграция"""
        logger.info("🔧 Схема БД должна быть создана отдельно (см. schema.sql)")

    def generate_balanced_dataset(self, total_users: int = 1000, fraud_ratio: float = FRAUD_RATIO) -> Dict:
        """Генерация сбалансированного датасета: 98% легитимные, 2% фрод"""
        n_fraud = int(total_users * fraud_ratio)
        n_legit = total_users - n_fraud

        logger.info(f"📊 Генерация: {n_legit} легитимных + {n_fraud} фрод-кейсов")
        logger.info("=" * 50)

        results = {"legit": [], "fraud": []}

        # Легитимные пользователи (98%)
        legit_patterns = [
            ("normal_shopper", {"n_users": n_legit // 3}),
            ("loyal_customer", {"n_users": n_legit // 3}),
            ("new_legit_user", {"n_users": n_legit - 2 * (n_legit // 3)})
        ]
        for i, (pattern, kwargs) in enumerate(legit_patterns, 1):
            logger.info(f"  [{i}/{len(legit_patterns)}] Генерация паттерна: {pattern}")
            method = getattr(self.normal_gen, pattern)
            results["legit"].append(method(**kwargs))
            logger.info(f"  ✅ Завершено: {pattern}")
        logger.info("🟢 ЗАВЕРШЕНО: Легитимные пользователи")
        logger.info("=" * 50)

        # Фрод-паттерны (2%) — распределяем по 27 типам
        logger.info("🔴 НАЧАЛО: Генерация фрод-паттернов...")
        fraud_per_pattern = max(1, n_fraud // 27)
        fraud_patterns = [
            ("wardrobing", fraud_per_pattern), ("price_arbitrage", fraud_per_pattern),
            ("shipping_fraud", fraud_per_pattern), ("receipt_fraud", max(1, fraud_per_pattern // 2)),
            ("switch_fraud", max(1, fraud_per_pattern // 2)), ("multi_accounting", {"n_accounts": fraud_per_pattern}),
            ("professional_refunder", {"n_accounts": max(1, fraud_per_pattern // 2)}),
            ("review_manipulation", {"n_reviews": fraud_per_pattern * 2}),
            ("bot_attack", {"n_bots": fraud_per_pattern * 3}), ("review_blackmail", fraud_per_pattern),
            ("chargeback_fraud", fraud_per_pattern), ("friendly_fraud", fraud_per_pattern),
            ("bricking", max(1, fraud_per_pattern // 2)), ("intentional_damage", max(1, fraud_per_pattern // 2)),
            ("mass_try_on", fraud_per_pattern), ("serial_refund", fraud_per_pattern),
            ("coupon_abuse", fraud_per_pattern), ("account_takeover", max(1, fraud_per_pattern // 2)),
            ("triangulation_fraud", max(1, fraud_per_pattern // 2)), ("promo_stacking", fraud_per_pattern),
            ("refund_loop", max(1, fraud_per_pattern // 2)), ("fake_identity", fraud_per_pattern),
            ("velocity_attack", max(1, fraud_per_pattern // 2)), ("geo_anomaly", max(1, fraud_per_pattern // 2)),
            ("device_fingerprint_spoofing", max(1, fraud_per_pattern // 2)),
            ("card_testing", fraud_per_pattern * 2), ("affiliate_fraud", max(1, fraud_per_pattern // 2))
        ]

        for i, item in enumerate(fraud_patterns, 1):
            if isinstance(item, tuple) and isinstance(item[1], dict):
                pattern_name, kwargs = item
                method = getattr(self.fraud_gen, pattern_name, None)
                if method:
                    logger.info(f"  [{i}/{len(fraud_patterns)}] Генерация паттерна: {pattern_name}")
                    results["fraud"].append(method(**kwargs))
                    logger.info(f"  ✅ Завершено: {pattern_name}")
            elif isinstance(item, tuple):
                pattern_name, count = item
                method = getattr(self.fraud_gen, pattern_name, None)
                if method:
                    logger.info(f"  [{i}/{len(fraud_patterns)}] Генерация паттерна: {pattern_name}")
                    results["fraud"].append(method(
                        n_cases=count if pattern_name not in ["multi_accounting", "professional_refunder",
                                                              "review_manipulation", "bot_attack",
                                                              "card_testing"] else fraud_per_pattern))
                    logger.info(f"  ✅ Завершено: {pattern_name}")

        logger.info("🔴 ЗАВЕРШЕНО: Фрод-паттерны")
        logger.info("=" * 50)
        return results

    def get_stats(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        stats = {}
        queries = {
            "clients": "SELECT COUNT(*) FROM clients",
            "orders": "SELECT COUNT(*) FROM orders",
            "returns": "SELECT COUNT(*) FROM returns",
            "high_return_rate_clients": "SELECT COUNT(*) FROM clients WHERE global_return_rate > 0.3",
            "fast_returns": "SELECT COUNT(*) FROM returns WHERE days_since_purchase <= 3",
            "no_receipt_returns": "SELECT COUNT(*) FROM returns WHERE has_receipt = FALSE",
            "tags_removed_returns": "SELECT COUNT(*) FROM returns WHERE tags_removed = TRUE",
            "missing_components_returns": "SELECT COUNT(*) FROM returns WHERE missing_components = TRUE",
        }
        for key, query in queries.items():
            try:
                cur.execute(query)
                stats[key] = cur.fetchone()[0]
            except Exception as e:
                stats[key] = f"Error: {e}"
        cur.close()
        return stats

    def close(self):
        self.conn.close()
        logger.info("🔌 Подключение к БД закрыто")


# =============================================================================
# ТОЧКА ВХОДА
# =============================================================================
if __name__ == "__main__":
    manager = FraudDBManager()
    try:
        print("\n" + "=" * 70)
        print("🛡️  FRAUDRETURN SHIELD v4.1 — DATA GENERATOR")
        print("   Схема: clients → orders → returns | Распределение: 98% / 2%")
        print("=" * 70 + "\n")

        # Генерация сбалансированного датасета
        print("📊 Генерация данных (1000 пользователей, 98/2 распределение)...")
        results = manager.generate_balanced_dataset(total_users=1000, fraud_ratio=0.02)

        # Статистика
        print("\n📈 СТАТИСТИКА ПОСЛЕ ГЕНЕРАЦИИ:")
        print("-" * 70)
        for key, value in manager.get_stats().items():
            print(f"   {key:35s}: {value:>8,}")

        print("\n✅ Паттерны фрода сгенерированы:")
        for r in results["fraud"]:
            print(f"   • {r.get('pattern', 'unknown')}: {r.get('count', 'N/A')} кейсов")

        print("\n" + "=" * 70)
        print("🎉 Генерация завершена! Данные готовы для обучения/тестирования модели.")
        print("=" * 70 + "\n")

    except KeyboardInterrupt:
        print("\n⚠️  Прервано пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        sys.exit(1)
    finally:
        manager.close()