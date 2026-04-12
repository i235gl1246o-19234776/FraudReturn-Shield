# =============================================================================
# ADD_FRAUD_USERS.py — Добавление мошенников и обычных пользователей
# 27 паттернов фрода с готовыми сценариями
# =============================================================================

import psycopg2
import psycopg2.extras
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Tuple
import random
import string

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "dbname": "fraud_db",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5432
}


# =============================================================================
# БАЗОВЫЙ ГЕНЕРАТОР
# =============================================================================
class BaseUserGenerator:
    """Базовые утилиты для генерации данных"""

    def __init__(self, conn, seed=42):
        self.conn = conn
        self.rng = np.random.default_rng(seed)
        random.seed(seed)
        self.now = datetime.now()

        # Пулы для переиспользования
        self.ip_pool = [f"192.168.{i}.{j}" for i in range(10, 20) for j in range(1, 254)]
        self.device_pool = [f"dev_{i:04d}" for i in range(1000, 2000)]
        self.phone_pool = [f"+79{i:09d}" for i in range(100000000, 100100000)]

        self.categories = ["Электроника", "Одежда", "Косметика", "Книги", "Спорттовары"]
        self.payment_methods = ["card", "cash", "electronic_wallet"]

        # Слова для тикетов/отзывов
        self.threat_words = ["суд", "иск", "жалоба", "рпн", "прокуратура", "угрожаю"]
        self.legal_words = ["судебный иск", "претензия", "компенсация", "закон о защите"]

    def _get_cursor(self):
        return self.conn.cursor()

    def _insert_clients(self, clients_data: List[Dict]) -> List[int]:
        """Вставка клиентов, возврат ID"""
        if not clients_data:
            return []

        cur = self._get_cursor()
        ids = []
        for c in clients_data:
            cur.execute("""
                INSERT INTO clients (account_age_days, total_orders, total_returns, 
                                    global_return_rate, avg_order_amount, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING client_id
            """, (
                c.get("account_age_days", 365),
                c.get("total_orders", 0),
                c.get("total_returns", 0),
                c.get("global_return_rate", 0.0),
                c.get("avg_order_amount", 0.0),
                c.get("created_at", self.now)
            ))
            ids.append(cur.fetchone()[0])
        self.conn.commit()
        cur.close()
        return ids

    def _insert_sessions(self, sessions: List[Dict]):
        if not sessions: return
        cur = self._get_cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO client_sessions (client_id, ip_address, device_id, 
                                        is_emulator, user_agent, created_at)
            VALUES %s
        """, [(s["client_id"], s["ip"], s["device"], s.get("is_emulator", False),
               s.get("user_agent", "Mozilla/5.0"), s.get("created_at", self.now)) for s in sessions])
        self.conn.commit()
        cur.close()

    def _insert_orders(self, orders: List[Dict]) -> List[int]:
        if not orders: return []
        cur = self._get_cursor()
        ids = []
        for o in orders:
            cur.execute("""
                INSERT INTO orders (client_id, order_amount, items_count, discount_amount,
                                   payment_method, order_timestamp, amount_deviation)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING order_id
            """, (o["client_id"], o["amount"], o.get("items", 1), o.get("discount", 0),
                  o.get("payment", "card"), o["timestamp"], o.get("deviation", 0)))
            ids.append(cur.fetchone()[0])
        self.conn.commit()
        cur.close()
        return ids

    def _insert_returns(self, returns: List[Dict]):
        if not returns: return
        cur = self._get_cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO returns (order_id, client_id, days_since_purchase, 
                                return_channel, has_receipt, tags_removed, 
                                missing_components, created_at)
            VALUES %s
        """, [(r["order_id"], r["client_id"], r.get("days_since", 7),
               r.get("channel", "online"), r.get("has_receipt", True),
               r.get("tags_removed", False), r.get("missing_components", False),
               r.get("timestamp", self.now)) for r in returns])
        self.conn.commit()
        cur.close()

    def _insert_tickets(self, tickets: List[Dict]):
        if not tickets: return
        cur = self._get_cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO support_tickets (client_id, order_id, subject, message_text,
                                        sentiment_score, has_threat, has_legal_claim, created_at)
            VALUES %s
        """, [(t["client_id"], t.get("order_id"), t.get("subject", "Вопрос"),
               t["text"], t.get("sentiment", -0.5), t.get("has_threat", False),
               t.get("has_legal", False), t.get("timestamp", self.now)) for t in tickets])
        self.conn.commit()
        cur.close()

    def _insert_reviews(self, reviews: List[Dict]):
        if not reviews: return
        cur = self._get_cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO product_reviews (client_id, order_id, rating, review_text,
                                        is_negative, similarity_score, created_at)
            VALUES %s
        """, [(r["client_id"], r["order_id"], r["rating"], r["text"],
               r.get("is_negative", False), r.get("similarity", 0.0),
               r.get("timestamp", self.now)) for r in reviews])
        self.conn.commit()
        cur.close()

    def _gen_text(self, is_threat=False, is_legal=False, is_negative=False, length=30):
        base = "Товар получил. " if not is_negative else "Ужасное качество! "
        if is_threat: base += random.choice(self.threat_words) + " "
        if is_legal: base += random.choice(self.legal_words) + " "
        return base + " ".join(random.choices(string.ascii_lowercase, k=length))


# =============================================================================
# 27 ПАТТЕРНОВ ФРОДА
# =============================================================================
class FraudPatternGenerator(BaseUserGenerator):
    """Генератор мошенников по 27 паттернам"""

    def wardrobing(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 1: Wardrobing (бронирование одежды)
        - Покупка одежды перед мероприятием
        - Возврат после использования
        - Удаленные бирки, следы носки
        """
        logger.info(f" Генерация wardrobing: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(30, 180),
            "created_at": self.now - timedelta(days=self.rng.integers(30, 180))
        } for _ in range(n_cases)])

        orders = []
        returns = []

        for cid in client_ids:
            # Заказ дорогой одежды за 1-3 дня до события
            order_ts = self.now - timedelta(days=self.rng.integers(5, 15))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(15000, 50000),
                "items": self.rng.integers(3, 8),
                "timestamp": order_ts,
                "payment": "card"
            }])[0]

            orders.append(oid)

            # Быстрый возврат с удаленными бирками
            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(3, 10),
                "channel": "pickup_point",
                "has_receipt": True,
                "tags_removed": True,  # 🔑 Ключевой признак
                "timestamp": order_ts + timedelta(days=self.rng.integers(3, 10))
            })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "pattern": "wardrobing"}

    def price_arbitrage(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 2: Price Arbitrage (подмена комплектующих)
        - Покупка электроники со скидкой
        - Возврат с недостающими компонентами
        - Молодые аккаунты
        """
        logger.info(f"💰 Генерация price_arbitrage: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 10),  # 🔑 Новые аккаунты
            "created_at": self.now - timedelta(days=self.rng.integers(1, 10))
        } for _ in range(n_cases)])

        orders = []
        returns = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(1, 5))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(30000, 80000),
                "items": 1,
                "discount": self.rng.uniform(5000, 15000),  # 🔑 Большая скидка
                "timestamp": order_ts,
                "payment": "electronic_wallet"
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(1, 3),
                "has_receipt": True,
                "missing_components": True,  # 🔑 Ключевой признак
                "timestamp": order_ts + timedelta(days=self.rng.integers(1, 3))
            })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "pattern": "price_arbitrage"}

    def multi_accounting(self, n_accounts: int = 5, shared_ip: str = None) -> Dict:
        """
        Паттерн 12: Multi-Accounting (множество аккаунтов с одного IP)
        - 5+ аккаунтов с одинаковым IP
        - Каждый делает 1 заказ со скидкой первого заказа
        - Все возвращают товар
        """
        logger.info(f"🔄 Генерация multi_accounting: {n_accounts} аккаунтов")

        shared_ip = shared_ip or f"192.168.{self.rng.integers(10, 20)}.{self.rng.integers(1, 254)}"
        shared_device = random.choice(self.device_pool)

        # Создаем несколько аккаунтов
        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 7),  # 🔑 Очень новые
            "created_at": self.now - timedelta(days=self.rng.integers(1, 7))
        } for _ in range(n_accounts)])

        # Все сессии с одного IP и устройства
        sessions = [{
            "client_id": cid,
            "ip": shared_ip,  # 🔑 Одинаковый IP
            "device": shared_device,  # 🔑 Одинаковое устройство
            "is_emulator": self.rng.random() < 0.5,
            "created_at": self.now - timedelta(hours=self.rng.integers(1, 24))
        } for cid in client_ids]

        self._insert_sessions(sessions)

        orders = []
        returns = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(1, 3))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(10000, 30000),
                "discount": self.rng.uniform(3000, 8000),  # 🔑 Скидка первого заказа
                "timestamp": order_ts,
                "payment": "card"
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(1, 5),
                "timestamp": order_ts + timedelta(days=self.rng.integers(1, 5))
            })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "shared_ip": shared_ip, "pattern": "multi_accounting"}

    def professional_refunder(self, n_accounts: int = 8) -> Dict:
        """
        Паттерн 11: Professional Refunder (организованная группа)
        - 8+ аккаунтов с пересекающимися IP/devices
        - Высокая скорость возвратов
        - Эмуляторы
        """
        logger.info(f"👥 Генерация professional_refunder: {n_accounts} аккаунтов")

        # Ограниченный пул IP и устройств
        shared_ips = [f"192.168.15.{i}" for i in range(10, 15)]
        shared_devices = [f"dev_{i}" for i in range(500, 505)]

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 30),
            "created_at": self.now - timedelta(days=self.rng.integers(1, 30))
        } for _ in range(n_accounts)])

        # Сессии с пересечением IP и устройств
        sessions = []
        for i, cid in enumerate(client_ids):
            sessions.append({
                "client_id": cid,
                "ip": shared_ips[i % len(shared_ips)],  # 🔑 Переиспользование IP
                "device": shared_devices[i % len(shared_devices)],  # 🔑 Переиспользование devices
                "is_emulator": True,  # 🔑 Эмуляторы
                "created_at": self.now - timedelta(hours=i)
            })

        self._insert_sessions(sessions)

        orders = []
        returns = []
        tickets = []

        for cid in client_ids:
            # Множественные заказы
            for j in range(self.rng.integers(2, 4)):
                order_ts = self.now - timedelta(days=j)
                oid = self._insert_orders([{
                    "client_id": cid,
                    "amount": self.rng.uniform(15000, 40000),
                    "timestamp": order_ts
                }])[0]

                orders.append(oid)

                returns.append({
                    "order_id": oid,
                    "client_id": cid,
                    "days_since": 1,  # 🔑 Быстрый возврат
                    "timestamp": order_ts + timedelta(days=1)
                })

                # Тикеты с угрозами
                if self.rng.random() < 0.3:
                    tickets.append({
                        "client_id": cid,
                        "order_id": oid,
                        "text": self._gen_text(is_threat=True),
                        "has_threat": True,  # 🔑 Угрозы
                        "timestamp": order_ts + timedelta(hours=12)
                    })

        self._insert_returns(returns)
        self._insert_tickets(tickets)
        return {"clients": client_ids, "orders": orders, "pattern": "professional_refunder"}

    def review_blackmail(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 19: Review Blackmail (шантаж отзывами)
        - Угрозы в тикетах
        - Негативные отзывы
        - Требование компенсации
        """
        logger.info(f"💬 Генерация review_blackmail: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(10, 90),
            "created_at": self.now - timedelta(days=self.rng.integers(10, 90))
        } for _ in range(n_cases)])

        orders = []
        tickets = []
        reviews = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(3, 10))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(8000, 25000),
                "timestamp": order_ts
            }])[0]

            orders.append(oid)

            # Тикет с угрозами
            tickets.append({
                "client_id": cid,
                "order_id": oid,
                "subject": "Требую компенсацию",
                "text": self._gen_text(is_threat=True, is_legal=True, is_negative=True),
                "has_threat": True,  # 🔑 Угрозы
                "has_legal": True,  # 🔑 Юридические угрозы
                "timestamp": order_ts + timedelta(hours=6)
            })

            # Негативный отзыв с высокой схожестью
            reviews.append({
                "client_id": cid,
                "order_id": oid,
                "rating": 1,  # 🔑 Низкий рейтинг
                "text": self._gen_text(is_negative=True),
                "is_negative": True,
                "similarity": self.rng.uniform(0.85, 0.98),  # 🔑 Высокая схожесть
                "timestamp": order_ts + timedelta(days=1)
            })

        self._insert_tickets(tickets)
        self._insert_reviews(reviews)
        return {"clients": client_ids, "orders": orders, "pattern": "review_blackmail"}

    def review_manipulation(self, n_reviews: int = 15, shared_ip: str = None) -> Dict:
        """
        Паттерн 24: Review Manipulation (накрутка отзывов)
        - Множество аккаунтов с одного IP
        - Похожие негативные отзывы
        - Координированная атака
        """
        logger.info(f"⭐ Генерация review_manipulation: {n_reviews} отзывов")

        shared_ip = shared_ip or f"10.0.0.{self.rng.integers(1, 254)}"

        # Создаем аккаунты
        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 14),
            "created_at": self.now - timedelta(days=self.rng.integers(1, 14))
        } for _ in range(n_reviews)])

        # Все с одного IP
        sessions = [{
            "client_id": cid,
            "ip": shared_ip,  # 🔑 Один IP для всех
            "device": f"dev_{self.rng.integers(1000, 2000)}",
            "created_at": self.now - timedelta(hours=self.rng.integers(1, 12))
        } for cid in client_ids]

        self._insert_sessions(sessions)

        orders = []
        reviews = []

        # Один и тот же товар (order_id) для всех отзывов
        base_order_ts = self.now - timedelta(days=5)
        base_oid = self._insert_orders([{
            "client_id": client_ids[0],
            "amount": 15000,
            "timestamp": base_order_ts
        }])[0]

        orders.append(base_oid)

        # Все пишут похожие негативные отзывы
        review_text = "Ужасный товар! Не рекомендую! Обман!"  # 🔑 Одинаковый текст
        for i, cid in enumerate(client_ids):
            if i > 0:
                # Остальные аккаунты просто пишут отзывы на тот же заказ
                pass

            reviews.append({
                "client_id": cid,
                "order_id": base_oid,
                "rating": 1,  # 🔑 Только 1 звезда
                "text": review_text,
                "is_negative": True,
                "similarity": self.rng.uniform(0.90, 0.99),  # 🔑 Очень высокая схожесть
                "timestamp": base_order_ts + timedelta(hours=i)
            })

        self._insert_reviews(reviews)
        return {"clients": client_ids, "orders": [base_oid], "shared_ip": shared_ip, "pattern": "review_manipulation"}

    def shipping_fraud(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 3: Shipping Fraud (ложная доставка)
        - Несоответствие адреса
        - Заявления о неполучении
        - Аномалии веса
        """
        logger.info(f"📦 Генерация shipping_fraud: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 60),
            "created_at": self.now - timedelta(days=self.rng.integers(1, 60))
        } for _ in range(n_cases)])

        orders = []
        returns = []
        tickets = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(2, 7))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(20000, 60000),
                "timestamp": order_ts,
                "payment": "card"
            }])[0]

            orders.append(oid)

            # Быстрый возврат с заявлением "не получил"
            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": 0,  # 🔑 В тот же день
                "channel": "online",
                "has_receipt": False,  # 🔑 Нет чека
                "timestamp": order_ts + timedelta(hours=12)
            })

            tickets.append({
                "client_id": cid,
                "order_id": oid,
                "subject": "Не получил товар",
                "text": "Заказ не пришел, требую возврат!",
                "timestamp": order_ts + timedelta(hours=6)
            })

        self._insert_returns(returns)
        self._insert_tickets(tickets)
        return {"clients": client_ids, "orders": orders, "pattern": "shipping_fraud"}

    def mass_try_on(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 17: Mass Try-On (массовые примерки)
        - Большие заказы (8-15 товаров)
        - Быстрый возврат
        - Удаленные бирки
        """
        logger.info(f"👔 Генерация mass_try_on: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(15, 120),
            "created_at": self.now - timedelta(days=self.rng.integers(15, 120))
        } for _ in range(n_cases)])

        orders = []
        returns = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(3, 7))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(25000, 60000),
                "items": self.rng.integers(8, 15),  # 🔑 Много товаров
                "timestamp": order_ts
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(1, 2),  # 🔑 Очень быстрый
                "tags_removed": True,  # 🔑 Бирки удалены
                "timestamp": order_ts + timedelta(days=self.rng.integers(1, 2))
            })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "pattern": "mass_try_on"}

    def serial_refund(self, n_cases: int = 5) -> Dict:
        """
        Паттерн 26: Serial Refund (серийные возвраты)
        - Многократные возвраты одного товара
        - Повторное использование RMA
        """
        logger.info(f"🔁 Генерация serial_refund: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(30, 180),
            "total_returns": self.rng.integers(5, 15),  # 🔑 История возвратов
            "created_at": self.now - timedelta(days=self.rng.integers(30, 180))
        } for _ in range(n_cases)])

        orders = []
        returns = []

        for cid in client_ids:
            # Несколько заказов с возвратами
            for j in range(self.rng.integers(3, 6)):
                order_ts = self.now - timedelta(days=j * 3)
                oid = self._insert_orders([{
                    "client_id": cid,
                    "amount": self.rng.uniform(10000, 30000),
                    "timestamp": order_ts
                }])[0]

                orders.append(oid)

                returns.append({
                    "order_id": oid,
                    "client_id": cid,
                    "days_since": self.rng.integers(1, 4),
                    "timestamp": order_ts + timedelta(days=self.rng.integers(1, 4))
                })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "pattern": "serial_refund"}

    # ... Остальные паттерны можно добавить аналогично ...
    # Для краткости показываю ключевые, остальные по тому же принципу

    def receipt_fraud(self, n_cases: int = 3) -> Dict:
        """Паттерн 4: Receipt Fraud (отсутствие чека)"""
        logger.info(f"🧾 Генерация receipt_fraud: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 30),
            "created_at": self.now - timedelta(days=self.rng.integers(1, 30))
        } for _ in range(n_cases)])

        orders = []
        returns = []
        tickets = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(1, 5))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(15000, 40000),
                "timestamp": order_ts
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(1, 3),
                "has_receipt": False,  # 🔑 Нет чека
                "timestamp": order_ts + timedelta(days=self.rng.integers(1, 3))
            })

            tickets.append({
                "client_id": cid,
                "order_id": oid,
                "text": self._gen_text(is_threat=True),
                "has_threat": True,
                "timestamp": order_ts + timedelta(days=2)
            })

        self._insert_returns(returns)
        self._insert_tickets(tickets)
        return {"clients": client_ids, "orders": orders, "pattern": "receipt_fraud"}

    def bricking(self, n_cases: int = 3) -> Dict:
        """Паттерн 10: Bricking (возврат электроники без комплектующих)"""
        logger.info(f"📱 Генерация bricking: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(1, 20),
            "created_at": self.now - timedelta(days=self.rng.integers(1, 20))
        } for _ in range(n_cases)])

        orders = []
        returns = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(1, 4))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(40000, 100000),
                "items": 1,
                "timestamp": order_ts
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(1, 3),
                "missing_components": True,  # 🔑 Нет комплектующих
                "timestamp": order_ts + timedelta(days=self.rng.integers(1, 3))
            })

        self._insert_returns(returns)
        return {"clients": client_ids, "orders": orders, "pattern": "bricking"}

    def intentional_damage(self, n_cases: int = 3) -> Dict:
        """Паттерн 14: Intentional Damage (намеренная порча)"""
        logger.info(f"💥 Генерация intentional_damage: {n_cases} случаев")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(10, 90),
            "created_at": self.now - timedelta(days=self.rng.integers(10, 90))
        } for _ in range(n_cases)])

        orders = []
        returns = []
        tickets = []

        for cid in client_ids:
            order_ts = self.now - timedelta(days=self.rng.integers(5, 15))
            oid = self._insert_orders([{
                "client_id": cid,
                "amount": self.rng.uniform(20000, 50000),
                "timestamp": order_ts
            }])[0]

            orders.append(oid)

            returns.append({
                "order_id": oid,
                "client_id": cid,
                "days_since": self.rng.integers(7, 14),
                "timestamp": order_ts + timedelta(days=self.rng.integers(7, 14))
            })

            tickets.append({
                "client_id": cid,
                "order_id": oid,
                "text": self._gen_text(is_threat=True, is_legal=True),
                "has_threat": True,
                "has_legal": True,
                "timestamp": order_ts + timedelta(days=10)
            })

        self._insert_returns(returns)
        self._insert_tickets(tickets)
        return {"clients": client_ids, "orders": orders, "pattern": "intentional_damage"}


# =============================================================================
# ОБЫЧНЫЕ ПОЛЬЗОВАТЕЛИ
# =============================================================================
class NormalUserGenerator(BaseUserGenerator):
    """Генератор обычных (легитимных) пользователей"""

    def normal_shopper(self, n_users: int = 10) -> Dict:
        """Обычные покупатели с редкими возвратами"""
        logger.info(f"🛒 Генерация normal_shopper: {n_users} пользователей")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(90, 730),
            "created_at": self.now - timedelta(days=self.rng.integers(90, 730))
        } for _ in range(n_users)])

        orders = []
        returns = []
        reviews = []

        for cid in client_ids:
            n_orders = self.rng.integers(3, 10)

            for _ in range(n_orders):
                order_ts = self.now - timedelta(days=self.rng.integers(10, 365))
                oid = self._insert_orders([{
                    "client_id": cid,
                    "amount": self.rng.uniform(3000, 25000),
                    "items": self.rng.integers(1, 4),
                    "timestamp": order_ts
                }])[0]

                orders.append(oid)

                # Редкие возвраты (15%)
                if self.rng.random() < 0.15:
                    returns.append({
                        "order_id": oid,
                        "client_id": cid,
                        "days_since": self.rng.integers(5, 20),
                        "has_receipt": True,
                        "timestamp": order_ts + timedelta(days=self.rng.integers(5, 20))
                    })

                # Отзывы (40%)
                if self.rng.random() < 0.4:
                    reviews.append({
                        "client_id": cid,
                        "order_id": oid,
                        "rating": self.rng.integers(3, 5),  # 🔑 Хорошие оценки
                        "text": self._gen_text(),
                        "is_negative": False,
                        "similarity": self.rng.uniform(0.0, 0.3),
                        "timestamp": order_ts + timedelta(days=self.rng.integers(3, 14))
                    })

        self._insert_returns(returns)
        self._insert_reviews(reviews)
        return {"clients": client_ids, "orders": orders, "type": "normal"}

    def loyal_customer(self, n_users: int = 5) -> Dict:
        """Лояльные клиенты с высокой активностью"""
        logger.info(f"💎 Генерация loyal_customer: {n_users} пользователей")

        client_ids = self._insert_clients([{
            "account_age_days": self.rng.integers(365, 730),
            "created_at": self.now - timedelta(days=self.rng.integers(365, 730))
        } for _ in range(n_users)])

        orders = []

        for cid in client_ids:
            n_orders = self.rng.integers(10, 25)

            for _ in range(n_orders):
                order_ts = self.now - timedelta(days=self.rng.integers(1, 365))
                oid = self._insert_orders([{
                    "client_id": cid,
                    "amount": self.rng.uniform(5000, 40000),
                    "timestamp": order_ts
                }])[0]
                orders.append(oid)

        return {"clients": client_ids, "orders": orders, "type": "loyal"}


# =============================================================================
# УДОБНЫЙ ИНТЕРФЕЙС
# =============================================================================
class FraudDBManager:
    """Удобный менеджер для добавления пользователей"""

    def __init__(self, db_config: Dict = DB_CONFIG):
        self.conn = psycopg2.connect(**db_config)
        self.fraud_gen = FraudPatternGenerator(self.conn)
        self.normal_gen = NormalUserGenerator(self.conn)
        logger.info("✅ FraudDBManager инициализирован")

    def add_fraud_pattern(self, pattern_name: str, **kwargs):
        """
        Добавить мошенников по паттерну

        Примеры:
        - manager.add_fraud_pattern("multi_accounting", n_accounts=5)
        - manager.add_fraud_pattern("wardrobing", n_cases=10)
        - manager.add_fraud_pattern("review_manipulation", n_reviews=20)
        """
        if not hasattr(self.fraud_gen, pattern_name):
            raise ValueError(f"Паттерн '{pattern_name}' не найден")

        method = getattr(self.fraud_gen, pattern_name)
        result = method(**kwargs)
        logger.info(f"✅ Добавлено: {result}")
        return result

    def add_normal_users(self, user_type: str = "normal", **kwargs):
        """
        Добавить обычных пользователей

        user_type: "normal" или "loyal"
        """
        if user_type == "normal":
            return self.normal_gen.normal_shopper(**kwargs)
        elif user_type == "loyal":
            return self.normal_gen.loyal_customer(**kwargs)
        else:
            raise ValueError(f"Неизвестный тип: {user_type}")

    def add_scenario(self, scenario_name: str):
        """
        Добавить готовый сценарий с несколькими паттернами
        """
        scenarios = {
            "organized_fraud_ring": [
                ("professional_refunder", {"n_accounts": 10}),
                ("multi_accounting", {"n_accounts": 8}),
                ("review_manipulation", {"n_reviews": 15}),
            ],
            "retail_fraud": [
                ("wardrobing", {"n_cases": 10}),
                ("mass_try_on", {"n_cases": 8}),
                ("price_arbitrage", {"n_cases": 5}),
            ],
            "mixed_traffic": [
                ("normal_shopper", {"n_users": 50}),
                ("wardrobing", {"n_cases": 5}),
                ("loyal_customer", {"n_users": 10}),
                ("multi_accounting", {"n_accounts": 6}),
            ]
        }

        if scenario_name not in scenarios:
            raise ValueError(f"Сценарий '{scenario_name}' не найден. Доступные: {list(scenarios.keys())}")

        results = []
        for pattern_name, kwargs in scenarios[scenario_name]:
            if hasattr(self.fraud_gen, pattern_name):
                result = getattr(self.fraud_gen, pattern_name)(**kwargs)
            else:
                result = getattr(self.normal_gen, pattern_name)(**kwargs)
            results.append(result)

        logger.info(f"✅ Сценарий '{scenario_name}' выполнен: {len(results)} паттернов")
        return results

    def close(self):
        self.conn.close()
        logger.info("🔌 Подключение к БД закрыто")


# =============================================================================
# ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ
# =============================================================================
if __name__ == "__main__":
    # Инициализация
    manager = FraudDBManager()

    try:
        # === ПРИМЕР 1: Отдельные паттерны ===
        logger.info("\n" + "=" * 60)
        logger.info("ПРИМЕР 1: Добавление отдельных паттернов")
        logger.info("=" * 60)

        # Multi-accounting: 5 аккаунтов с одного IP
        result1 = manager.add_fraud_pattern("multi_accounting", n_accounts=5)
        print(f"   Создано аккаунтов: {len(result1['clients'])}")
        print(f"   Shared IP: {result1['shared_ip']}")

        # Wardrobing: 10 случаев
        result2 = manager.add_fraud_pattern("wardrobing", n_cases=10)
        print(f"   Создано случаев: {len(result2['clients'])}")

        # Review manipulation: 20 отзывов с одного IP
        result3 = manager.add_fraud_pattern("review_manipulation", n_reviews=20)
        print(f"   Создано отзывов: {len(result3['clients'])}")

        # === ПРИМЕР 2: Обычные пользователи ===
        logger.info("\n" + "=" * 60)
        logger.info("ПРИМЕР 2: Добавление обычных пользователей")
        logger.info("=" * 60)

        normal1 = manager.add_normal_users("normal", n_users=30)
        print(f"   Обычных покупателей: {len(normal1['clients'])}")

        normal2 = manager.add_normal_users("loyal", n_users=10)
        print(f"   Лояльных клиентов: {len(normal2['clients'])}")

        # === ПРИМЕР 3: Готовый сценарий ===
        logger.info("\n" + "=" * 60)
        logger.info("ПРИМЕР 3: Готовый сценарий 'mixed_traffic'")
        logger.info("=" * 60)

        scenario = manager.add_scenario("mixed_traffic")
        print(f"   Выполнено паттернов: {len(scenario)}")

        # === ПРИМЕР 4: Профессиональный рефандер ===
        logger.info("\n" + "=" * 60)
        logger.info("ПРИМЕР 4: Организованная группа")
        logger.info("=" * 60)

        prof = manager.add_fraud_pattern("professional_refunder", n_accounts=8)
        print(f"   Аккаунтов в группе: {len(prof['clients'])}")
        print(f"   Заказов: {len(prof['orders'])}")

        # === ИТОГИ ===
        logger.info("\n" + "=" * 60)
        logger.info(" ИТОГИ ЗАПОЛНЕНИЯ")
        logger.info("=" * 60)

        cur = manager.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM clients")
        total_clients = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM orders")
        total_orders = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM returns")
        total_returns = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE has_threat = TRUE")
        threat_tickets = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM product_reviews WHERE is_negative = TRUE")
        negative_reviews = cur.fetchone()[0]

        print(f"   Всего клиентов: {total_clients}")
        print(f"   Всего заказов: {total_orders}")
        print(f"   Всего возвратов: {total_returns}")
        print(f"   Тикетов с угрозами: {threat_tickets}")
        print(f"   Негативных отзывов: {negative_reviews}")

        cur.close()

    finally:
        manager.close()
'''
    logger.info("\n🎉 База данных успешно заполнена!")
    logger.info("💡 Проверьте данные: psql -U postgres -d fraud_db -c 'SELECT * FROM clients LIMIT 5;'")

    manager = FraudDBManager()

    # Multi-accounting: 5 аккаунтов с одного IP
    manager.add_fraud_pattern("multi_accounting", n_accounts=5)

    # Wardrobing: 10 случаев возврата одежды
    manager.add_fraud_pattern("wardrobing", n_cases=10)

    # Review blackmail: 5 случаев шантажа
    manager.add_fraud_pattern("review_blackmail", n_cases=5)

    # 30 обычных покупателей
    manager.add_normal_users("normal", n_users=30)

    # 10 лояльных клиентов
    manager.add_normal_users("loyal", n_users=10)

    # Смешанный трафик (фрод + легитим)
    manager.add_scenario("mixed_traffic")

    # Организованная преступная группа
    manager.add_scenario("organized_fraud_ring")

    # Розничный фрод
    manager.add_scenario("retail_fraud")
'''