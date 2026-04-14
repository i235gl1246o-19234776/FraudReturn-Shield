# =============================================================================
# ONNX FEATURE PIPELINE — FRAUDRETURN SHIELD v4.1 (FULL BATCH PROCESSING)
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
import psycopg2
from psycopg2.extras import execute_batch
from contextlib import contextmanager
import sys
import os

warnings.filterwarnings('ignore')

# =============================================================================
# КОНФИГУРАЦИЯ ПОДКЛЮЧЕНИЯ К БД
# =============================================================================
CONN_PARAMS = {
    'host': 'localhost',
    'port': 5432,
    'database': 'postgres',
    'user': 'postgres',
    'password': '1234'
}

# Пути к моделям
ONNX_PATH = "fraud_model_v4_27patterns.onnx"
METADATA_PATH = "metadata_v4_27patterns.json"
ANOMALY_SCALER_PATH = "scaler_v4.pkl"
ANOMALY_MODEL_PATH = "anomaly_model_v4.pkl"


# =============================================================================
# 1. SQL DATA EXTRACTOR (РАБОТА С ВАШЕЙ БД)
# =============================================================================
class DatabaseFeatureExtractor:
    """Извлекает и агрегирует данные из БД: clients + orders + returns"""

    def __init__(self, conn_params: Dict[str, any]):
        self.conn_params = conn_params.copy()
        self.conn_params_log = {k: ('***' if k == 'password' else v)
                                for k, v in self.conn_params.items()}

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для подключения к БД"""
        conn = psycopg2.connect(**self.conn_params)
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
            c.registration_city,

            -- Из таблицы orders
            o.order_id,
            o.order_amount,
            o.items_count,
            o.discount_amount,
            o.payment_method,
            o.order_timestamp,
            o.amount_deviation,
            o.orders_last_30d,
            o.product_category,
            o.is_electronics,
            o.shipping_region,
            o.region_risk_score,
            o.delivery_city,
            o.distance_from_registration_km,
            o.payment_card_bin,
            o.card_issuing_country,
            o.card_country_mismatch,
            o.delivery_address_type,
            o.address_match_score,
            o.is_address_match,

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
            r.claimed_reason,
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
        Получает статистику по IP/устройствам из таблицы client_sessions
        """
        query = """
        SELECT 
            COUNT(DISTINCT ip_address) as unique_ips_90d,
            COUNT(DISTINCT device_id) as unique_devices_90d,
            COUNT(*) as total_sessions_90d,
            COUNT(*) FILTER (WHERE login_timestamp >= NOW() - INTERVAL '24 hours') as sessions_24h,
            COUNT(*) FILTER (WHERE is_emulator = TRUE) as emulator_sessions,
            AVG(CASE WHEN is_new_device THEN 1.0 ELSE 0.0 END) as new_device_ratio
        FROM client_sessions
        WHERE client_id = %s
        AND login_timestamp >= NOW() - INTERVAL '90 days'
        """

        try:
            with self.get_connection() as conn:
                df = pd.read_sql_query(query, conn, params=(client_id,))

            if not df.empty and df.iloc[0]['unique_ips_90d'] is not None:
                row = df.iloc[0]
                return {
                    "ip_velocity_24h": int(row['sessions_24h'] or 0),
                    "ip_velocity_7d": int(row['total_sessions_90d'] or 0),
                    "accounts_per_ip": max(1, int(row['unique_ips_90d'] or 1)),
                    "accounts_per_phone": 1,  # Нет данных о телефонах в сессиях
                    "accounts_per_device": max(1, int(row['unique_devices_90d'] or 1)),
                    "device_is_emulator": 1 if (row['emulator_sessions'] or 0) > 0 else 0,
                    "device_trust_score": float(0.85 if (row['new_device_ratio'] or 0) < 0.5 else 0.65),
                    "ip_trust_score": float(0.80 if (row['sessions_24h'] or 0) < 5 else 0.50)
                }
        except Exception:
            pass

        # Дефолтные значения если таблица не существует или нет данных
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

    CATEGORY_MAP = {
        "Electronics": 0, "Clothing": 1, "Home": 2, "Books": 3, "Toys": 4,
        "Электроника": 0, "Одежда": 1, "Косметика": 2, "Книги": 3, "Спорттовары": 4
    }

    PAYMENT_RISK_MAP = {
        "card": 0.3, "sbp": 0.2, "cash": 0.2, "electronic_wallet": 0.5,
        "crypto": 0.8, "invoice": 0.4
    }

    RETURN_CHANNEL_MAP = {
        "online": 0, "store": 1, "pickup_point": 2, "courier": 3,
        "post": 1, "dropoff": 2
    }

    @staticmethod
    def safe_float(value, default=0.0):
        """Безопасное преобразование в float (обработка Decimal и None)"""
        if value is None:
            return default
        return float(value)

    @staticmethod
    def safe_int(value, default=0):
        """Безопасное преобразование в int"""
        if value is None:
            return default
        return int(value)

    @staticmethod
    def map_transaction_row(row: pd.Series, history_df: pd.DataFrame,
                            ip_device_stats: Dict) -> Dict:
        """Преобразует одну строку из БД в словарь признаков"""

        # === БАЗОВЫЕ ПРИЗНАКИ ИЗ БД ===
        order_amount = DatabaseToModelMapper.safe_float(row.get("order_amount"), 0.0)
        discount_amount = DatabaseToModelMapper.safe_float(row.get("discount_amount"), 0.0)

        features = {
            # Из clients
            "account_age_days": DatabaseToModelMapper.safe_int(row.get("account_age_days"), 0),
            "total_purchases": DatabaseToModelMapper.safe_int(row.get("total_orders"), 0),
            "total_returns": DatabaseToModelMapper.safe_int(row.get("total_returns"), 0),
            "customer_return_rate": DatabaseToModelMapper.safe_float(row.get("global_return_rate"), 0.0),
            "avg_order_amount": DatabaseToModelMapper.safe_float(row.get("avg_order_amount"), 0.0),

            # Из orders
            "order_amount": order_amount,
            "items_in_order": DatabaseToModelMapper.safe_int(row.get("items_count"), 1),
            "discount_percent": (discount_amount / max(order_amount, 1) * 100) if order_amount > 0 else 0.0,
            "payment_method_risk": DatabaseToModelMapper.PAYMENT_RISK_MAP.get(
                row.get("payment_method"), 0.3
            ),
            "amount_deviation": DatabaseToModelMapper.safe_float(row.get("amount_deviation"), 0.0),
            "orders_last_30d": DatabaseToModelMapper.safe_int(row.get("orders_last_30d"), 0),

            # Из returns
            "return_rate_30d": DatabaseToModelMapper.safe_float(row.get("return_rate_last_30d"), 0.0),
            "refund_velocity_30d": DatabaseToModelMapper.safe_int(row.get("returns_last_30d"), 0),
            "days_since_last_return": DatabaseToModelMapper.safe_int(row.get("days_since_last_return"), 999),
            "days_since_purchase": DatabaseToModelMapper.safe_int(row.get("days_since_purchase"), 0),
            "has_receipt": 1 if row.get("has_receipt") else 0,
            "receipt_provided": 1 if row.get("has_receipt") else 0,
            "tags_removed": 1 if row.get("tags_removed") else 0,
            "missing_components": 1 if row.get("missing_components") else 0,
            "return_channel": row.get("return_channel") or "online",

            # Временные признаки
            "order_hour": pd.Timestamp(row.get("order_timestamp")).hour if pd.notna(row.get("order_timestamp")) else 12,
        }

        # === ФЛАГИ ===
        features["high_value_flag"] = int(features["order_amount"] > 30000)
        features["order_time_night"] = int(features["order_hour"] in [0, 1, 2, 3, 4, 5])
        features["fast_return_flag"] = int(features["days_since_purchase"] <= 3)
        features["new_account_flag"] = int(features["account_age_days"] < 7)
        features["first_order_discount_abuse"] = int(
            features["total_purchases"] == 1 and features["discount_percent"] > 20
        )

        # === КАТЕГОРИИ ===
        category = row.get("product_category") or "Electronics"
        features["category"] = category
        features["is_electronics"] = int(row.get("is_electronics", False)) if row.get(
            "is_electronics") is not None else int(category == "Electronics")
        features["claimed_reason"] = row.get("claimed_reason") or "Defective"

        # === IP/DEVICE STATS ===
        features.update(ip_device_stats)

        # === РАСЧЁТНЫЕ ПРИЗНАКИ ===
        features["address_match"] = 1 if row.get("is_address_match", True) else 0
        features["device_new"] = 0  # Будет рассчитано из истории
        features["promo_code_used"] = int(features["discount_percent"] > 0)
        features["weekend_purchase"] = int(
            pd.Timestamp(row.get("order_timestamp")).dayofweek >= 5
            if pd.notna(row.get("order_timestamp")) else 0
        )

        # === ДОПОЛНИТЕЛЬНЫЕ ПРИЗНАКИ ИЗ ИСТОРИИ ===
        if "return_date" in history_df.columns:
            features["refund_velocity_7d"] = len(history_df[
                                                     history_df["return_date"].notna() &
                                                     (history_df["return_date"] >= datetime.now() - timedelta(days=7))
                                                     ])
        else:
            features["refund_velocity_7d"] = 0

        features["support_ticket_count_30d"] = 0
        features["review_count_30d"] = 0
        features["negative_review_cluster"] = 0

        # === RISK SCORES ===
        features["shipping_region_risk"] = min(1.0, max(0.0,
                                                        DatabaseToModelMapper.safe_float(row.get("region_risk_score"),
                                                                                         0.3)
                                                        ))
        features["delivery_address_type"] = row.get("delivery_address_type") or "home"
        features["distance_from_registration_city"] = DatabaseToModelMapper.safe_float(
            row.get("distance_from_registration_km"), 0.0
        )
        features["card_bin_country_mismatch"] = 1 if row.get("card_country_mismatch", False) else 0
        features["chargeback_history_90d"] = 0

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

        # accounts_per_*
        features["accounts_per_ip"] = max(1, features.get("accounts_per_ip", 1))
        features["accounts_per_phone"] = max(1, features.get("accounts_per_phone", 1))
        features["accounts_per_device"] = max(1, features.get("accounts_per_device", 1))

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

    def __init__(self,
                 conn_params: Dict[str, any],
                 onnx_path: str,
                 metadata_path: str,
                 anomaly_scaler_path: str,
                 anomaly_model_path: str):

        # 1. DB Extractor
        self.db_extractor = DatabaseFeatureExtractor(conn_params)

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
        with open(anomaly_scaler_path, 'rb') as f:
            self.anomaly_scaler = joblib.load(f, encoding='bytes')
        with open(anomaly_model_path, 'rb') as f:
            self.anomaly_model = joblib.load(f, encoding='bytes')

        # 5. Preprocessor
        self.preprocessor = ONNXPreprocessor(self.expected_columns, self.categorical_cols)

        print(f"✅ FraudDetectionService инициализирован")
        print(f"   - БД: {self.db_extractor.conn_params_log}")
        print(f"   - ONNX: {len(self.expected_columns)} колонок")
        print(f"   - Anomaly: scaler + IsolationForest загружены")

    def predict_for_return(self, return_id: int) -> Dict:
        """Основной метод: предсказание для конкретного возврата"""
        # 1. Извлечение данных из БД
        tx_df = self.db_extractor.get_transaction_features(
            client_id=0, order_id=0, return_id=return_id
        )
        row = tx_df.iloc[0]
        client_id = int(row["client_id"])

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

        # 7. CatBoost (ONNX) ✅
        raw_output = self.sess.run(None, {self.input_name: X_onnx})[0]
        if raw_output.ndim == 2 and raw_output.shape[1] > 1:
            prob_fraud = float(raw_output[0][1])
        elif raw_output.ndim == 2:
            prob_fraud = float(raw_output[0][0])
        else:
            prob_fraud = float(raw_output.flatten()[0])

        # 8. Isolation Forest (со скалированием!) ✅ ИСПРАВЛЕНО
        scaler_expected = self.anomaly_scaler.n_features_in_
        actual_features = X_onnx.shape[1]

        if actual_features != scaler_expected:
            print(f"⚠️ Shape mismatch: Scaler expects {scaler_expected}, got {actual_features}.")
            print("   Auto-trimming to first {0} features.".format(scaler_expected))
            X_for_scaler = X_onnx[:, :scaler_expected]
        else:
            X_for_scaler = X_onnx

        X_anomaly_scaled = self.anomaly_scaler.transform(X_for_scaler)
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
            "return_id": int(return_id),
            "client_id": int(client_id),
            "order_id": int(row["order_id"]),
            "probability_fraud": round(prob_fraud, 4),
            "anomaly_score": round(float(anomaly_score), 4),
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

    def get_all_return_ids(self, limit: Optional[int] = None) -> List[int]:
        """Получает все return_id из таблицы returns"""
        query = "SELECT return_id FROM returns ORDER BY return_id"
        if limit:
            query += f" LIMIT {int(limit)}"

        with self.db_extractor.get_connection() as conn:
            df = pd.read_sql_query(query, conn)
        return df["return_id"].tolist()

    def batch_predict_all_returns(self,
                                  limit: Optional[int] = None,
                                  batch_size: int = 100,
                                  output_csv: Optional[str] = "fraud_results.csv",
                                  save_to_db: bool = False,
                                  db_result_table: str = "fraud_predictions") -> pd.DataFrame:
        """
        Запускает предсказание для ВСЕХ возвратов в БД
        """
        try:
            from tqdm import tqdm
        except ImportError:
            print("⚠️  tqdm не установлен. Установите: pip install tqdm")
            tqdm = lambda x, **kwargs: x

        # 1. Получаем список всех возвратов
        print("🔍 Получаем список возвратов из БД...")
        return_ids = self.get_all_return_ids(limit=limit)
        total = len(return_ids)
        print(f"✅ Найдено возвратов: {total}")

        if total == 0:
            print("⚠️ Нет данных для обработки")
            return pd.DataFrame()

        # 2. Подготовка результатов
        results = []
        errors = []
        start_time = datetime.now()

        # 3. Пакетная обработка с прогресс-баром
        print("🚀 Запуск предсказаний...\n")

        for i, return_id in enumerate(tqdm(return_ids, desc="Обработка")):
            try:
                result = self.predict_for_return(return_id)
                results.append(result)

                # Периодический коммит в БД (если включено)
                if save_to_db and results and (i + 1) % batch_size == 0:
                    self._save_results_to_db(results[-batch_size:], db_result_table)
                    print(f"   💾 Сохранено {i + 1}/{total} в БД")

            except Exception as e:
                errors.append({"return_id": return_id, "error": str(e)})
                continue

        # 4. Финальное сохранение
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n⏱️  Завершено за {elapsed:.1f} сек ({total / elapsed * 60:.1f} предсказаний/мин)")

        # 5. Конвертация в DataFrame
        df_results = pd.DataFrame(results)

        # 6. Сохранение в CSV
        if output_csv and not df_results.empty:
            df_results.to_csv(output_csv, index=False, encoding='utf-8-sig')
            print(f"📄 Результаты сохранены в: {output_csv}")

        # 7. Сохранение в БД (оставшиеся)
        if save_to_db and results:
            self._save_results_to_db(results, db_result_table)
            print(f"💾 Все результаты сохранены в таблицу: {db_result_table}")

        # 8. Статистика
        self._print_summary(df_results, errors)

        return df_results
    def predict_from_web_payload(self, payload: Dict) -> Dict:
        """
        Принимает данные от сайта (JSON/Dict), дополняет их из БД
        и возвращает предсказание в реальном времени.
        """
        # 1. Извлекаем идентификаторы
        client_id = payload.get("client_id")
        order_id = payload.get("order_id")
        if not client_id or not order_id:
            raise ValueError("Поля client_id и order_id обязательны в payload")

        # 2. Подтягиваем базовые данные клиента и заказа из БД
        baseline_query = """
        SELECT c.client_id, c.account_age_days, c.total_orders, c.total_returns,
               c.global_return_rate, c.avg_order_amount, c.address_change_frequency,
               c.category_returns_count, c.registration_city,
               o.order_id, o.order_amount, o.items_count, o.discount_amount,
               o.payment_method, o.order_timestamp, o.amount_deviation,
               o.orders_last_30d, o.product_category, o.is_electronics,
               o.shipping_region, o.region_risk_score, o.delivery_city,
               o.distance_from_registration_km, o.payment_card_bin,
               o.card_issuing_country, o.card_country_mismatch,
               o.delivery_address_type, o.address_match_score, o.is_address_match
        FROM clients c
        JOIN orders o ON c.client_id = o.client_id
        WHERE c.client_id = %s AND o.order_id = %s
        """
        with self.db_extractor.get_connection() as conn:
            baseline_df = pd.read_sql_query(baseline_query, conn, params=(client_id, order_id))

        # Если заказа/клиента ещё нет в БД (например, pending), используем payload как базу
        if baseline_df.empty:
            merged_row = payload.copy()
        else:
            merged_row = baseline_df.iloc[0].to_dict()
            # Поля от сайта имеют приоритет над БД (покрываем актуальными данными формы)
            merged_row.update(payload)

        # 3. Подтягиваем историю и телеметрию
        history_df = self.db_extractor.get_client_history(client_id, days_back=90)
        ip_device_stats = self.db_extractor.get_ip_device_stats(client_id)

        # 4. Преобразуем в признаки
        features = DatabaseToModelMapper.map_transaction_row(
            pd.Series(merged_row), history_df, ip_device_stats
        )
        features = HiddenFeatureCalculator.calculate_advanced_features(features, history_df)

        # 5. ONNX + Anomaly Pipeline (идентично predict_for_return)
        df_single = pd.DataFrame([features])
        X_onnx = self.preprocessor.transform(df_single)

        raw_output = self.sess.run(None, {self.input_name: X_onnx})[0]
        prob_fraud = float(raw_output[0][1] if raw_output.ndim == 2 and raw_output.shape[1] > 1
                           else raw_output.flatten()[0])

        # Anomaly
        scaler_expected = self.anomaly_scaler.n_features_in_
        X_for_scaler = X_onnx[:, :scaler_expected] if X_onnx.shape[1] != scaler_expected else X_onnx
        X_anomaly_scaled = self.anomaly_scaler.transform(X_for_scaler)
        anomaly_score = float(self.anomaly_model.score_samples(X_anomaly_scaled)[0])
        is_anomaly = anomaly_score < self.anomaly_threshold

        rule_score = self._calculate_rule_score(features)
        anomaly_risk = 1.0 if is_anomaly else 0.2
        combined_score = 0.6 * prob_fraud + 0.25 * rule_score + 0.15 * anomaly_risk

        if combined_score > self.threshold:
            decision = "BLOCK"
        elif combined_score > self.threshold * 0.8:
            decision = "REVIEW"
        else:
            decision = "APPROVE"

        return {
            "return_id": payload.get("return_id", 0),
            "client_id": int(client_id),
            "order_id": int(order_id),
            "probability_fraud": round(prob_fraud, 4),
            "anomaly_score": round(anomaly_score, 4),
            "is_anomaly": bool(is_anomaly),
            "combined_score": round(combined_score, 4),
            "decision": decision,
            "features_used": len(self.expected_columns),
            "timestamp": datetime.now().isoformat()
        }

    def _save_results_to_db(self, results: List[Dict], table_name: str):
        """Сохраняет результаты предсказаний в таблицу БД"""
        if not results:
            return

        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            prediction_id SERIAL PRIMARY KEY,
            return_id INTEGER UNIQUE NOT NULL,
            client_id INTEGER,
            order_id INTEGER,
            probability_fraud DECIMAL(5,4),
            anomaly_score DECIMAL(6,4),
            is_anomaly BOOLEAN,
            combined_score DECIMAL(5,4),
            decision VARCHAR(20),
            features_used INTEGER,
            predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_{table_name}_decision ON {table_name}(decision);
        CREATE INDEX IF NOT EXISTS idx_{table_name}_score ON {table_name}(combined_score DESC);
        """

        with self.db_extractor.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(create_table_sql)

            upsert_sql = f"""
            INSERT INTO {table_name} (
                return_id, client_id, order_id, probability_fraud,
                anomaly_score, is_anomaly, combined_score, decision,
                features_used, predicted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (return_id) DO UPDATE SET
                probability_fraud = EXCLUDED.probability_fraud,
                combined_score = EXCLUDED.combined_score,
                decision = EXCLUDED.decision,
                predicted_at = CURRENT_TIMESTAMP
            """

            batch_data = [
                (
                    r["return_id"], r["client_id"], r["order_id"],
                    r["probability_fraud"], r["anomaly_score"],
                    r["is_anomaly"], r["combined_score"], r["decision"],
                    r["features_used"], r["timestamp"]
                )
                for r in results
            ]

            execute_batch(cur, upsert_sql, batch_data)
            conn.commit()
            cur.close()

    def _print_summary(self, df: pd.DataFrame, errors: List[Dict]):
        """Выводит статистику по результатам и ошибки (если есть)"""
        print("\n" + "=" * 60)
        print("📊 СТАТИСТИКА ПРЕДСКАЗАНИЙ")
        print("=" * 60)

        # === СЦЕНАРИЙ 1: Ничего не обработалось ===
        if df.empty:
            print("❌ Нет успешных предсказаний (DataFrame пуст)")

            if errors:
                print(f"\n💥 СКРИПТ УПАЛ НА ВСЕХ ЗАПИСЯХ ({len(errors)} ошибок)")
                print("🔍 Детали первой ошибки:")
                err = errors[0]
                print(f"   🆔 return_id: {err.get('return_id', 'N/A')}")
                print(f"   📝 Причина: {err.get('error', 'Неизвестная ошибка')}")
                print("\n💡 РЕКОМЕНДАЦИЯ:")
                print("   1. Проверьте SQL-запрос get_transaction_features (возможно, JOIN не работает).")
                print("   2. Проверьте типы данных в БД (Decimal vs Float).")
                print("   3. Убедитесь, что в таблице returns есть данные.")
            else:
                print("⚠️ Список ошибок пуст. Возможно, таблица returns пуста.")

            print("=" * 60)
            return

        # === СЦЕНАРИЙ 2: Успешная обработка (полная или частичная) ===
        total_success = len(df)
        total_errors = len(errors)
        total_all = total_success + total_errors

        print(f"✅ Успешно обработано: {total_success}")
        if total_errors > 0:
            print(f"⚠️ Ошибок при обработке: {total_errors}")

        # 1. Распределение решений
        print(f"\n🎯 Распределение решений:")
        decisions = df["decision"].value_counts()
        for decision, count in decisions.items():
            pct = (count / total_success) * 100
            bar = "█" * int(pct / 3)
            print(f"   {decision:8} | {bar} {count:4} ({pct:5.1f}%)")

        # 2. Статистика по скорам
        print(f"\n📈 Статистика combined_score:")
        print(f"   Средний: {df['combined_score'].mean():.3f}")
        print(f"   Медиана: {df['combined_score'].median():.3f}")
        print(f"   Мин/Макс: {df['combined_score'].min():.3f} / {df['combined_score'].max():.3f}")

        # 3. Топ подозрительных
        print(f"\n🔥 ТОП-5 самых подозрительных возвратов:")
        try:
            top_fraud = df.nlargest(5, "combined_score")
            for _, row in top_fraud.iterrows():
                flag = "⚠️ " if row["decision"] != "APPROVE" else "✓ "
                print(f"   {flag} ID: {row['return_id']:5} | Score: {row['combined_score']:.3f} | {row['decision']}")
        except Exception:
            print("   (Не удалось построить рейтинг)")

        # 4. Вывод примеров ошибок (если были успешные, но часть упала)
        if errors:
            print(f"\n❌ Примеры ошибок (первые 3):")
            for err in errors[:3]:
                print(f"   • return_id={err['return_id']}: {str(err['error'])[:60]}...")

        print("=" * 60)


# =============================================================================
# 6. MAIN
# =============================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("🛡️  FRAUD RETURN SHIELD v4.1 - MASS FRAUD DETECTION")
    print("=" * 70)

    # Тест подключения к БД
    try:
        print("\n🔌 Проверка подключения к БД...")
        with psycopg2.connect(**CONN_PARAMS) as test_conn:
            with test_conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
                print(f"✅ Подключение успешно: {version[:60]}...")

                # Проверка наличия таблиц
                cur.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('clients', 'orders', 'returns')
                """)
                tables = [t[0] for t in cur.fetchall()]
                if len(tables) == 3:
                    print(f"✅ Все необходимые таблицы найдены: {', '.join(tables)}")
                else:
                    print(f"⚠️  Найдены таблицы: {', '.join(tables)}")
                    print(f"⚠️  Отсутствуют: {set(['clients', 'orders', 'returns']) - set(tables)}")
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        print("   Проверьте CONN_PARAMS (host, port, database, user, password)")
        sys.exit(1)

    # Проверка наличия файлов моделей
    print("\n📁 Проверка файлов моделей...")
    required_files = [
        ONNX_PATH,
        METADATA_PATH,
        ANOMALY_SCALER_PATH,
        ANOMALY_MODEL_PATH
    ]

    missing_files = [f for f in required_files if not os.path.exists(f)]
    if missing_files:
        print(f"❌ Отсутствуют файлы моделей:")
        for f in missing_files:
            print(f"   • {f}")
        print("\n💡 Убедитесь, что модели обучены и сохранены в папке models4/")
        sys.exit(1)
    else:
        print("✅ Все файлы моделей найдены")

    # Инициализация сервиса
    print("\n" + "=" * 70)
    print("🚀 ИНИЦИАЛИЗАЦИЯ СЕРВИСА")
    print("=" * 70)

    try:
        service = FraudDetectionService(
            conn_params=CONN_PARAMS,
            onnx_path=ONNX_PATH,
            metadata_path=METADATA_PATH,
            anomaly_scaler_path=ANOMALY_SCALER_PATH,
            anomaly_model_path=ANOMALY_MODEL_PATH
        )
    except Exception as e:
        print(f"❌ Ошибка инициализации сервиса: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # ПАКЕТНАЯ ПРОВЕРКА ВСЕХ ВОЗВРАТОВ
    print("\n" + "=" * 70)
    print("🎯 ЗАПУСК МАССОВОЙ ПРОВЕРКИ")
    print("=" * 70)

    try:
        df_results = service.batch_predict_all_returns(
            limit=None,  # None = все возвраты
            batch_size=100,  # коммит в БД каждые 100 записей
            output_csv="fraud_results.csv",  # сохранить в CSV
            save_to_db=True,  # сохранить результаты в БД
            db_result_table="fraud_predictions"
        )

        # Итоговый вывод
        if not df_results.empty:
            print("\n" + "=" * 70)
            print("✅ ОБРАБОТКА ЗАВЕРШЕНА")
            print("=" * 70)
            print(f"\n📊 ИТОГИ:")
            print(f"   🚫 Заблокировано: {len(df_results[df_results['decision'] == 'BLOCK'])}")
            print(f"   ⚠️  На проверке: {len(df_results[df_results['decision'] == 'REVIEW'])}")
            print(f"   ✅ Одобрено: {len(df_results[df_results['decision'] == 'APPROVE'])}")
            print(f"   📄 Результаты: fraud_results.csv")
            print(f"   🗄️  БД: таблица fraud_predictions")

            # ТОП-5 самых подозрительных
            print(f"\n🔥 ТОП-5 по combined_score:")
            top = df_results.nlargest(5, "combined_score")
            for _, row in top.iterrows():
                print(f"   return_id={row['return_id']:5} | score={row['combined_score']:.3f} | {row['decision']}")
        else:
            print("\n⚠️  Нет результатов для отображения")

    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 70)
    print(" Работа завершена")
    print("=" * 70)