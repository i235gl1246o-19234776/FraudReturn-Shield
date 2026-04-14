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
import sys
import os

warnings.filterwarnings('ignore')

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
CONN_PARAMS = {
    'host': 'localhost',
    'port': 5432,
    'database': 'fraud_return_db',
    'user': 'postgres',
    'password': 'OmegaBloody13'
}

# Пути к артефактам модели (должны совпадать с теми, что сохранил HybridFraudDetector)
MODEL_DIR = "models4/"
ONNX_PATH = os.path.join(MODEL_DIR, "fraud_model_v4_27patterns.onnx")
METADATA_PATH = os.path.join(MODEL_DIR, "metadata_v4_27patterns.json")
ANOMALY_SCALER_PATH = os.path.join(MODEL_DIR, "scaler_v4.pkl")
ANOMALY_MODEL_PATH = os.path.join(MODEL_DIR, "anomaly_model_v4.pkl")
OHE_ENCODER_PATH = os.path.join(MODEL_DIR, "ohe_encoder_v4.pkl")

def get_connection():
    """Подключение к PostgreSQL с UTF-8"""
    conn = psycopg2.connect(**CONN_PARAMS)
    conn.set_client_encoding('UTF8')
    return conn

# =============================================================================
# 1. SQL DATA EXTRACTORS (Агрегация данных из БД)
# =============================================================================

def get_transaction_features(return_id: int) -> pd.DataFrame:
    """Извлекает полный контекст возврата: Клиент + Заказ + Возврат"""
    query = """
    SELECT 
        c.client_id, c.account_age_days, c.total_orders, c.total_returns,
        c.global_return_rate, c.avg_order_amount, c.registration_city,
        
        o.order_id, o.order_amount, o.items_count, o.discount_amount,
        o.payment_method, o.order_timestamp, o.amount_deviation,
        o.orders_last_30d, o.product_category, o.is_electronics,
        o.shipping_region, o.region_risk_score, o.distance_from_registration_km,
        o.card_country_mismatch, o.delivery_address_type, o.is_address_match,
        
        r.return_id, r.returns_last_30d, r.return_rate_last_30d,
        r.days_since_last_return, r.days_since_purchase, r.return_channel,
        r.has_receipt, r.tags_removed, r.missing_components, r.claimed_reason,
        r.created_at as return_created_at
    FROM returns r
    JOIN orders o ON r.order_id = o.order_id
    JOIN clients c ON r.client_id = c.client_id
    WHERE r.return_id = %s
    """
    conn = get_connection()
    try:
        df = pd.read_sql_query(query, conn, params=(return_id,))
        if df.empty:
            raise ValueError(f"Return ID {return_id} not found")
        return df
    finally:
        conn.close()

def get_client_history(client_id: int, days_back: int = 90) -> pd.DataFrame:
    """История заказов и возвратов для расчета velocity-признаков"""
    query = """
    SELECT o.order_id, o.order_timestamp, r.return_id, r.created_at as return_date
    FROM orders o
    LEFT JOIN returns r ON o.order_id = r.order_id
    WHERE o.client_id = %s AND o.order_timestamp >= NOW() - INTERVAL '%s days'
    ORDER BY o.order_timestamp DESC
    """
    conn = get_connection()
    try:
        return pd.read_sql_query(query, conn, params=(client_id, days_back))
    finally:
        conn.close()

def get_ip_device_stats(client_id: int) -> Dict:
    """Статистика по устройствам и IP (защита от мультиаккаунтинга)"""
    query = """
    SELECT 
        COUNT(DISTINCT ip_address) as unique_ips_90d,
        COUNT(DISTINCT device_id) as unique_devices_90d,
        COUNT(*) FILTER (WHERE login_timestamp >= NOW() - INTERVAL '24 hours') as sessions_24h,
        COUNT(*) FILTER (WHERE is_emulator = TRUE) as emulator_sessions
    FROM client_sessions
    WHERE client_id = %s AND login_timestamp >= NOW() - INTERVAL '90 days'
    """
    try:
        conn = get_connection()
        try:
            df = pd.read_sql_query(query, conn, params=(client_id,))
        finally:
            conn.close()
            
        if not df.empty and df.iloc[0]['unique_ips_90d'] is not None:
            row = df.iloc[0]
            return {
                "ip_velocity_24h": int(row['sessions_24h'] or 0),
                "accounts_per_ip": max(1, int(row['unique_ips_90d'] or 1)),
                "accounts_per_device": max(1, int(row['unique_devices_90d'] or 1)),
                "device_is_emulator": 1 if (row['emulator_sessions'] or 0) > 0 else 0,
                # Дефолтные трасты, если нет детальной логики
                "device_trust_score": 0.9, 
                "ip_trust_score": 0.9
            }
    except Exception:
        pass
    
    # Fallback значения
    return {
        "ip_velocity_24h": 0, "accounts_per_ip": 1, "accounts_per_device": 1,
        "device_is_emulator": 0, "device_trust_score": 0.9, "ip_trust_score": 0.9
    }

# =============================================================================
# 2. PRODUCTION FEATURE MAPPER (Гарантия целостности признаков)
# =============================================================================
class ProductionFeatureMapper:
    """
    Гарантирует, что на вход модели попадет ПОЛНЫЙ набор признаков.
    Если поле отсутствует в БД, ставится безопасный дефолт.
    """
    
    # Полный список числовых признаков с дефолтами (безопасными для "легитимного" пользователя)
    NUMERIC_DEFAULTS = {
        'account_age_days': 365, 'total_purchases': 1, 'total_returns': 0,
        'customer_return_rate': 0.0, 'order_amount': 1000.0, 'items_in_order': 1,
        'discount_percent': 0.0, 'payment_method_risk': 0.3, 'amount_deviation': 0.0,
        'orders_last_30d': 0, 'return_rate_30d': 0.0, 'refund_velocity_30d': 0,
        'days_since_last_return': 999, 'days_since_purchase': 14, 'has_receipt': 1,
        'receipt_provided': 1, 'tags_removed': 0, 'missing_components': 0,
        'order_hour': 12, 'high_value_flag': 0, 'order_time_night': 0,
        'fast_return_flag': 0, 'new_account_flag': 0, 'first_order_discount_abuse': 0,
        'is_electronics': 0, 'address_match': 1, 'device_new': 0,
        'promo_code_used': 0, 'weekend_purchase': 0, 'refund_velocity_7d': 0,
        'support_ticket_count_30d': 0, 'review_count_30d': 0,
        'negative_review_cluster': 0, 'shipping_region_risk': 0.3,
        'distance_from_registration_city': 50.0, 'card_bin_country_mismatch': 0,
        'chargeback_history_90d': 0, 'threat_language_detected': 0,
        'legal_claim_threat': 0, 'ip_velocity_24h': 1, 'ip_velocity_7d': 5,
        'accounts_per_ip': 1, 'accounts_per_phone': 1, 'accounts_per_device': 1,
        'device_is_emulator': 0, 'device_trust_score': 0.9, 'ip_trust_score': 0.9,
        'avg_order_amount': 1000.0, 
        # Специфические признаки паттернов фрода
        'wear_evidence_detected': 0, 'brand_mismatch': 0, 'category_mismatch': 0, 
        'event_season_flag': 0, 'mass_tryon_flag': 0, 'order_bracketing_ratio': 0.0,
        'package_weight_vs_expected': 0.0, 'xray_scan_anomaly': 0,
        'empty_box_claim_count': 0, 'package_density_score': 1.0,
        'review_text_similarity_score': 0.0, 'same_address_different_accounts': 0,
        'rma_reuse_count': 0, 'cross_channel_return': 0, 'duplicate_refund_30d': 0,
        'same_item_burst': 0, 'holiday_season_return': 0
    }

    CATEGORICAL_DEFAULTS = {
        'category': 'Электроника',
        'claimed_reason': 'Передумал',
        'delivery_address_type': 'home'
    }

    @staticmethod
    def normalize(raw_data: Dict, history_stats: Dict = None, ip_stats: Dict = None) -> Dict:
        if history_stats is None: history_stats = {}
        if ip_stats is None: ip_stats = {}

        features = {}
        sources = [raw_data, history_stats, ip_stats]
        
        # 1. Заполнение числовых признаков
        for key, default_val in ProductionFeatureMapper.NUMERIC_DEFAULTS.items():
            val = default_val
            for src in sources:
                if key in src and src[key] is not None:
                    val = src[key]
                    break
            try:
                features[key] = float(val)
            except (ValueError, TypeError):
                features[key] = float(default_val)

        # 2. Заполнение категориальных признаков
        for key, default_val in ProductionFeatureMapper.CATEGORICAL_DEFAULTS.items():
            val = default_val
            for src in sources:
                if key in src and src[key] is not None:
                    val = src[key]
                    break
            features[key] = str(val)
            
        # 3. Пересчет зависимых флагов (на случай, если сырые данные изменились)
        order_amount = features.get('order_amount', 0)
        features['high_value_flag'] = 1.0 if order_amount > 30000 else 0.0
        
        order_hour = features.get('order_hour', 12)
        features['order_time_night'] = 1.0 if int(order_hour) in [0,1,2,3,4,5] else 0.0
        
        days_since_purchase = features.get('days_since_purchase', 14)
        features['fast_return_flag'] = 1.0 if days_since_purchase <= 3 else 0.0
        
        account_age = features.get('account_age_days', 365)
        features['new_account_flag'] = 1.0 if account_age < 7 else 0.0

        return features

# =============================================================================
# 3. ONNX PREPROCESSOR & SERVICE
# =============================================================================
class OnnxFraudService:
    def __init__(self):
        self._load_models()
        print("✅ OnnxFraudService initialized")

    def _load_models(self):
        # 1. Metadata
        with open(METADATA_PATH, 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)
        self.feature_columns = self.metadata['feature_columns']
        self.threshold = self.metadata.get('optimal_threshold', 0.65)
        self.anomaly_threshold = self.metadata.get('anomaly_threshold', -0.1)

        # 2. ONNX Model
        self.session = rt.InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

        # 3. OHE Encoder (Критически важен для согласованности колонок)
        self.encoder = joblib.load(OHE_ENCODER_PATH)

        # 4. Anomaly Detection
        self.scaler = joblib.load(ANOMALY_SCALER_PATH)
        self.iforest = joblib.load(ANOMALY_MODEL_PATH)

    def _prepare_vector(self, features_dict: Dict) -> np.ndarray:
        """
        Единый метод подготовки вектора:
        Dict -> OHE -> Strict Order Array -> Float32
        """
        # 1. OHE Transform (гарантирует наличие всех dummy-колонок)
        encoded_dict = self.encoder.transform_single_dict(features_dict)
        
        # 2. Strict Ordering (строгий порядок как при обучении)
        input_vector = []
        for col in self.feature_columns:
            val = encoded_dict.get(col, 0.0)
            input_vector.append(float(val))
            
        return np.array([input_vector], dtype=np.float32)

    def predict_for_return(self, return_id: int) -> Dict:
        """Пакетный режим: данные полностью из БД"""
        # 1. Extract
        tx_df = get_transaction_features(return_id)
        row = tx_df.iloc[0]
        client_id = int(row["client_id"])
        
        history_df = get_client_history(client_id)
        ip_stats = get_ip_device_stats(client_id)
        
        # 2. Map & Normalize
        raw_dict = row.to_dict()
        # Добавляем историю в словарь для маппера (упрощенно)
        hist_stats = {
            "refund_velocity_7d": len(history_df[history_df['return_date'].notna()]) # Упрощенно
        }
        
        clean_features = ProductionFeatureMapper.normalize(raw_dict, hist_stats, ip_stats)
        
        # 3. Predict
        return self._run_inference(clean_features, return_id=int(return_id), order_id=int(row['order_id']), client_id=client_id)

    def predict_from_web_payload(self, payload: Dict) -> Dict:
        """Real-time режим: Данные с сайта + Профиль из БД"""
        client_id = payload.get("client_id")
        order_id = payload.get("order_id")
        
        if not client_id or not order_id:
            raise ValueError("client_id and order_id required")

        # 1. Get Baseline from DB
        query = """
        SELECT c.*, o.* FROM clients c JOIN orders o ON c.client_id = o.client_id
        WHERE c.client_id = %s AND o.order_id = %s
        """
        conn = get_connection()
        try:
            df = pd.read_sql_query(query, conn, params=(client_id, order_id))
        finally:
            conn.close()

        # Merge: Payload has priority over DB (for fresh data like receipt status)
        if not df.empty:
            db_data = df.iloc[0].to_dict()
            db_data.update(payload) 
            raw_dict = db_data
        else:
            raw_dict = payload # Fallback if order not yet in DB

        # 2. Get Context
        history_df = get_client_history(client_id)
        ip_stats = get_ip_device_stats(client_id)
        
        hist_stats = {
             "refund_velocity_7d": len(history_df[history_df['return_date'].notna()])
        }

        # 3. Map & Predict
        clean_features = ProductionFeatureMapper.normalize(raw_dict, hist_stats, ip_stats)
        return self._run_inference(clean_features, return_id=payload.get("return_id", 0), order_id=order_id, client_id=client_id)

    def _run_inference(self, features: Dict, return_id: int, order_id: int, client_id: int) -> Dict:
        """Внутренний метод запуска ONNX и Anomaly моделей"""
        
        # 1. Prepare Vector
        X_input = self._prepare_vector(features)
        
        # 2. CatBoost ONNX Inference
        onnx_out = self.session.run(None, {self.input_name: X_input})[0]
        prob_fraud = float(onnx_out[0][1]) if onnx_out.ndim == 2 and onnx_out.shape[1] > 1 else float(onnx_out.flatten()[0])
        
        # 3. Isolation Forest (Anomaly)
        # Scaler expects the SAME columns as ONNX. Since we used strict ordering, shapes match.
        X_scaled = self.scaler.transform(X_input)
        anomaly_score = float(self.iforest.score_samples(X_scaled)[0])
        is_anomaly = anomaly_score < self.anomaly_threshold
        
        # 4. Rule-based Score
        rule_score = 0.0
        if features.get('account_age_days', 365) < 7: rule_score += 0.15
        if features.get('order_amount', 0) > 30000: rule_score += 0.10
        if features.get('missing_components', 0) == 1: rule_score += 0.20
        if features.get('wear_evidence_detected', 0) == 1: rule_score += 0.18
        if features.get('device_is_emulator', 0) == 1: rule_score += 0.12
        rule_score = min(rule_score, 1.0)
        
        # 5. Final Decision
        anomaly_risk = 1.0 if is_anomaly else 0.2
        combined_score = 0.6 * prob_fraud + 0.25 * rule_score + 0.15 * anomaly_risk
        
        decision = "BLOCK" if combined_score > self.threshold else ("REVIEW" if combined_score > self.threshold * 0.8 else "APPROVE")
        
        return {
            "return_id": int(return_id),
            "client_id": int(client_id),
            "order_id": int(order_id),
            "probability_fraud": round(prob_fraud, 4),
            "anomaly_score": round(anomaly_score, 4),
            "is_anomaly": bool(is_anomaly),
            "combined_score": round(combined_score, 4),
            "decision": decision,
            "timestamp": datetime.now().isoformat()
        }

    def batch_predict_all_returns(self, limit: Optional[int] = None, save_to_db: bool = True) -> pd.DataFrame:
        """Массовая обработка всех возвратов в БД"""
        conn = get_connection()
        try:
            query = "SELECT return_id FROM returns ORDER BY return_id"
            if limit: query += f" LIMIT {limit}"
            ids_df = pd.read_sql_query(query, conn)
        finally:
            conn.close()
            
        results = []
        print(f"🚀 Processing {len(ids_df)} returns...")
        
        for _, row in ids_df.iterrows():
            try:
                res = self.predict_for_return(row['return_id'])
                results.append(res)
            except Exception as e:
                print(f"Error on return {row['return_id']}: {e}")
                
        df_res = pd.DataFrame(results)
        
        if save_to_db and not df_res.empty:
            self._save_results_to_db(df_res)
            
        return df_res

    def _save_results_to_db(self, df: pd.DataFrame):
        table = "fraud_predictions"
        conn = get_connection()
        try:
            cur = conn.cursor()
            # Create table if not exists
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                return_id INTEGER PRIMARY KEY,
                client_id INTEGER,
                order_id INTEGER,
                probability_fraud FLOAT,
                anomaly_score FLOAT,
                is_anomaly BOOLEAN,
                combined_score FLOAT,
                decision VARCHAR(20),
                predicted_at TIMESTAMP DEFAULT NOW()
            )""")
            
            # Upsert
            sql = f"""
            INSERT INTO {table} (return_id, client_id, order_id, probability_fraud, anomaly_score, is_anomaly, combined_score, decision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (return_id) DO UPDATE SET
                probability_fraud = EXCLUDED.probability_fraud,
                combined_score = EXCLUDED.combined_score,
                decision = EXCLUDED.decision,
                predicted_at = NOW()
            """
            execute_batch(cur, sql, df[['return_id', 'client_id', 'order_id', 'probability_fraud', 'anomaly_score', 'is_anomaly', 'combined_score', 'decision']].values)
            conn.commit()
            print(f"💾 Saved {len(df)} predictions to DB")
        except Exception as e:
            print(f"DB Save Error: {e}")
            conn.rollback()
        finally:
            conn.close()

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    service = OnnxFraudService()
    
    # 1. Тест Real-time API
    print("\n--- Test Web Payload ---")
    try:
        web_result = service.predict_from_web_payload({
            "client_id": 1, "order_id": 100, "return_id": 999,
            "claimed_reason": "Брак", "order_amount": 50000, "account_age_days": 2
        })
        print(json.dumps(web_result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Web test failed (expected if DB empty): {e}")

    # 2. Тест Batch Processing
    print("\n--- Test Batch Processing ---")
    try:
        df_results = service.batch_predict_all_returns(limit=10)
        print(df_results[['return_id', 'decision', 'combined_score']].head())
    except Exception as e:
        print(f"Batch test failed: {e}")