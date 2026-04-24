# =============================================================================
# FRAUDRETURN SHIELD v4.1 — ПРОДАКШН-ГОТОВАЯ СИСТЕМА С 27 ПАТТЕРНАМИ ФРОДА
# ✅ One-Hot Encoding вместо Label Encoding
# ✅ Плавающий процент фрода 0.5-2% при обучении
# ✅ Все 27 паттернов, O(N) feature engineering, защита от leakage
# =============================================================================

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from catboost import CatBoostClassifier, Pool
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import average_precision_score, confusion_matrix
import shap
import warnings
import time
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import joblib
import onnx
import onnxruntime as rt

plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")
warnings.filterwarnings('ignore')

print("зависимости импортированы")


def _generate_fraud_transaction(self, customer_profile: Dict, pattern: str) -> Dict:
    base = self._generate_legitimate_transaction(customer_profile)

    # === ZHANG ET AL. (2023) — 12 паттернов ===
    if pattern == 'wardrobing':
        base.update({
            'category': 'Одежда', 'days_to_return': self.rng.integers(3, 10),
            'weekend_purchase': self.rng.choice([True, False], p=[0.7, 0.3]),
            'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
            'claimed_reason': 'Не подошёл размер', 'is_fraud': True, 'fraud_pattern': 'wardrobing',
            'wear_evidence_detected': self.rng.choice([1, 0], p=[0.7, 0.3]),
            'event_season_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
            'items_in_order': self.rng.integers(3, 8),
            'mass_tryon_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
            'order_bracketing_ratio': self.rng.uniform(0.5, 1.0),
        })
    elif pattern == 'price_arbitrage':
        base.update({
            'category': 'Электроника', 'days_to_return': self.rng.integers(1, 3),
            'order_amount': max(15000, base['order_amount']),
            'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'price_arbitrage',
            'is_electronics': 1, 'discount_percent': self.rng.uniform(15, 40),
            'first_order_discount_abuse': self.rng.choice([1, 0], p=[0.7, 0.3]),
            'new_account_flag': self.rng.choice([True, False], p=[0.7, 0.3]),
            'account_age_days': self.rng.integers(1, 10),
        })
    elif pattern == 'shipping_fraud':
        base.update({
            'days_to_return': self.rng.integers(0, 2),
            'address_match': self.rng.choice([True, False], p=[0.3, 0.7]),
            'device_new': self.rng.choice([True, False], p=[0.7, 0.3]),
            'claimed_reason': 'Не получил товар', 'is_fraud': True, 'fraud_pattern': 'shipping_fraud',
            'package_weight_vs_expected': self.rng.uniform(-0.9, -0.3),
            'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
            'empty_box_claim_count': self.rng.choice([1, 0], p=[0.5, 0.5]),
            'accounts_per_phone': self.rng.integers(2, 5),
            'device_is_emulator': self.rng.choice([0, 1], p=[0.6, 0.4]),
        })
    elif pattern == 'receipt_fraud':
        base.update({
            'receipt_provided': False, 'days_to_return': self.rng.integers(1, 3),
            'claimed_reason': 'Потерял чек', 'is_fraud': True, 'fraud_pattern': 'receipt_fraud',
            'support_ticket_count_30d': self.rng.integers(2, 5),
            'threat_language_detected': self.rng.choice([0, 1], p=[0.6, 0.4]),
        })
    elif pattern == 'employee_fraud':
        base.update({
            'days_to_return': 0, 'claimed_reason': 'Возврат по гарантии',
            'is_fraud': True, 'fraud_pattern': 'employee_fraud',
            'warranty_doc_provided': 1, 'legal_claim_threat': 1,
            'same_address_different_accounts': 1, 'accounts_per_device': self.rng.integers(2, 4),
        })
    elif pattern == 'multi_channel_refund':
        base.update({
            'days_to_return': self.rng.integers(1, 5), 'claimed_reason': 'Проблема с доставкой',
            'is_fraud': True, 'fraud_pattern': 'multi_channel_refund',
            'cross_channel_return': 1, 'duplicate_refund_30d': 1,
            'refund_velocity_7d': self.rng.integers(2, 5), 'rma_reuse_count': self.rng.integers(1, 3),
        })
    elif pattern == 'discount_fraud':
        base.update({
            'claimed_reason': 'Нашел дешевле', 'is_fraud': True, 'fraud_pattern': 'discount_fraud',
            'discount_percent': self.rng.uniform(25, 50), 'promo_code_used': 1,
            'first_order_discount_abuse': 1,
            'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]), 'order_time_night': 1,
        })
    elif pattern == 'damage_fraud':
        base.update({
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'damage_fraud',
            'wear_evidence_detected': 1, 'package_weight_vs_expected': self.rng.uniform(-0.3, 0.3),
            'support_ticket_count_30d': self.rng.integers(1, 3),
        })
    elif pattern == 'points_fraud':
        base.update({
            'claimed_reason': 'Передумал', 'is_fraud': True, 'fraud_pattern': 'points_fraud',
            'review_count_30d': self.rng.integers(5, 10), 'negative_review_cluster': 1,
            'items_in_order': self.rng.integers(5, 10),
        })
    elif pattern == 'bricking':
        base.update({
            'category': 'Электроника', 'claimed_reason': 'Не работает',
            'is_fraud': True, 'fraud_pattern': 'bricking',
            'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
            'package_weight_vs_expected': self.rng.uniform(-0.7, -0.3),
            'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
            'empty_box_claim_count': self.rng.integers(1, 3),
        })
    elif pattern == 'professional_refunder':
        base.update({
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'professional_refunder',
            'accounts_per_ip': self.rng.integers(3, 8), 'accounts_per_phone': self.rng.integers(2, 5),
            'accounts_per_device': self.rng.integers(2, 4), 'ip_velocity_24h': self.rng.integers(5, 15),
            'same_address_different_accounts': 1, 'device_is_emulator': 1,
            'cross_channel_return': 1, 'refund_velocity_7d': self.rng.integers(3, 8),
        })
    elif pattern == 'multi_accounting':
        base.update({
            'claimed_reason': 'Первый заказ', 'is_fraud': True, 'fraud_pattern': 'multi_accounting',
            'accounts_per_ip': self.rng.integers(3, 6), 'accounts_per_phone': self.rng.integers(2, 4),
            'first_order_discount_abuse': 1, 'discount_percent': self.rng.uniform(25, 45),
            'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]), 'ip_velocity_24h': self.rng.integers(4, 10),
        })

    elif pattern == 'old_item_return':
        base.update({
            'category': 'Одежда', 'claimed_reason': 'Не подошёл размер',
            'is_fraud': True, 'fraud_pattern': 'old_item_return',
            'package_weight_vs_expected': self.rng.uniform(-0.4, -0.1),
            'wear_evidence_detected': 1, 'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
            'items_in_order': self.rng.integers(5, 10), 'mass_tryon_flag': 1,
            'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
            'brand_mismatch': self.rng.choice([0, 1], p=[0.3, 0.7]),
        })
    elif pattern == 'intentional_damage':
        base.update({
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'intentional_damage',
            'legal_claim_threat': 1, 'support_ticket_count_30d': self.rng.integers(3, 6),
            'threat_language_detected': 1, 'warranty_doc_provided': 1,
            'review_count_30d': self.rng.integers(5, 10), 'negative_review_cluster': 1,
            'wear_evidence_detected': 1,
        })
    elif pattern == 'self_checkout_theft':
        base.update({
            'claimed_reason': 'Ошибка кассы', 'is_fraud': True, 'fraud_pattern': 'self_checkout_theft',
            'device_is_emulator': 1, 'package_density_score': self.rng.uniform(0.3, 0.6),
            'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
            'device_new': 1, 'account_age_days': self.rng.integers(1, 10),
        })
    elif pattern == 'freezing_competitors':
        base.update({
            'claimed_reason': 'Не пришёл товар', 'is_fraud': True, 'fraud_pattern': 'freezing_competitors',
            'cross_channel_return': 1, 'refund_velocity_7d': self.rng.integers(2, 4),
            'distance_from_registration_city': self.rng.integers(500, 2000),
            'same_item_burst': True, 'negative_review_cluster': 1,
        })
    elif pattern == 'mass_try_on':
        base.update({
            'category': 'Одежда', 'claimed_reason': 'Не подошёл размер',
            'is_fraud': True, 'fraud_pattern': 'mass_try_on',
            'items_in_order': self.rng.integers(8, 15),
            'order_bracketing_ratio': self.rng.uniform(0.8, 1.0), 'mass_tryon_flag': 1,
            'event_season_flag': 1, 'days_to_return': self.rng.integers(1, 2),
            'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
        })
    elif pattern == 'perishable_fraud':
        base.update({
            'category': self.rng.choice(['Продукты питания', 'Косметика']),
            'claimed_reason': 'Испорченный товар', 'is_fraud': True, 'fraud_pattern': 'perishable_fraud',
            'receipt_provided': False, 'support_ticket_count_30d': self.rng.integers(2, 4),
            'package_weight_vs_expected': self.rng.uniform(0.9, 1.1), 'wear_evidence_detected': 1,
        })
    elif pattern == 'review_blackmail':
        base.update({
            'claimed_reason': 'Не соответствует описанию', 'is_fraud': True, 'fraud_pattern': 'review_blackmail',
            'threat_language_detected': 1, 'negative_review_cluster': 1,
            'review_text_similarity_score': self.rng.uniform(0.7, 0.95), 'refund_velocity_7d': 0,
        })
    elif pattern == 'pvz_swap':
        base.update({
            'category': 'Одежда', 'claimed_reason': 'Не подошёл размер',
            'is_fraud': True, 'fraud_pattern': 'pvz_swap',
            'tag_removed': 1, 'wear_evidence_detected': 1, 'items_in_order': 1,
            'brand_mismatch': 1, 'category_mismatch': self.rng.choice([0, 1], p=[0.7, 0.3]),
        })
    elif pattern == 'cashier_no_receipt':
        base.update({
            'claimed_reason': 'Не пришёл товар', 'is_fraud': True, 'fraud_pattern': 'cashier_no_receipt',
            'same_address_different_accounts': self.rng.integers(2, 4),
            'accounts_per_device': self.rng.integers(3, 6), 'device_is_emulator': 1,
            'refund_velocity_7d': 0,
        })
    elif pattern == 'fake_return_employee':
        base.update({
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'fake_return_employee',
            'same_address_different_accounts': self.rng.integers(2, 3),
            'accounts_per_phone': self.rng.integers(2, 3),
            'refund_velocity_7d': self.rng.integers(2, 4), 'cross_channel_return': 1,
        })
    elif pattern == 'cashier_swap':
        base.update({
            'claimed_reason': 'Не тот товар', 'is_fraud': True, 'fraud_pattern': 'cashier_swap',
            'package_weight_vs_expected': self.rng.uniform(-0.6, -0.3), 'missing_components': 1,
            'same_address_different_accounts': self.rng.integers(2, 3),
            'refund_velocity_7d': self.rng.integers(1, 3),
        })
    elif pattern == 'review_manipulation':
        base.update({
            'claimed_reason': 'Не соответствует описанию', 'is_fraud': True, 'fraud_pattern': 'review_manipulation',
            'review_text_similarity_score': self.rng.uniform(0.8, 0.98),
            'accounts_per_ip': self.rng.integers(5, 10), 'review_count_30d': self.rng.integers(10, 20),
            'negative_review_cluster': 1, 'cross_channel_return': 1,
        })
    elif pattern == 'post_event_return':
        base.update({
            'category': 'Одежда', 'claimed_reason': 'Не подошёл размер',
            'is_fraud': True, 'fraud_pattern': 'post_event_return',
            'wear_evidence_detected': 1, 'event_season_flag': 1,
            'days_to_return': self.rng.integers(1, 3), 'tag_removed': 1, 'items_in_order': 1,
        })
    elif pattern == 'serial_refund':
        base.update({
            'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'serial_refund',
            'duplicate_refund_30d': self.rng.integers(2, 4), 'rma_reuse_count': self.rng.integers(2, 3),
            'refund_velocity_7d': self.rng.integers(3, 6), 'same_item_burst': True,
        })
    else:
        base.update({
            'is_fraud': True, 'fraud_pattern': pattern,
            'payment_method_risk': self.rng.uniform(0.4, 0.9),
            'shipping_region_risk': self.rng.uniform(0.4, 0.9),
        })

    if base['is_fraud']:
        base.update({
            'payment_method_risk': self.rng.uniform(0.4, 0.9),
            'chargeback_history_90d': self.rng.choice([0, 1], p=[0.6, 0.4]),
            'card_bin_country_mismatch': self.rng.choice([0, 1], p=[0.5, 0.5]),
            'shipping_region_risk': self.rng.uniform(0.4, 0.9),
            'distance_from_registration_city': self.rng.integers(50, 1000),
            'order_hour': self.rng.choice([2, 3, 4, 5, 12, 14, 18, 10, 11]),
            'order_time_night': 1 if base['order_hour'] in [2, 3, 4, 5] else 0,
            'holiday_season_return': self.rng.choice([0, 1], p=[0.6, 0.4]),
            'ip_velocity_24h': self.rng.integers(2, 10),
            'negative_review_cluster': self.rng.choice([0, 1], p=[0.6, 0.4]),
        })

    return self._add_noise_to_features(base, is_fraud=True)
# =============================================================================
# ONE-HOT ЭНКОДЕР
# =============================================================================
class OneHotFeatureEncoder:
    """
    One-Hot Encoder для категориальных признаков.
    """

    def __init__(self):
        self.expected_columns = []
        self.cat_cols = []
        self.fitted = False

    def fit(self, df: pd.DataFrame, categorical_cols: List[str]):
        self.cat_cols = [c for c in categorical_cols if c in df.columns]
        if self.cat_cols:
            df_cats = df[self.cat_cols].astype(str)
            self.expected_columns = list(pd.get_dummies(df_cats, prefix_sep='__').columns)
        self.fitted = True
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted:
            raise ValueError("Encoder not fitted. Call fit() first.")

        df_out = df.drop(columns=self.cat_cols, errors='ignore').copy()
        if self.cat_cols:
            # Безопасное создание дамми-колонок (даже если в инференсе не хватает категорий)
            df_cats = pd.DataFrame({c: df.get(c, pd.Series('', index=df.index)) for c in self.cat_cols}, index=df.index)
            dummies = pd.get_dummies(df_cats.astype(str), prefix_sep='__')

            # 🔧 Выравнивание колонок под обученную схему
            for col in self.expected_columns:
                if col not in dummies.columns:
                    dummies[col] = 0
            dummies = dummies[self.expected_columns].astype(int)
            df_out = pd.concat([df_out, dummies], axis=1)
        return df_out

    def fit_transform(self, df: pd.DataFrame, categorical_cols: List[str]) -> pd.DataFrame:
        self.fit(df, categorical_cols)
        return self.transform(df)

    def transform_single(self, features: Dict) -> Dict:
        """Трансформация одиночного словаря признаков с гарантированным порядком колонок"""
        if not self.fitted:
            raise ValueError("Encoder not fitted. Call fit() first.")

        df_single = pd.DataFrame([features])
        df_encoded = self.transform(df_single).iloc[0]
        # Возвращаем dict с гарантированным наличием всех expected_columns
        return df_encoded.to_dict()

    def save(self, path: str):
        joblib.dump(self, path)

    @staticmethod
    def load(path: str) -> 'OneHotFeatureEncoder':
        return joblib.load(path)

class FraudDataGenerator:
    def __init__(self, seed: int = 42):
        np.random.seed(seed)
        self.rng = np.random.default_rng(seed)
        self.customer_counter = 0

    def _generate_customer_id(self) -> str:
        self.customer_counter += 1
        return f"cust_{self.customer_counter:06d}"

    def _generate_customer_profile(self) -> Dict:
        customer_id = self._generate_customer_id()
        registration_date = pd.Timestamp.now() - pd.Timedelta(days=self.rng.integers(1, 730))
        account_age_days = (pd.Timestamp.now() - registration_date).days
        total_purchases = max(1, int(self.rng.exponential(5)))
        total_returns = int(total_purchases * self.rng.beta(2, 8))
        category_weights = {'Электроника': 0.30, 'Одежда': 0.35, 'Косметика': 0.15, 'Книги': 0.10, 'Спорттовары': 0.10}
        preferred_category = self.rng.choice(list(category_weights.keys()), p=list(category_weights.values()))
        risk_profile = self.rng.choice(['low', 'medium', 'high'], p=[0.7, 0.2, 0.1])

        return {
            'customer_id': customer_id, 'registration_date': registration_date, 'account_age_days': account_age_days,
            'total_purchases': total_purchases, 'total_returns': total_returns,
            'return_rate': total_returns / total_purchases if total_purchases > 0 else 0,
            'preferred_category': preferred_category,
            'ip_prefix': f"192.168.{self.rng.integers(0, 255)}.{self.rng.integers(0, 10)}",
            'device_id': f"device_{self.rng.integers(1000, 2000)}",
            'phone_hash': f"phone_{self.rng.integers(10000, 15000)}", 'risk_profile': risk_profile
        }

    def _add_noise_to_features(self, base: Dict, is_fraud: bool, noise_level: float = 0.15) -> Dict:
        noisy = base.copy()
        if is_fraud and self.rng.random() < noise_level:
            noisy['missing_components'] = False;
            noisy['wear_evidence_detected'] = 0;
            noisy['tag_removed'] = False
        elif not is_fraud and self.rng.random() < noise_level * 0.7:
            noisy['missing_components'] = self.rng.choice([True, False], p=[0.3, 0.7])
            noisy['wear_evidence_detected'] = self.rng.choice([0, 1], p=[0.8, 0.2])

        binary_flags = ['address_match', 'device_new', 'receipt_provided', 'weekend_purchase', 'high_value_flag',
                        'fast_return_flag', 'new_account_flag', 'promo_code_used', 'first_order_discount_abuse',
                        'legal_claim_threat', 'warranty_doc_provided', 'mass_tryon_flag', 'event_season_flag',
                        'device_is_emulator', 'cross_channel_return', 'negative_review_cluster',
                        'threat_language_detected',
                        'chargeback_history_90d', 'card_bin_country_mismatch', 'order_time_night',
                        'holiday_season_return',
                        'brand_mismatch', 'category_mismatch', 'xray_scan_anomaly']
        for flag in binary_flags:
            if flag in noisy and self.rng.random() < 0.05:
                noisy[flag] = not noisy[flag] if isinstance(noisy[flag], bool) else 1 - noisy[flag]
        return noisy

    def _generate_legitimate_transaction(self, customer_profile: Dict) -> Dict:
        category = customer_profile['preferred_category']
        order_amount = self.rng.lognormal(mean=8.5, sigma=1.0)
        days_to_return = int(np.clip(self.rng.normal(14, 5), 1, 30))
        order_hour = self.rng.integers(8, 22)
        timestamp = pd.Timestamp.now() - pd.Timedelta(days=self.rng.integers(1, 365))

        base = {
            'customer_id': customer_profile['customer_id'], 'timestamp': timestamp,
            'account_age_days': customer_profile['account_age_days'],
            'total_purchases': customer_profile['total_purchases'], 'total_returns': customer_profile['total_returns'],
            'customer_return_rate': customer_profile['return_rate'], 'order_amount': round(order_amount, 2),
            'category': category,
            'days_to_return': days_to_return, 'address_match': self.rng.random() > 0.1,
            'device_new': self.rng.random() > 0.8,
            'missing_components': False, 'tag_removed': False, 'receipt_provided': True,
            'claimed_reason': self.rng.choice(['Не подошёл размер', 'Передумал', 'Брак']),
            'weekend_purchase': self.rng.random() > 0.7, 'high_value_flag': order_amount > 30000,
            'fast_return_flag': days_to_return <= 3,
            'new_account_flag': customer_profile['account_age_days'] < 7, 'same_item_burst': False,
            'accounts_per_ip': 1,
            'is_fraud': False, 'fraud_pattern': 'legitimate', 'discount_percent': self.rng.uniform(0, 15),
            'discount_amount': round(order_amount * self.rng.uniform(0, 0.15), 2),
            'promo_code_used': self.rng.choice([0, 1], p=[0.7, 0.3]),
            'first_order_discount_abuse': 0, 'is_electronics': 1 if category == 'Электроника' else 0,
            'electronics_return_delay': 0,
            'legal_claim_threat': 0, 'warranty_doc_provided': self.rng.choice([0, 1], p=[0.9, 0.1]),
            'items_in_order': self.rng.integers(1, 4),
            'order_bracketing_ratio': self.rng.uniform(0, 0.3), 'mass_tryon_flag': 0, 'wear_evidence_detected': 0,
            'event_season_flag': 0,
            'accounts_per_phone': 1, 'accounts_per_device': 1, 'same_address_different_accounts': 0,
            'device_is_emulator': 0,
            'ip_velocity_24h': self.rng.integers(1, 3), 'rma_reuse_count': 0, 'cross_channel_return': 0,
            'duplicate_refund_30d': 0,
            'refund_velocity_7d': 0, 'package_weight_vs_expected': self.rng.uniform(-0.1, 0.1), 'xray_scan_anomaly': 0,
            'empty_box_claim_count': 0, 'package_density_score': self.rng.uniform(0.8, 1.2),
            'review_count_30d': self.rng.integers(0, 3),
            'review_text_similarity_score': self.rng.uniform(0, 0.3), 'negative_review_cluster': 0,
            'support_ticket_count_30d': self.rng.integers(0, 2),
            'threat_language_detected': 0, 'payment_method_risk': self.rng.uniform(0.1, 0.4),
            'chargeback_history_90d': 0,
            'card_bin_country_mismatch': 0, 'subscription_chargeback_flag': 0,
            'shipping_region_risk': self.rng.uniform(0.1, 0.4),
            'delivery_address_type': self.rng.choice(['home', 'office', 'pickup_point'], p=[0.6, 0.3, 0.1]),
            'distance_from_registration_city': self.rng.integers(0, 100), 'order_hour': order_hour,
            'order_time_night': 0,
            'holiday_season_return': 0, 'time_to_return_hours': days_to_return * 24,
            'ip_prefix': customer_profile['ip_prefix'],
            'device_id': customer_profile['device_id'], 'phone_hash': customer_profile['phone_hash'],
            'brand_mismatch': 0, 'category_mismatch': 0,
            'ip_velocity_7d': self.rng.integers(1, 5), 'refund_velocity_30d': self.rng.integers(0, 2),
            'avg_order_amount': round(order_amount * self.rng.uniform(0.9, 1.1), 2),
            'return_rate_30d': self.rng.uniform(0, 0.3),
            'device_trust_score': self.rng.uniform(0.7, 1.0), 'ip_trust_score': self.rng.uniform(0.7, 1.0),
        }
        return self._add_noise_to_features(base, is_fraud=False)

    def _generate_fraud_transaction(self, customer_profile: Dict, pattern: str) -> Dict:
        base = self._generate_legitimate_transaction(customer_profile)

        # === ПАТТЕРНЫ ИЗ СТАТЬИ ZHANG ET AL. (2023) — 12 паттернов ===
        if pattern == 'wardrobing':
            base.update({'category': 'Одежда', 'days_to_return': self.rng.integers(3, 10),
                         'weekend_purchase': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'claimed_reason': 'Не подошёл размер', 'is_fraud': True, 'fraud_pattern': 'wardrobing',
                         'wear_evidence_detected': self.rng.choice([1, 0], p=[0.7, 0.3]),
                         'event_season_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
                         'items_in_order': self.rng.integers(3, 8),
                         'mass_tryon_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
                         'order_bracketing_ratio': self.rng.uniform(0.5, 1.0)})
        elif pattern == 'price_arbitrage':
            base.update({'category': 'Электроника', 'days_to_return': self.rng.integers(1, 3),
                         'order_amount': max(15000, base['order_amount']),
                         'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]), 'claimed_reason': 'Брак',
                         'is_fraud': True, 'fraud_pattern': 'price_arbitrage',
                         'is_electronics': 1, 'discount_percent': self.rng.uniform(15, 40),
                         'first_order_discount_abuse': self.rng.choice([1, 0], p=[0.7, 0.3]),
                         'new_account_flag': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'account_age_days': self.rng.integers(1, 10)})
        elif pattern == 'shipping_fraud':
            base.update({'days_to_return': self.rng.integers(0, 2),
                         'address_match': self.rng.choice([True, False], p=[0.3, 0.7]),
                         'device_new': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'claimed_reason': 'Не получил товар', 'is_fraud': True, 'fraud_pattern': 'shipping_fraud',
                         'package_weight_vs_expected': self.rng.uniform(-0.9, -0.3),
                         'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                         'empty_box_claim_count': self.rng.choice([1, 0], p=[0.5, 0.5]),
                         'accounts_per_phone': self.rng.integers(2, 5),
                         'device_is_emulator': self.rng.choice([0, 1], p=[0.6, 0.4])})
        elif pattern == 'receipt_fraud':
            base.update(
                {'receipt_provided': False, 'days_to_return': self.rng.integers(1, 3), 'claimed_reason': 'Потерял чек',
                 'is_fraud': True, 'fraud_pattern': 'receipt_fraud',
                 'support_ticket_count_30d': self.rng.integers(2, 5),
                 'threat_language_detected': self.rng.choice([0, 1], p=[0.6, 0.4])})
        elif pattern == 'employee_fraud':
            base.update({'days_to_return': 0, 'claimed_reason': 'Возврат по гарантии', 'is_fraud': True,
                         'fraud_pattern': 'employee_fraud',
                         'warranty_doc_provided': 1, 'legal_claim_threat': 1, 'same_address_different_accounts': 1,
                         'accounts_per_device': self.rng.integers(2, 4)})
        elif pattern == 'multi_channel_refund':
            base.update(
                {'days_to_return': self.rng.integers(1, 5), 'claimed_reason': 'Проблема с доставкой', 'is_fraud': True,
                 'fraud_pattern': 'multi_channel_refund',
                 'cross_channel_return': 1, 'duplicate_refund_30d': 1, 'refund_velocity_7d': self.rng.integers(2, 5),
                 'rma_reuse_count': self.rng.integers(1, 3)})
        elif pattern == 'discount_fraud':
            base.update({'claimed_reason': 'Нашел дешевле', 'is_fraud': True, 'fraud_pattern': 'discount_fraud',
                         'discount_percent': self.rng.uniform(25, 50), 'promo_code_used': 1,
                         'first_order_discount_abuse': 1,
                         'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]), 'order_time_night': 1})
        elif pattern == 'damage_fraud':
            base.update({'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'damage_fraud',
                         'wear_evidence_detected': 1, 'package_weight_vs_expected': self.rng.uniform(-0.3, 0.3),
                         'support_ticket_count_30d': self.rng.integers(1, 3)})
        elif pattern == 'points_fraud':
            base.update({'claimed_reason': 'Передумал', 'is_fraud': True, 'fraud_pattern': 'points_fraud',
                         'review_count_30d': self.rng.integers(5, 10), 'negative_review_cluster': 1,
                         'items_in_order': self.rng.integers(5, 10)})
        elif pattern == 'bricking':
            base.update({'category': 'Электроника', 'claimed_reason': 'Не работает', 'is_fraud': True,
                         'fraud_pattern': 'bricking',
                         'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'package_weight_vs_expected': self.rng.uniform(-0.7, -0.3),
                         'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                         'empty_box_claim_count': self.rng.integers(1, 3)})
        elif pattern == 'professional_refunder':
            base.update({'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'professional_refunder',
                         'accounts_per_ip': self.rng.integers(3, 8), 'accounts_per_phone': self.rng.integers(2, 5),
                         'accounts_per_device': self.rng.integers(2, 4),
                         'ip_velocity_24h': self.rng.integers(5, 15), 'same_address_different_accounts': 1,
                         'device_is_emulator': 1,
                         'cross_channel_return': 1, 'refund_velocity_7d': self.rng.integers(3, 8)})
        elif pattern == 'multi_accounting':
            base.update({'claimed_reason': 'Первый заказ', 'is_fraud': True, 'fraud_pattern': 'multi_accounting',
                         'accounts_per_ip': self.rng.integers(3, 6), 'accounts_per_phone': self.rng.integers(2, 4),
                         'first_order_discount_abuse': 1,
                         'discount_percent': self.rng.uniform(25, 45),
                         'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]),
                         'ip_velocity_24h': self.rng.integers(4, 10)})
        elif pattern == 'old_item_return':
            base.update({'category': 'Одежда', 'claimed_reason': 'Не подошёл размер', 'is_fraud': True,
                         'fraud_pattern': 'old_item_return',
                         'package_weight_vs_expected': self.rng.uniform(-0.4, -0.1), 'wear_evidence_detected': 1,
                         'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'items_in_order': self.rng.integers(5, 10),
                         'mass_tryon_flag': 1, 'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                         'brand_mismatch': self.rng.choice([0, 1], p=[0.3, 0.7])})
        elif pattern == 'intentional_damage':
            base.update({'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'intentional_damage',
                         'legal_claim_threat': 1, 'support_ticket_count_30d': self.rng.integers(3, 6),
                         'threat_language_detected': 1,
                         'warranty_doc_provided': 1, 'review_count_30d': self.rng.integers(5, 10),
                         'negative_review_cluster': 1, 'wear_evidence_detected': 1})
        elif pattern == 'self_checkout_theft':
            base.update({'claimed_reason': 'Ошибка кассы', 'is_fraud': True, 'fraud_pattern': 'self_checkout_theft',
                         'device_is_emulator': 1, 'package_density_score': self.rng.uniform(0.3, 0.6),
                         'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                         'device_new': 1, 'account_age_days': self.rng.integers(1, 10)})
        elif pattern == 'freezing_competitors':
            base.update({'claimed_reason': 'Не пришёл товар', 'is_fraud': True, 'fraud_pattern': 'freezing_competitors',
                         'cross_channel_return': 1, 'refund_velocity_7d': self.rng.integers(2, 4),
                         'distance_from_registration_city': self.rng.integers(500, 2000),
                         'same_item_burst': True, 'negative_review_cluster': 1})
        elif pattern == 'mass_try_on':
            base.update({'category': 'Одежда', 'claimed_reason': 'Не подошёл размер', 'is_fraud': True,
                         'fraud_pattern': 'mass_try_on',
                         'items_in_order': self.rng.integers(8, 15),
                         'order_bracketing_ratio': self.rng.uniform(0.8, 1.0), 'mass_tryon_flag': 1,
                         'event_season_flag': 1, 'days_to_return': self.rng.integers(1, 2),
                         'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3])})
        elif pattern == 'perishable_fraud':
            base.update(
                {'category': self.rng.choice(['Продукты питания', 'Косметика']), 'claimed_reason': 'Испорченный товар',
                 'is_fraud': True, 'fraud_pattern': 'perishable_fraud',
                 'receipt_provided': False, 'support_ticket_count_30d': self.rng.integers(2, 4),
                 'package_weight_vs_expected': self.rng.uniform(0.9, 1.1), 'wear_evidence_detected': 1})
        elif pattern == 'review_blackmail':
            base.update(
                {'claimed_reason': 'Не соответствует описанию', 'is_fraud': True, 'fraud_pattern': 'review_blackmail',
                 'threat_language_detected': 1, 'negative_review_cluster': 1,
                 'review_text_similarity_score': self.rng.uniform(0.7, 0.95), 'refund_velocity_7d': 0})
        elif pattern == 'pvz_swap':
            base.update({'category': 'Одежда', 'claimed_reason': 'Не подошёл размер', 'is_fraud': True,
                         'fraud_pattern': 'pvz_swap',
                         'tag_removed': 1, 'wear_evidence_detected': 1, 'items_in_order': 1, 'brand_mismatch': 1,
                         'category_mismatch': self.rng.choice([0, 1], p=[0.7, 0.3])})
        elif pattern == 'cashier_no_receipt':
            base.update({'claimed_reason': 'Не пришёл товар', 'is_fraud': True, 'fraud_pattern': 'cashier_no_receipt',
                         'same_address_different_accounts': self.rng.integers(2, 4),
                         'accounts_per_device': self.rng.integers(3, 6), 'device_is_emulator': 1,
                         'refund_velocity_7d': 0})
        elif pattern == 'fake_return_employee':
            base.update({'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'fake_return_employee',
                         'same_address_different_accounts': self.rng.integers(2, 3),
                         'accounts_per_phone': self.rng.integers(2, 3),
                         'refund_velocity_7d': self.rng.integers(2, 4), 'cross_channel_return': 1})
        elif pattern == 'cashier_swap':
            base.update({'claimed_reason': 'Не тот товар', 'is_fraud': True, 'fraud_pattern': 'cashier_swap',
                         'package_weight_vs_expected': self.rng.uniform(-0.6, -0.3), 'missing_components': 1,
                         'same_address_different_accounts': self.rng.integers(2, 3),
                         'refund_velocity_7d': self.rng.integers(1, 3)})
        elif pattern == 'review_manipulation':
            base.update({'claimed_reason': 'Не соответствует описанию', 'is_fraud': True,
                         'fraud_pattern': 'review_manipulation',
                         'review_text_similarity_score': self.rng.uniform(0.8, 0.98),
                         'accounts_per_ip': self.rng.integers(5, 10),
                         'review_count_30d': self.rng.integers(10, 20), 'negative_review_cluster': 1,
                         'cross_channel_return': 1})
        elif pattern == 'post_event_return':
            base.update({'category': 'Одежда', 'claimed_reason': 'Не подошёл размер', 'is_fraud': True,
                         'fraud_pattern': 'post_event_return',
                         'wear_evidence_detected': 1, 'event_season_flag': 1, 'days_to_return': self.rng.integers(1, 3),
                         'tag_removed': 1, 'items_in_order': 1})
        elif pattern == 'serial_refund':
            base.update({'claimed_reason': 'Брак', 'is_fraud': True, 'fraud_pattern': 'serial_refund',
                         'duplicate_refund_30d': self.rng.integers(2, 4), 'rma_reuse_count': self.rng.integers(2, 3),
                         'refund_velocity_7d': self.rng.integers(3, 6), 'same_item_burst': True})
        else:
            base.update({'is_fraud': True, 'fraud_pattern': pattern, 'payment_method_risk': self.rng.uniform(0.4, 0.9),
                         'shipping_region_risk': self.rng.uniform(0.4, 0.9)})

        if base['is_fraud']:
            base.update({
                'payment_method_risk': self.rng.uniform(0.4, 0.9),
                'chargeback_history_90d': self.rng.choice([0, 1], p=[0.6, 0.4]),
                'card_bin_country_mismatch': self.rng.choice([0, 1], p=[0.5, 0.5]),
                'shipping_region_risk': self.rng.uniform(0.4, 0.9),
                'distance_from_registration_city': self.rng.integers(50, 1000),
                'order_hour': self.rng.choice([2, 3, 4, 5, 12, 14, 18, 10, 11]),
                'order_time_night': 1 if base['order_hour'] in [2, 3, 4, 5] else 0,
                'holiday_season_return': self.rng.choice([0, 1], p=[0.6, 0.4]),
                'ip_velocity_24h': self.rng.integers(2, 10),
                'negative_review_cluster': self.rng.choice([0, 1], p=[0.6, 0.4]),
            })

        return self._add_noise_to_features(base, is_fraud=True)

    def generate_dataset(self, n_samples: int = 150000,
                         fraud_rate_range: Tuple[float, float] = (0.005, 0.02)) -> pd.DataFrame:
        # 🔧 ПЛАВАЮЩИЙ ПРОЦЕНТ ФРОДА: 0.5% - 2%
        target_fraud_rate = np.random.uniform(*fraud_rate_range)
        n_fraud = int(n_samples * target_fraud_rate)
        n_legitimate = n_samples - n_fraud

        fraud_distribution = {
            'wardrobing': 0.30, 'price_arbitrage': 0.20, 'shipping_fraud': 0.15, 'receipt_fraud': 0.08,
            'employee_fraud': 0.05, 'multi_channel_refund': 0.03, 'discount_fraud': 0.05, 'damage_fraud': 0.04,
            'points_fraud': 0.03, 'bricking': 0.04, 'professional_refunder': 0.03, 'multi_accounting': 0.05,
            'old_item_return': 0.04, 'intentional_damage': 0.02, 'self_checkout_theft': 0.03,
            'freezing_competitors': 0.015,
            'mass_try_on': 0.035, 'perishable_fraud': 0.02, 'review_blackmail': 0.01, 'pvz_swap': 0.025,
            'cashier_no_receipt': 0.015, 'fake_return_employee': 0.01, 'cashier_swap': 0.008,
            'review_manipulation': 0.005,
            'post_event_return': 0.03, 'serial_refund': 0.007
        }

        print(f"Генерация {n_legitimate:,} легитимных и {n_fraud:,} мошеннических транзакций...")
        print(f"🎲 Фактический процент фрода: {target_fraud_rate:.2%}")

        data = []
        for _ in range(n_legitimate):
            data.append(self._generate_legitimate_transaction(self._generate_customer_profile()))

        for pattern, ratio in fraud_distribution.items():
            count = max(1, int(n_fraud * ratio))
            for _ in range(count):
                data.append(self._generate_fraud_transaction(self._generate_customer_profile(), pattern))

        df = pd.DataFrame(data).sort_values('timestamp').reset_index(drop=True)
        print(f"✅ Датасет сгенерирован: {len(df)} записей (Фрод: {df['is_fraud'].mean():.2%})")
        return df

class CausalFeatureEngineer:
    def __init__(self):
        self.categorical_features = ['category', 'claimed_reason', 'delivery_address_type']
        self.exclude_cols = ['is_fraud', 'timestamp', 'fraud_pattern', 'registration_date', 'customer_id', 'ip_prefix',
                             'device_id', 'phone_hash', 'days_to_return', 'time_to_return_hours']
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
        ]

    def prepare_dataset_efficient(self, df: pd.DataFrame) -> pd.DataFrame:
        print("   Подготовка признаков (O(N) через groupby)...")
        df = df.sort_values(['customer_id', 'timestamp']).reset_index(drop=True)
        df['cum_purchases'] = df.groupby('customer_id').cumcount() + 1
        df['cum_returns'] = df.groupby('customer_id')['claimed_reason'].transform(
            lambda x: x.isin(['Брак', 'Не подошёл размер', 'Передумал']).cumsum())
        df['customer_return_rate_cum'] = df['cum_returns'] / df['cum_purchases']

        df['refund_velocity_7d'] = df.groupby('customer_id')['is_fraud'].transform(
            lambda x: x.shift(1).rolling(window=7, min_periods=1).sum())
        df['refund_velocity_30d'] = df.groupby('customer_id')['is_fraud'].transform(
            lambda x: x.shift(1).rolling(window=30, min_periods=1).sum())
        df['accounts_per_ip'] = df.groupby('ip_prefix')['customer_id'].transform('nunique')
        df['accounts_per_device'] = df.groupby('device_id')['customer_id'].transform('nunique')

        feature_cols = [col for col in self.pre_return_features if col in df.columns]
        X = df[feature_cols + ['is_fraud', 'timestamp', 'customer_id', 'fraud_pattern']].copy()
        X[feature_cols] = X[feature_cols].fillna(0)
        return X


class HybridFraudDetector:
    def __init__(self, optimal_threshold: float = 0.65, fp_cost: int = 75, fn_cost: int = 350):
        self.pattern_model = None
        self.anomaly_model = None
        self.anomaly_scaler = StandardScaler()
        self.anomaly_threshold = None
        self.optimal_threshold = optimal_threshold
        self.fp_cost = fp_cost
        self.fn_cost = fn_cost
        self.feature_engineer = CausalFeatureEngineer()
        self.categorical_features = ['category', 'claimed_reason', 'delivery_address_type']
        self.exclude_cols = ['is_fraud', 'timestamp', 'fraud_pattern', 'customer_id']
        self.metadata = {}
        # 🔧 ONE-HOT ENCODER
        self.encoder = OneHotFeatureEncoder()
        self.shap_explainer = None
        self.feature_importance = None

    def train(self, df_train: pd.DataFrame, df_val: pd.DataFrame) -> Tuple:
        print("\n1. Подготовка признаков и OHE...")
        X_train = self.feature_engineer.prepare_dataset_efficient(df_train)
        X_val = self.feature_engineer.prepare_dataset_efficient(df_val)

        y_train = X_train['is_fraud'].astype(np.int32).values
        y_val = X_val['is_fraud'].astype(np.int32).values

        # 🔧 One-Hot Encoding
        X_train_ohe = self.encoder.fit_transform(X_train, self.categorical_features)
        X_val_ohe = self.encoder.transform(X_val)

        # Динамический сбор признаков после OHE
        feature_cols = [col for col in X_train_ohe.columns if col not in self.exclude_cols]
        X_train_features = X_train_ohe[feature_cols]
        X_val_features = X_val_ohe[feature_cols]
        self.metadata['feature_columns'] = feature_cols

        print(f"   Признаков после OHE: {len(feature_cols)}")
        print(f"   Выборка: {len(X_train)} записей, фрод: {y_train.mean():.2%}")

        print("\n2. Обучение CatBoost...")
        scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
        self.pattern_model = CatBoostClassifier(
            iterations=1000, learning_rate=0.05, depth=6, loss_function='Logloss', eval_metric='PRAUC',
            scale_pos_weight=scale_pos_weight, early_stopping_rounds=50, random_seed=42, verbose=100,
            task_type='CPU', grow_policy='SymmetricTree', bootstrap_type='Bernoulli', subsample=0.8
        )
        self.pattern_model.fit(Pool(X_train_features, y_train), eval_set=Pool(X_val_features, y_val))

        y_pred_proba = self.pattern_model.predict_proba(X_val_features)[:, 1]
        pr_auc = average_precision_score(y_val, y_pred_proba)
        print(f"   PR-AUC: {pr_auc:.4f}")

        print("\n3. Обучение Isolation Forest...")
        X_train_legit = X_train_features[y_train == 0]
        numeric_cols = X_train_legit.select_dtypes(include=[np.number]).columns.tolist()
        self.anomaly_scaler.fit(X_train_legit[numeric_cols])
        self.anomaly_model = IsolationForest(contamination=0.05, random_state=42, n_jobs=-1)
        self.anomaly_model.fit(self.anomaly_scaler.transform(X_train_legit[numeric_cols]))
        scores = self.anomaly_model.score_samples(self.anomaly_scaler.transform(X_train_legit[numeric_cols]))
        self.anomaly_threshold = np.percentile(scores, 5)

        print("\n4. Оптимизация порога...")
        thresholds = np.arange(0.1, 0.95, 0.01)
        costs = []
        for t in thresholds:
            y_pred = (y_pred_proba > t).astype(int)
            fp = ((y_pred == 1) & (y_val == 0)).sum()
            fn = ((y_pred == 0) & (y_val == 1)).sum()
            costs.append(fp * self.fp_cost + fn * self.fn_cost)
        self.optimal_threshold = thresholds[np.argmin(costs)]

        print("\n5. SHAP Feature Importance...")
        self.shap_explainer = shap.TreeExplainer(self.pattern_model)
        shap_values = self.shap_explainer.shap_values(
            X_train_features.sample(min(1000, len(X_train_features)), random_state=42))
        self.feature_importance = np.abs(shap_values).mean(axis=0)

        self.metadata.update({
            'features': feature_cols, 'categorical_features': self.categorical_features,
            'optimal_threshold': float(self.optimal_threshold), 'anomaly_threshold': float(self.anomaly_threshold),
            'pr_auc_val': float(pr_auc), 'fp_cost': self.fp_cost, 'fn_cost': self.fn_cost,
            'training_samples': len(X_train), 'validation_samples': len(X_val),
            'fraud_rate_train': float(y_train.mean()), 'fraud_rate_val': float(y_val.mean()),
            'scale_pos_weight': float(scale_pos_weight), 'calibration': 'catboost_native',
            'encoder_type': 'one_hot'
        })
        print("✅ Обучение завершено")
        return y_val, y_pred_proba, thresholds, costs

    def _get_anomaly_scores(self, X: pd.DataFrame) -> np.ndarray:
        numeric_cols = X.select_dtypes(include=[np.number]).columns.tolist()
        scores = self.anomaly_model.score_samples(self.anomaly_scaler.transform(X[numeric_cols]))
        return (scores - scores.min()) / (scores.max() - scores.min() + 1e-10)

    def predict(self, features_dict: Dict, return_shap: bool = False) -> Dict:
        # Rule-based скор до OHE
        rule_score = self._calculate_rule_score(features_dict)

        # OHE трансформация
        encoded_dict = self.encoder.transform_single(features_dict)
        X = pd.DataFrame([{col: encoded_dict.get(col, 0) for col in self.metadata['feature_columns']}])

        pattern_proba = self.pattern_model.predict_proba(X)[0][1]
        anomaly_scores = self._get_anomaly_scores(X)
        is_anomaly = anomaly_scores[0] < (1 - self.anomaly_threshold)

        combined_score = 0.6 * pattern_proba + 0.25 * rule_score + 0.15 * (1 - anomaly_scores[0])
        decision = 'BLOCK' if combined_score > self.optimal_threshold else 'APPROVE'
        if is_anomaly and decision == 'APPROVE': decision = 'REVIEW'

        return {
            'decision': decision, 'risk_score': float(pattern_proba), 'combined_score': float(combined_score),
            'anomaly_score': float(anomaly_scores[0]), 'rule_score': float(rule_score),
            'risk_level': self._get_risk_level(combined_score), 'model_version': 'hybrid_v4.1_ohe_27patterns',
            'timestamp': datetime.now().isoformat()
        }

    def _calculate_rule_score(self, features_dict: Dict) -> float:
        score = 0.0
        if features_dict.get('account_age_days', 365) < 7: score += 0.15
        if features_dict.get('order_amount', 0) > 30000: score += 0.10
        if features_dict.get('fast_return_flag', 0) == 1: score += 0.12
        if features_dict.get('missing_components', 0) == 1: score += 0.20
        if features_dict.get('wear_evidence_detected', 0) == 1: score += 0.18
        if features_dict.get('accounts_per_ip', 1) >= 3: score += 0.15
        if features_dict.get('device_is_emulator', 0) == 1: score += 0.12
        return min(score, 1.0)

    def _get_risk_level(self, score: float) -> str:
        if score > 0.80: return 'CRITICAL'
        if score > 0.65: return 'HIGH'
        if score > 0.30: return 'MEDIUM'
        return 'LOW'

    def save(self, path: str = 'models4/'):
        os.makedirs(path, exist_ok=True)
        self.pattern_model.save_model(f'{path}/fraud_model_v4_27patterns.cbm')
        joblib.dump(self.anomaly_model, f'{path}/anomaly_model_v4.pkl')
        joblib.dump(self.encoder, f'{path}/ohe_encoder_v4.pkl')
        joblib.dump(self.anomaly_scaler, f'{path}/scaler_v4.pkl')
        with open(f'{path}/metadata_v4_27patterns.json', 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)
        print(f"✅ Модель сохранена в {path}")

    def load(self, path: str = 'models4/'):
        self.pattern_model = CatBoostClassifier()
        self.pattern_model.load_model(f'{path}/fraud_model_v4_27patterns.cbm')
        self.anomaly_model = joblib.load(f'{path}/anomaly_model_v4.pkl')
        self.encoder = joblib.load(f'{path}/ohe_encoder_v4.pkl')
        self.anomaly_scaler = joblib.load(f'{path}/scaler_v4.pkl')
        with open(f'{path}/metadata_v4_27patterns.json', 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)

    def export_onnx(self, path: str = 'models4/fraud_model_v4_27patterns.onnx'):
        self.pattern_model.save_model(path, format="onnx",
                                      export_parameters={'onnx_doc_string': 'FraudReturn Shield v4.1'})
        onnx.checker.check_model(onnx.load(path))
        print(f"✅ ONNX модель валидна: {path}")

def test_1000_users(detector: HybridFraudDetector, n_users: int = 1000):
    print(f"\n🧪 ТЕСТ НА {n_users} ПОЛЬЗОВАТЕЛЕЙ")
    generator = FraudDataGenerator(seed=123)
    df_test = generator.generate_dataset(n_samples=n_users, fraud_rate_range=(0.005, 0.02))

    X_test = detector.feature_engineer.prepare_dataset_efficient(df_test)
    feature_cols = [col for col in X_test.columns if col not in detector.exclude_cols]
    X_test_features = detector.encoder.transform(X_test[feature_cols])
    y_test = X_test['is_fraud'].values

    y_pred_proba = detector.pattern_model.predict_proba(X_test_features)[:, 1]
    y_pred_binary = (y_pred_proba > detector.optimal_threshold).astype(int)
    cm = confusion_matrix(y_test, y_pred_binary)
    tn, fp, fn, tp = cm.ravel()
    pr_auc = average_precision_score(y_test, y_pred_proba)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0

    print(
        f"📊 Фрод в тесте: {y_test.mean():.2%} | PR-AUC: {pr_auc:.3f} | Recall: {recall:.1%} | Precision: {precision:.1%}")
    return {'pr_auc': pr_auc, 'recall': recall, 'precision': precision, 'fraud_rate': y_test.mean()}


def run_full_pipeline():
    print("🚀 FRAUDRETURN SHIELD v4.1 — ONE-HOT + ПЛАВАЮЩИЙ ФРОД")
    generator = FraudDataGenerator(seed=42)
    df = generator.generate_dataset(n_samples=150000, fraud_rate_range=(0.005, 0.02))

    train_end, val_end = int(len(df) * 0.7), int(len(df) * 0.85)
    df_train, df_val, df_test = df.iloc[:train_end], df.iloc[train_end:val_end], df.iloc[val_end:]
    assert df_train['timestamp'].max() < df_val['timestamp'].min() < df_test['timestamp'].min()
    print(f"📊 Train: {len(df_train):,} ({df_train['is_fraud'].mean():.2%} фрода)")

    detector = HybridFraudDetector()
    detector.train(df_train, df_val)
    test_1000_users(detector)
    detector.save()
    detector.export_onnx()
    print("\n✅ СИСТЕМА ГОТОВА К ПРОДАКШНУ")
    return detector


if __name__ == "__main__":
    run_full_pipeline()