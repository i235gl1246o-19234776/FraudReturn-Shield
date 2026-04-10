# =============================================================================
# ONNX FEATURE PIPELINE — FRAUDRETURN SHIELD v4.1 (ONE-HOT + SQL БД)
# Работает с таблицами: clients, orders, returns
# =============================================================================

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import onnxruntime as rt
import joblib
import warnings
import json
import psycopg2  # или pymysql для MySQL
from contextlib import contextmanager

warnings.filterwarnings('ignore')


# =============================================================================
# 1. SQL DATA EXTRACTOR (РАБОТА С ВАШЕЙ БД)
# =============================================================================
class DatabaseFeatureExtractor:
    """Извлекает и агрегирует данные из БД: clients + orders + returns"""

    def __init__(self, db_connection_string: str):
        self.conn_string = db_connection_string

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для подключения к БД"""
        conn = psycopg2.connect(self.conn_string)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_transaction_features(self, client_id: int, order_id: int, return_id: int) -> pd.DataFrame:
        """
        Извлекает все признаки для конкретной транзакции возврата
        JOIN: returns → orders → clients
        """
        query = """
        SELECT 
            -- Из таблицы clients
            c.client_id,
            c.account_age_days,
            c.total_orders,
            c.total_returns,
            c.global_return_rate,
            c.avg_order_amount,
            c.address_change_frequency,
            c.category_returns_count,

            -- Из таблицы orders
            o.order_id,
            o.order_amount,
            o.items_count,
            o.discount_amount,
            o.payment_method,
            o.order_timestamp,
            o.amount_deviation,
            o.orders_last_30d,

            -- Из таблицы returns
            r.return_id,
            r.returns_last_30d,
            r.return_rate_last_30d,
            r.days_since_last_return,
            r.days_since_purchase,
            r.return_channel,
            r.has_receipt,
            r.tags_removed,
            r.missing_components,
            r.created_at as return_created_at

        FROM returns r
        JOIN orders o ON r.order_id = o.order_id
        JOIN clients c ON r.client_id = c.client_id

        WHERE r.return_id = %s
        """

        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(return_id,))

        if df.empty:
            raise ValueError(f"Возврат return_id={return_id} не найден в БД")

        return df

    def get_client_history(self, client_id: int, days_back: int = 90) -> pd.DataFrame:
        """
        Получает историю клиента за последние N дней для расчёта поведенческих признаков
        """
        query = """
        SELECT 
            o.order_id,
            o.order_timestamp,
            o.order_amount,
            o.payment_method,
            r.return_id,
            r.created_at as return_date,
            r.return_channel
        FROM orders o
        LEFT JOIN returns r ON o.order_id = r.order_id
        WHERE o.client_id = %s
        AND o.order_timestamp >= NOW() - INTERVAL '%s days'
        ORDER BY o.order_timestamp DESC
        """

        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(client_id, days_back))

        return df

    def get_ip_device_stats(self, client_id: int) -> Dict:
        """
        Получает статистику по IP/устройствам (если есть отдельные таблицы)
        Если нет — возвращает дефолтные значения
        """
        # Заглушка: в вашей схеме нет таблиц с IP/devices
        # В продакшене здесь будет JOIN с client_sessions или device_fingerprints
        return {
            "ip_velocity_24h": 0,
            "ip_velocity_7d": 0,
            "accounts_per_ip": 1,
            "accounts_per_phone": 1,
            "accounts_per_device": 1,
            "device_is_emulator": 0,
            "device_trust_score": 0.85,
            "ip_trust_score": 0.80
        }


# =============================================================================
# 2. MAPPER: БД → МОДЕЛЬНЫЕ ПРИЗНАКИ
# =============================================================================
class DatabaseToModelMapper:
    """Преобразует сырые данные из БД в признаки для модели"""

    # Маппинг категорий (из обучающей выборки)
    CATEGORY_MAP = {
        "Электроника": 0, "Одежда": 1, "Косметика": 2, "Книги": 3, "Спорттовары": 4
    }

    PAYMENT_RISK_MAP = {
        "card": 0.3, "cash": 0.2, "electronic_wallet": 0.5,
        "crypto": 0.8, "invoice": 0.4
    }

    RETURN_CHANNEL_MAP = {
        "online": 0, "store": 1, "pickup_point": 2, "courier": 3
    }

    @staticmethod
    def map_transaction_row(row: pd.Series, history_df: pd.DataFrame,
                            ip_device_stats: Dict) -> Dict:
        """Преобразует одну строку из БД в словарь признаков"""

        # === БАЗОВЫЕ ПРИЗНАКИ ИЗ БД ===
        features = {
            # Из clients
            "account_age_days": int(row["account_age_days"] or 0),
            "total_purchases": int(row["total_orders"] or 0),
            "total_returns": int(row["total_returns"] or 0),
            "customer_return_rate": float(row["global_return_rate"] or 0.0),
            "avg_order_amount": float(row["avg_order_amount"] or 0.0),

            # Из orders
            "order_amount": float(row["order_amount"] or 0.0),
            "items_in_order": int(row["items_count"] or 1),
            "discount_percent": float(row["discount_amount"] or 0.0) / max(float(row["order_amount"] or 1), 1) * 100,
            "payment_method_risk": DatabaseToModelMapper.PAYMENT_RISK_MAP.get(
                row["payment_method"], 0.3
            ),
            "amount_deviation": float(row["amount_deviation"] or 0.0),
            "orders_last_30d": int(row["orders_last_30d"] or 0),

            # Из returns
            "return_rate_30d": float(row["return_rate_last_30d"] or 0.0),
            "refund_velocity_30d": int(row["returns_last_30d"] or 0),
            "days_since_last_return": int(row["days_since_last_return"] or 999),
            "days_since_purchase": int(row["days_since_purchase"] or 0),
            "has_receipt": int(row["has_receipt"]) if row["has_receipt"] is not None else 1,
            "receipt_provided": int(row["has_receipt"]) if row["has_receipt"] is not None else 1,
            "tags_removed": int(row["tags_removed"]) if row["tags_removed"] is not None else 0,
            "missing_components": int(row["missing_components"]) if row["missing_components"] is not None else 0,
            "return_channel": row["return_channel"] or "online",

            # Временные признаки
            "order_hour": pd.Timestamp(row["order_timestamp"]).hour if pd.notna(row["order_timestamp"]) else 12,
        }

        # === ФЛАГИ ===
        features["high_value_flag"] = int(features["order_amount"] > 30000)
        features["order_time_night"] = int(features["order_hour"] in [0, 1, 2, 3, 4, 5])
        features["fast_return_flag"] = int(features["days_since_purchase"] <= 3)
        features["new_account_flag"] = int(features["account_age_days"] < 7)
        features["first_order_discount_abuse"] = int(
            features["total_purchases"] == 1 and features["discount_percent"] > 20
        )

        # === КАТЕГОРИИ (требуют отдельной логики) ===
        # В вашей БД нет категории товара в returns/orders
        # Нужно либо добавить, либо брать из products таблицы
        features["category"] = "Электроника"  # Заглушка
        features["is_electronics"] = int(features["category"] == "Электроника")
        features["claimed_reason"] = "Брак"  # Заглушка

        # === IP/DEVICE STATS (из отдельного метода) ===
        features.update(ip_device_stats)

        # === РАСЧЁТНЫЕ ПРИЗНАКИ ===
        features["address_match"] = 1  # Заглушка (нет данных в схеме)
        features["device_new"] = 0  # Заглушка
        features["promo_code_used"] = int(features["discount_percent"] > 0)
        features["weekend_purchase"] = int(
            pd.Timestamp(row["order_timestamp"]).dayofweek >= 5
            if pd.notna(row["order_timestamp"]) else 0
        )

        # === ДОПОЛНИТЕЛЬНЫЕ ПРИЗНАКИ ИЗ ИСТОРИИ ===
        features["refund_velocity_7d"] = len(history_df[
                                                 history_df["return_date"].notna() &
                                                 (history_df["return_date"] >= datetime.now() - timedelta(days=7))
                                                 ])

        features["support_ticket_count_30d"] = 0  # Нет таблицы tickets
        features["review_count_30d"] = 0  # Нет таблицы reviews
        features["negative_review_cluster"] = 0

        # === RISK SCORES ===
        features["shipping_region_risk"] = 0.3  # Заглушка
        features["delivery_address_type"] = "pickup_point" if features["return_channel"] == "pickup_point" else "home"
        features["distance_from_registration_city"] = 0  # Нет данных
        features["card_bin_country_mismatch"] = 0  # Нет данных о карте
        features["chargeback_history_90d"] = 0  # Нет таблицы chargebacks

        # === THREAT DETECTION ===
        features["threat_language_detected"] = 0
        features["legal_claim_threat"] = 0

        return features


# =============================================================================
# 3. СКРЫТЫЕ ПРИЗНАКИ (ДОПОЛНИТЕЛЬНЫЕ РАСЧЁТЫ)
# =============================================================================
class HiddenFeatureCalculator:
    """Рассчитывает сложные агрегированные признаки"""

    @staticmethod
    def calculate_advanced_features(features: Dict, history_df: pd.DataFrame) -> Dict:
        """Дополнительные расчёты на основе истории"""

        # ip_velocity (если есть данные)
        if "ip_address" in history_df.columns:
            now = datetime.now()
            ip = features.get("current_ip")
            if ip:
                ip_history = history_df[history_df["ip_address"] == ip]
                features["ip_velocity_24h"] = len(ip_history[
                                                      ip_history["order_timestamp"] >= now - timedelta(hours=24)
                                                      ])
                features["ip_velocity_7d"] = len(ip_history[
                                                     ip_history["order_timestamp"] >= now - timedelta(days=7)
                                                     ])

        # accounts_per_* (если есть данные о shared devices)
        features["accounts_per_ip"] = features.get("accounts_per_ip", 1)
        features["accounts_per_phone"] = features.get("accounts_per_phone", 1)
        features["accounts_per_device"] = features.get("accounts_per_device", 1)

        # trust scores
        features["device_trust_score"] = max(0.0, min(1.0,
                                                      0.9 - features.get("device_is_emulator", 0) * 0.4
                                                      - (features["accounts_per_device"] > 3) * 0.2
                                                      ))

        features["ip_trust_score"] = max(0.0, min(1.0,
                                                  0.85 - (features["ip_velocity_24h"] > 10) * 0.3
                                                  - (features["accounts_per_phone"] > 3) * 0.15
                                                  ))

        return features


# =============================================================================
# 4. ONNX PREPROCESSOR (OHE + СКАЛИРОВАНИЕ)
# =============================================================================
class ONNXPreprocessor:
    def __init__(self, expected_columns: List[str], categorical_cols: List[str]):
        self.expected_columns = expected_columns
        self.categorical_cols = categorical_cols

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if df.empty:
            raise ValueError("Empty DataFrame")

        df_out = df.copy()

        # One-Hot Encoding
        if self.categorical_cols:
            df_cats = df_out[self.categorical_cols].astype(str)
            dummies = pd.get_dummies(df_cats, prefix_sep='__')
            df_out = pd.concat([df_out.drop(columns=self.categorical_cols), dummies], axis=1)

        # Выравнивание колонок
        for col in self.expected_columns:
            if col not in df_out.columns:
                df_out[col] = 0
        df_aligned = df_out[self.expected_columns]

        # Клиппинг
        for col in ["order_hour"]:
            if col in df_aligned.columns:
                df_aligned[col] = df_aligned[col].clip(0, 23)
        for col in ["shipping_region_risk", "device_trust_score", "ip_trust_score",
                    "customer_return_rate", "return_rate_30d"]:
            if col in df_aligned.columns:
                df_aligned[col] = df_aligned[col].clip(0.0, 1.0)

        return df_aligned.fillna(0).astype(np.float32).values


# =============================================================================
# 5. ОСНОВНОЙ СЕРВИС (END-TO-END PIPELINE)
# =============================================================================
class FraudDetectionService:
    """Полный пайплайн: БД → признаки → ONNX → решение"""

    def __init__(self, db_connection_string: str, onnx_path: str,
                 metadata_path: str, anomaly_scaler_path: str,
                 anomaly_model_path: str):

        # 1. DB Extractor
        self.db_extractor = DatabaseFeatureExtractor(db_connection_string)

        # 2. ONNX Model
        self.sess = rt.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name

        # 3. Metadata
        with open(metadata_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        self.expected_columns = meta['feature_columns']
        self.categorical_cols = meta.get('categorical_features', [])
        self.threshold = meta.get('optimal_threshold', 0.65)
        self.anomaly_threshold = meta.get('anomaly_threshold', -0.1)

        # 4. Anomaly Detection
        self.anomaly_scaler = joblib.load(anomaly_scaler_path)
        self.anomaly_model = joblib.load(anomaly_model_path)

        # 5. Preprocessor
        self.preprocessor = ONNXPreprocessor(self.expected_columns, self.categorical_cols)

        print(f"✅ FraudDetectionService инициализирован")
        print(f"   - БД: подключено")
        print(f"   - ONNX: {len(self.expected_columns)} колонок")
        print(f"   - Anomaly: scaler + IsolationForest загружены")

    def predict_for_return(self, return_id: int) -> Dict:
        """
        Основной метод: предсказание для конкретного возврата
        """
        # 1. Извлечение данных из БД
        tx_df = self.db_extractor.get_transaction_features(
            client_id=0,  # будет извлечён из БД
            order_id=0,
            return_id=return_id
        )
        row = tx_df.iloc[0]
        client_id = row["client_id"]

        # 2. История клиента
        history_df = self.db_extractor.get_client_history(client_id, days_back=90)

        # 3. IP/Device stats
        ip_device_stats = self.db_extractor.get_ip_device_stats(client_id)

        # 4. Маппинг БД → признаки
        features = DatabaseToModelMapper.map_transaction_row(
            row, history_df, ip_device_stats
        )

        # 5. Дополнительные расчёты
        features = HiddenFeatureCalculator.calculate_advanced_features(
            features, history_df
        )

        # 6. Подготовка к ONNX
        df_single = pd.DataFrame([features])
        X_onnx = self.preprocessor.transform(df_single)

        # 7. CatBoost (ONNX)
        catboost_pred = self.sess.run(None, {self.input_name: X_onnx})[0]
        prob_fraud = float(catboost_pred[0][1])

        # 8. Isolation Forest (со скалированием!)
        X_anomaly_scaled = self.anomaly_scaler.transform(X_onnx)
        anomaly_score = self.anomaly_model.score_samples(X_anomaly_scaled)[0]
        is_anomaly = anomaly_score < self.anomaly_threshold

        # 9. Rule-based score
        rule_score = self._calculate_rule_score(features)

        # 10. Combined score
        anomaly_risk = 1.0 if is_anomaly else 0.2
        combined_score = 0.6 * prob_fraud + 0.25 * rule_score + 0.15 * anomaly_risk

        # 11. Решение
        if combined_score > self.threshold:
            decision = "BLOCK"
        elif combined_score > self.threshold * 0.8:
            decision = "REVIEW"
        else:
            decision = "APPROVE"

        return {
            "return_id": return_id,
            "client_id": client_id,
            "order_id": int(row["order_id"]),
            "probability_fraud": round(prob_fraud, 4),
            "anomaly_score": round(anomaly_score, 4),
            "is_anomaly": bool(is_anomaly),
            "combined_score": round(combined_score, 4),
            "decision": decision,
            "features_used": len(self.expected_columns),
            "timestamp": datetime.now().isoformat()
        }

    def _calculate_rule_score(self, features: Dict) -> float:
        score = 0.0
        if features.get("account_age_days", 365) < 7: score += 0.15
        if features.get("order_amount", 0) > 30000: score += 0.10
        if features.get("fast_return_flag", 0) == 1: score += 0.12
        if features.get("missing_components", 0) == 1: score += 0.20
        if features.get("tags_removed", 0) == 1: score += 0.18
        if features.get("accounts_per_ip", 1) >= 3: score += 0.15
        if features.get("device_is_emulator", 0) == 1: score += 0.12
        return min(score, 1.0)


# =============================================================================
# 6. ПРИМЕР ИСПОЛЬЗОВАНИЯ
# =============================================================================
if __name__ == "__main__":
    # Конфигурация
    DB_CONNECTION = "postgresql://user:pass@localhost:5432/fraud_db"
    ONNX_PATH = "models4/fraud_model_v4_27patterns.onnx"
    METADATA_PATH = "models4/metadata_v4_27patterns.json"
    ANOMALY_SCALER_PATH = "models4/scaler_v4.pkl"
    ANOMALY_MODEL_PATH = "models4/anomaly_model_v4.pkl"

    # Инициализация сервиса
    service = FraudDetectionService(
        db_connection_string=DB_CONNECTION,
        onnx_path=ONNX_PATH,
        metadata_path=METADATA_PATH,
        anomaly_scaler_path=ANOMALY_SCALER_PATH,
        anomaly_model_path=ANOMALY_MODEL_PATH
    )

    # Предсказание для возврата
    return_id = 12345
    result = service.predict_for_return(return_id)

    print(f"\n🎯 Результат для return_id={return_id}:")
    print(f"   Вероятность фрода: {result['probability_fraud']:.2%}")
    print(f"   Аномалия: {result['is_anomaly']}")
    print(f"   Решение: {result['decision']}")
    print(f"   Combined score: {result['combined_score']:.3f}")