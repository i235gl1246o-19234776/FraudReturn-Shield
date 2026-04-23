import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import onnxruntime as rt
import joblib
import warnings
import json
import psycopg2
import os
import csv
from contextlib import contextmanager

try:
    from feature_encoder import OneHotFeatureEncoder
except ImportError:
    print("feature_encoder.py not found. Ensure it's in sys.path.")
    warnings.filterwarnings('ignore')

warnings.filterwarnings('ignore', message='.*pandas only supports SQLAlchemy connectable.*', category=UserWarning)


conn_params = {
    'host': 'localhost',  
    'port': 5432,  
    'database': 'fraud_return_db',  
    'user': 'postgres',  
    'password': 'OmegaBloody13'  
}

MODEL_DIR = "models/"
ONNX_PATH = os.path.join(MODEL_DIR, "fraud_model_v4_27patterns.onnx")
METADATA_PATH = os.path.join(MODEL_DIR, "metadata_v4_27patterns.json")
ANOMALY_SCALER_PATH = os.path.join(MODEL_DIR, "scaler_v4.pkl")
ANOMALY_MODEL_PATH = os.path.join(MODEL_DIR, "anomaly_model_v4.pkl")
OHE_ENCODER_PATH = os.path.join(MODEL_DIR, "ohe_encoder_v4.pkl")
PREDICTIONS_CSV = os.path.join(MODEL_DIR, "fraud_predictions.csv")


@contextmanager
def get_db_connection():
    conn = psycopg2.connect(**conn_params)
    conn.set_client_encoding('UTF8')
    try:
        yield conn
    finally:
        conn.close()

def get_transaction_features(return_id: int) -> pd.DataFrame:
    query = """
    SELECT
        c.client_id, c.account_age_days, c.total_orders as total_purchases, 
        c.total_returns, c.global_return_rate as customer_return_rate,
        c.avg_order_amount,
        o.order_id, o.order_amount, o.items_count as items_in_order, 
        o.discount_amount, o.payment_method, o.order_timestamp, 
        o.orders_last_30d, o.product_category as category, 
        o.is_electronics, o.shipping_region, o.region_risk_score as shipping_region_risk,
        o.distance_from_registration_km as distance_from_registration_city,
        o.card_country_mismatch as card_bin_country_mismatch, 
        o.delivery_address_type, o.is_address_match as address_match,
        r.return_id, r.returns_last_30d, r.return_rate_last_30d, 
        r.days_since_last_return, r.days_since_purchase, r.return_channel, 
        r.has_receipt, r.tags_removed, r.missing_components, r.claimed_reason
    FROM returns r
    JOIN orders o ON r.order_id = o.order_id
    JOIN clients c ON r.client_id = c.client_id
    WHERE r.return_id = %s
    """
    with get_db_connection() as conn:
        df = pd.read_sql_query(query, conn, params=(return_id,))
    if df.empty: raise ValueError(f"Return ID {return_id} not found")
    return df


def get_client_history(client_id: int, days_back: int = 90) -> pd.DataFrame:
    query = f"""
    SELECT o.order_id, o.order_timestamp, o.order_amount,
           r.return_id, r.created_at as return_date
    FROM orders o
    LEFT JOIN returns r ON o.order_id = r.order_id
    WHERE o.client_id = %s AND o.order_timestamp >= NOW() - INTERVAL '{days_back} days'
    ORDER BY o.order_timestamp DESC
    """
    with get_db_connection() as conn:
        return pd.read_sql_query(query, conn, params=(client_id,))


def get_ip_device_stats(client_id: int) -> Dict:
    query = """
    SELECT COUNT(DISTINCT ip_address) as unique_ips_90d, COUNT(DISTINCT device_id) as unique_devices_90d,
           COUNT(*) FILTER (WHERE login_timestamp >= NOW() - INTERVAL '24 hours') as sessions_24h,
           COUNT(*) FILTER (WHERE login_timestamp >= NOW() - INTERVAL '7 days') as sessions_7d,
           COUNT(*) FILTER (WHERE is_emulator = TRUE) as emulator_sessions
    FROM client_sessions
    WHERE client_id = %s AND login_timestamp >= NOW() - INTERVAL '90 days'
    """
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(client_id,))
        if not df.empty and df.iloc[0]['unique_ips_90d'] is not None:
            row = df.iloc[0]
            return {
                "ip_velocity_24h": int(row['sessions_24h'] or 0),
                "ip_velocity_7d": int(row['sessions_7d'] or 0),
                "accounts_per_ip": max(1, int(row['unique_ips_90d'] or 1)),
                "accounts_per_device": max(1, int(row['unique_devices_90d'] or 1)),
                "device_is_emulator": 1 if (row['emulator_sessions'] or 0) > 0 else 0,
                "device_trust_score": 0.9, "ip_trust_score": 0.9
            }
    except Exception:
        pass
    return {"ip_velocity_24h": 0, "ip_velocity_7d": 0, "accounts_per_ip": 1, "accounts_per_device": 1,
            "device_is_emulator": 0, "device_trust_score": 0.9, "ip_trust_score": 0.9}


class ProductionFeatureEngineer:
    def __init__(self):
        self.categorical_features = ['category', 'claimed_reason', 'delivery_address_type']
        self.exclude_cols = ['is_fraud', 'timestamp', 'fraud_pattern', 'registration_date',
                             'customer_id', 'ip_prefix', 'device_id', 'phone_hash',
                             'days_to_return', 'time_to_return_hours', 'return_id', 'order_id', 'client_id']
        self.pre_return_features = [
            'account_age_days', 'total_purchases', 'total_returns', 'customer_return_rate', 'order_amount',
            'category', 'high_value_flag', 'weekend_purchase', 'address_match', 'device_new', 'receipt_provided',
            'claimed_reason', 'discount_percent', 'promo_code_used', 'first_order_discount_abuse', 'is_electronics',
            'items_in_order', 'payment_method_risk', 'chargeback_history_90d', 'card_bin_country_mismatch',
            'shipping_region_risk', 'delivery_address_type', 'distance_from_registration_city', 'order_hour',
            'order_time_night', 'ip_velocity_24h', 'ip_velocity_7d', 'accounts_per_ip', 'accounts_per_phone',
            'accounts_per_device', 'device_is_emulator', 'device_trust_score', 'ip_trust_score', 'avg_order_amount',
            'return_rate_30d', 'refund_velocity_7d', 'refund_velocity_30d', 'support_ticket_count_30d',
            'review_count_30d', 'negative_review_cluster', 'threat_language_detected', 'legal_claim_threat',
            'wear_evidence_detected', 'brand_mismatch', 'category_mismatch', 'event_season_flag', 'mass_tryon_flag',
            'order_bracketing_ratio', 'package_weight_vs_expected', 'xray_scan_anomaly', 'empty_box_claim_count',
            'package_density_score', 'review_text_similarity_score', 'same_address_different_accounts',
            'rma_reuse_count',
            'cross_channel_return', 'duplicate_refund_30d', 'same_item_burst', 'holiday_season_return',
            'fast_return_flag', 'new_account_flag', 'tags_removed', 'missing_components', 'has_receipt'
        ]

    def prepare_features(self, raw_data: Dict, history_df: pd.DataFrame = None) -> Dict:
        features = raw_data.copy()

        if history_df is not None and not history_df.empty:
            now = datetime.now()
            features['refund_velocity_7d'] = len(history_df[(history_df['return_date'].notna()) & (
                    history_df['order_timestamp'] >= now - timedelta(days=7))])
            features['refund_velocity_30d'] = len(history_df[(history_df['return_date'].notna()) & (
                    history_df['order_timestamp'] >= now - timedelta(days=30))])
            total_purchases = len(history_df)
            total_returns = history_df['return_date'].notna().sum()
            features['customer_return_rate_cum'] = total_returns / max(total_purchases, 1)
        else:
            features['refund_velocity_7d'] = 0
            features['refund_velocity_30d'] = 0
            features['customer_return_rate_cum'] = features.get('customer_return_rate', 0)

        features['high_value_flag'] = 1 if features.get('order_amount', 0) > 30000 else 0
        order_ts = features.get('order_timestamp')
        features['weekend_purchase'] = 1 if isinstance(order_ts,
                                                       (datetime, pd.Timestamp)) and order_ts.weekday() >= 5 else 0
        features['order_time_night'] = 1 if int(features.get('order_hour', 12)) in [0, 1, 2, 3, 4, 5] else 0
        features['fast_return_flag'] = 1 if features.get('days_since_purchase', 14) <= 3 else 0
        features['new_account_flag'] = 1 if features.get('account_age_days', 365) < 7 else 0
        features['is_electronics'] = 1 if str(features.get('category', '')).lower() in ['электроника',
                                                                                        'electronics'] else 0

        defaults = {
            'discount_percent': 0.0, 'promo_code_used': 0, 'first_order_discount_abuse': 0,
            'chargeback_history_90d': 0, 'support_ticket_count_30d': 0, 'review_count_30d': 0,
            'negative_review_cluster': 0, 'threat_language_detected': 0, 'legal_claim_threat': 0,
            'wear_evidence_detected': 0, 'brand_mismatch': 0, 'category_mismatch': 0,
            'event_season_flag': 0, 'mass_tryon_flag': 0, 'order_bracketing_ratio': 0.0,
            'package_weight_vs_expected': 0.0, 'xray_scan_anomaly': 0, 'empty_box_claim_count': 0,
            'package_density_score': 1.0, 'review_text_similarity_score': 0.0,
            'same_address_different_accounts': 0, 'rma_reuse_count': 0, 'cross_channel_return': 0,
            'duplicate_refund_30d': 0, 'same_item_burst': 0, 'holiday_season_return': 0,
            'device_new': 0, 'address_match': 1, 'receipt_provided': 1, 'tags_removed': 0,
            'missing_components': 0, 'has_receipt': 1, 'accounts_per_phone': 1,
            'return_rate_30d': 0.0, 'avg_order_amount': float(features.get('order_amount', 1000)),
            'device_trust_score': 0.9, 'ip_trust_score': 0.9, 'payment_method_risk': 0.3,
            'shipping_region_risk': 0.3, 'distance_from_registration_city': 50.0,
            'order_hour': 12, 'items_in_order': 1, 'account_age_days': 365,
            'total_purchases': 1, 'total_returns': 0, 'customer_return_rate': 0.0,
            'ip_velocity_24h': 1, 'ip_velocity_7d': 5, 'accounts_per_ip': 1,
            'accounts_per_device': 1, 'device_is_emulator': 0
        }
        for k, v in defaults.items():
            if k not in features or features[k] is None or pd.isna(features[k]):
                features[k] = v
        return features


def get_feature_columns(self) -> List[str]:
    return [c for c in self.pre_return_features if c not in self.exclude_cols]


class OnnxFraudService:
    def __init__(self, model_dir: str = ""):
        global MODEL_DIR, ONNX_PATH, METADATA_PATH, ANOMALY_SCALER_PATH, ANOMALY_MODEL_PATH, OHE_ENCODER_PATH
        MODEL_DIR = model_dir
        METADATA_PATH = os.path.join(MODEL_DIR, "metadata_v4_27patterns.json")
        ANOMALY_SCALER_PATH = os.path.join(MODEL_DIR, "scaler_v4.pkl")
        ANOMALY_MODEL_PATH = os.path.join(MODEL_DIR, "anomaly_model_v4.pkl")
        OHE_ENCODER_PATH = os.path.join(MODEL_DIR, "ohe_encoder_v4.pkl")

        self._load_models()
        self.feature_engineer = ProductionFeatureEngineer()
        print("✅ OnnxFraudService initialized")

    def _load_models(self):
        import os

        required_files = {
            'metadata': METADATA_PATH,
            'encoder': OHE_ENCODER_PATH,
            'scaler': ANOMALY_SCALER_PATH,
            'anomaly_model': ANOMALY_MODEL_PATH,
            'pattern_model': os.path.join(MODEL_DIR, "fraud_model_v4_27patterns.cbm")
        }

        missing = [name for name, path in required_files.items() if not os.path.exists(path)]
        if missing:
            raise FileNotFoundError(
                f"Отсутствуют файлы моделей: {', '.join(missing)}\n"
                f"Запустите обучение или проверьте MODEL_DIR='{MODEL_DIR}'"
            )

        print(f"Загрузка моделей из {MODEL_DIR}...")

        with open(METADATA_PATH, 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)
        self.feature_columns = self.metadata.get('feature_columns', [])
        self.numeric_columns = self.metadata.get('numeric_columns', [])
        self.threshold = self.metadata.get('optimal_threshold', 0.65)
        self.anomaly_threshold = self.metadata.get('anomaly_threshold', -0.1)
        print(f"   metadata.json: {len(self.feature_columns)} признаков")

        self.encoder = OneHotFeatureEncoder.load(OHE_ENCODER_PATH)
        print(f"   ohe_encoder_v4.pkl: {len(self.encoder.cat_cols)} категориальных колонок")

        self.scaler = joblib.load(ANOMALY_SCALER_PATH)
        print(f"   scaler_v4.pkl: {len(self.scaler.mean_)} числовых признаков")

        self.iforest = joblib.load(ANOMALY_MODEL_PATH)
        print(f"   anomaly_model_v4.pkl: {self.iforest.n_estimators} деревьев")

        from catboost import CatBoostClassifier
        self.pattern_model = CatBoostClassifier()
        self.pattern_model.load_model(required_files['pattern_model'])
        print(f"   fraud_model_v4_27patterns.cbm: {self.pattern_model.tree_count_} деревьев")

        print("Все модели загружены успешно")

    def _calculate_rule_score(self, features: Dict) -> float:
        score = 0.0
        if features.get('account_age_days', 365) < 7: score += 0.15
        if features.get('order_amount', 0) > 30000: score += 0.10
        if features.get('missing_components', 0) == 1: score += 0.20
        if features.get('wear_evidence_detected', 0) == 1: score += 0.18
        if features.get('device_is_emulator', 0) == 1: score += 0.12
        if features.get('refund_velocity_30d', 0) >= 3: score += 0.15
        return min(score, 1.0)

    def _run_inference(self, features: Dict, return_id: int, order_id: int, client_id: int) -> Dict:
        if hasattr(self.encoder, 'transform_single'):
            encoded_dict = self.encoder.transform_single(features)
        else:
            encoded_dict = self.encoder.transform(pd.DataFrame([features])).iloc[0].to_dict()

        X_dict = {k: float(encoded_dict.get(k, 0.0)) for k in self.feature_columns}
        X_df = pd.DataFrame([X_dict]).reindex(columns=self.feature_columns, fill_value=0.0)

        proba = self.pattern_model.predict_proba(X_df)[0]
        prob_fraud = float(proba[1])


        top_contributions = []
        try:
            from catboost import Pool
            pool = Pool(X_df)
            shap_vals = self.pattern_model.get_feature_importance(data=pool, type='ShapValues')

            row_vals = shap_vals[0]
            n_feats = len(self.feature_columns)
            contributions = {col: float(row_vals[i]) for i, col in enumerate(self.feature_columns[:n_feats])}

            top_5 = sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
            top_contributions = [
                {
                    "feature": feat,
                    "contribution": round(val, 4),
                    "effect": "повышает риск" if val > 0 else "снижает риск"
                }
                for feat, val in top_5
            ]
        except Exception as e:
            print(f"⚠️ Объяснение пропущено: {e}")

        if self.numeric_columns and all(col in X_df.columns for col in self.numeric_columns):
            X_scaled = self.scaler.transform(X_df[self.numeric_columns])
            anomaly_score = float(self.iforest.score_samples(X_scaled)[0])
        else:
            anomaly_score = 0.0
        is_anomaly = anomaly_score < self.anomaly_threshold

        rule_score = self._calculate_rule_score(features)

        anomaly_risk = 1.0 if is_anomaly else 0.2
        combined_score = 0.6 * prob_fraud + 0.25 * rule_score + 0.15 * anomaly_risk

        decision = "BLOCK" if combined_score > self.threshold else (
            "REVIEW" if combined_score > self.threshold * 0.8 else "APPROVE"
        )

        return {
            "return_id": int(return_id),
            "client_id": int(client_id),
            "order_id": int(order_id),
            "probability_fraud": round(prob_fraud, 4),
            "anomaly_score": round(anomaly_score, 4),
            "is_anomaly": bool(is_anomaly),
            "combined_score": round(combined_score, 4),
            "decision": decision,
            "top_features": top_contributions,  
            "timestamp": datetime.now().isoformat()
        }
    def predict_for_return(self, return_id: int) -> Dict:
        tx_df = get_transaction_features(return_id)
        row = tx_df.iloc[0]
        client_id = int(row["client_id"])
        history_df = get_client_history(client_id, days_back=90)
        ip_stats = get_ip_device_stats(client_id)
        raw_dict = {**row.to_dict(), **ip_stats}
        clean_features = self.feature_engineer.prepare_features(raw_dict, history_df)
        return self._run_inference(clean_features, return_id, int(row['order_id']), client_id)

    def predict_from_web_payload(self, payload: Dict) -> Dict:
        client_id, order_id = payload.get("client_id"), payload.get("order_id")
        if not client_id or not order_id:
            raise ValueError("client_id and order_id required")

        return_id = payload.get("return_id")
        db_context = {}
        query = """
        SELECT r.return_id, c.account_age_days, c.total_orders as total_purchases, 
               c.total_returns, c.global_return_rate as customer_return_rate,
               c.avg_order_amount, o.order_amount, o.items_count as items_in_order, 
               o.discount_amount, o.payment_method, o.order_timestamp, 
               o.product_category as category, o.is_electronics, o.shipping_region, 
               o.region_risk_score as shipping_region_risk, 
               o.distance_from_registration_km as distance_from_registration_city,
               o.card_country_mismatch as card_bin_country_mismatch, 
               o.delivery_address_type, o.is_address_match as address_match
        FROM returns r JOIN orders o ON r.order_id = o.order_id 
        JOIN clients c ON r.client_id = c.client_id
        WHERE r.client_id = %s AND r.order_id = %s 
        ORDER BY r.created_at DESC LIMIT 1
        """
        with get_db_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(client_id, order_id))

        if not df.empty:
            db_context = df.iloc[0].to_dict()
            if return_id is None:
                return_id = db_context.get('return_id')
        if return_id is None:
            return_id = 0

        raw_data = {**db_context, **payload}
        raw_data["tags_removed"] = 0.0 if raw_data.get("tag_present", True) else 1.0
        raw_data["has_receipt"] = 1.0 if raw_data.get("receipt_provided", True) else 0.0
        raw_data["receipt_provided"] = raw_data["has_receipt"]
        raw_data["missing_components"] = 1.0 if raw_data.get("has_damage", False) else 0.0
        raw_data["wear_evidence_detected"] = 1.0 if raw_data.get("has_wear", False) else 0.0
        raw_data["claimed_reason"] = str(raw_data.get("return_reason", raw_data.get("claimed_reason", "Передумал")))
        raw_data["category"] = str(raw_data.get("product_category", raw_data.get("category", "Электроника")))

        history_df = get_client_history(client_id, days_back=90)
        ip_stats = get_ip_device_stats(client_id)
        raw_data.update(ip_stats)
        clean_features = self.feature_engineer.prepare_features(raw_data, history_df)
        return self._run_inference(clean_features, return_id, order_id, client_id)

    def process_all_returns_sequentially(self, return_ids: List[int] = None) -> List[Dict]:
        if return_ids is None:
            with get_db_connection() as conn:
                df = pd.read_sql_query("SELECT return_id FROM returns ORDER BY return_id", conn)
            return_ids = [int(row['return_id']) for _, row in df.iterrows()]

        if not return_ids:
            print("⚠️ Нет возвратов для обработки")
            return []

        results, total = [], len(return_ids)
        print(f"Обработка {total} возвратов (по одному)...")

        for idx, rid in enumerate(return_ids, 1):
            try:
                res = self.predict_for_return(rid)
                clean_res = {}
                for k, v in res.items():
                    if isinstance(v, (np.integer, np.floating)):
                        clean_res[k] = v.item()
                    elif isinstance(v, bool):
                        clean_res[k] = v
                    elif isinstance(v, datetime):
                        clean_res[k] = v.isoformat()
                    elif isinstance(v, list):
                        clean_res[k] = json.dumps(v, ensure_ascii=False)
                    else:
                        clean_res[k] = v
                results.append(clean_res)

                if idx % 10 == 0 or idx == total:
                    pct = (idx / total) * 100
                    print(f"[{idx}/{total}] {pct:.1f}% | return_id={rid} → {clean_res['decision']}")

            except Exception as e:
                print(f"Ошибка на return_id {rid}: {e}")
                results.append({"return_id": rid, "error": str(e), "decision": "ERROR"})
                continue

        print(f"Обработка завершена. Успешно: {len([r for r in results if 'error' not in r])}/{total}")
        return results


def export_model_with_proba_sklearn(model_path: str, output_onnx_path: str):
    try:
        from skl2onnx import to_onnx
        import onnx
        from catboost import CatBoostClassifier

        model = CatBoostClassifier()
        model.load_model(model_path.replace('.onnx', '.cbm'))

        dummy_X = pd.DataFrame({col: [0.0] for col in [c for c in json.load(open(METADATA_PATH))['feature_columns']]})

        onx = to_onnx(model, dummy_X,
                      options={CatBoostClassifier: {'predict_proba': True, 'zipmap': False}},
                      target_opset=14)
        with open(output_onnx_path, "wb") as f:
            f.write(onx.SerializeToString())
        onnx.checker.check_model(onnx.load(output_onnx_path))
        print(f"Модель пересохранена с predict_proba: {output_onnx_path}")
    except ImportError:
        print("Установите skl2onnx: pip install skl2onnx")
    except Exception as e:
        print(f"Ошибка экспорта: {e}")


if __name__ == "__main__":
    service = OnnxFraudService()

    print("\n--- Test Web Payload (UI Form) ---")
    try:
        web_result = service.predict_from_web_payload({
            "client_id": 6001,
            "order_id": 7626,
            "account_age_days": 180,
            "total_orders": 25,
            "product_category": "Одежда",
            "order_amount": 5000,
            "days_since_purchase": 14,
            "tag_present": False,
            "receipt_provided": False,
            "has_damage": True,
            "has_wear": True,
            "return_reason": "Не подошел размер"
        })
        print(json.dumps(web_result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Web test failed: {e}")

    print("\n--- Process All Returns Sequentially ---")
    try:
        all_results = service.process_all_returns_sequentially()

        clean_results = [r for r in all_results if "error" not in r]

        if clean_results:
            output_csv = "fraud_predictions.csv"
            try:

                pd.DataFrame(clean_results).to_csv(output_csv, index=False, encoding="utf-8-sig")
                print(f"Успешно сохранено {len(clean_results)} записей в {output_csv}")
            except PermissionError:
                from datetime import datetime

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_csv = f"fraud_predictions_{ts}.csv"
                pd.DataFrame(clean_results).to_csv(safe_csv, index=False, encoding="utf-8-sig")
                print(f"Файл {output_csv} был заблокирован. Сохранено как: {safe_csv}")
        else:
            print("Нет успешных результатов для сохранения")

    except Exception as e:
        print(f"Ошибка выполнения: {e}")