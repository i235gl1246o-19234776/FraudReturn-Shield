import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import uuid


class FraudDataGenerator:
    def __init__(self, seed=42):
        self.rng = np.random.default_rng(seed)
        self.categories = ['Одежда', 'Электроника', 'Продукты питания', 'Косметика', 'Дом и сад', 'Спорт']
        self.reasons = ['Не подошёл размер', 'Брак', 'Не получил товар', 'Передумал', 'Не соответствует описанию']

    def generate_base_user(self):
        """Генерирует базового пользователя"""
        return {
            'user_id': str(uuid.uuid4()),
            'account_age_days': self.rng.integers(30, 365),
            'total_orders': self.rng.integers(1, 50),
            'order_amount': round(self.rng.uniform(1000, 10000), 2),
            'device_id': str(uuid.uuid4()),
            'ip_address': f"{self.rng.integers(1, 255)}.{self.rng.integers(0, 255)}.{self.rng.integers(0, 255)}.{self.rng.integers(0, 255)}",
            'registration_city': 'Москва',
            'order_hour': self.rng.integers(8, 22),
            'category': self.rng.choice(self.categories),
            'days_to_return': self.rng.integers(3, 14),
            'claimed_reason': self.rng.choice(self.reasons),
            'is_fraud': False,
            'fraud_pattern': 'none',
            'wear_evidence_detected': 0,
            'tag_removed': False,
            'missing_components': False,
            'receipt_provided': True,
            'device_is_emulator': 0,
            'accounts_per_ip': 1,
            'accounts_per_phone': 1,
            'accounts_per_device': 1,
            'refund_velocity_7d': 0,
            'support_ticket_count_30d': 0,
            'negative_review_cluster': 0,
            'threat_language_detected': 0,
            'cross_channel_return': 0,
            'same_address_different_accounts': 0,
            'package_weight_vs_expected': 1.0,
            'xray_scan_anomaly': 0,
            'brand_mismatch': 0,
            'review_text_similarity_score': 0.0,
            'order_bracketing_ratio': 0.1,
            'mass_tryon_flag': 0,
            'event_season_flag': 0,
            'items_in_order': 1,
            'discount_percent': 0.0,
            'first_order_discount_abuse': 0,
            'promo_code_used': 0,
            'order_time_night': 0,
            'legal_claim_threat': 0,
            'warranty_doc_provided': 0,
            'duplicate_refund_30d': 0,
            'rma_reuse_count': 0,
            'empty_box_claim_count': 0,
            'ip_velocity_24h': 0,
            'review_count_30d': 0,
            'distance_from_registration_city': 0,
            'same_item_burst': False,
            'package_density_score': 1.0,
            'category_mismatch': 0
        }

    def apply_pattern(self, base, pattern):
        """Применяет логику паттерна к базовой записи (на основе вашего кода)"""
        if pattern == 'wardrobing':
            base.update({
                'category': 'Одежда',
                'days_to_return': self.rng.integers(3, 10),
                'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
                'claimed_reason': 'Не подошёл размер',
                'is_fraud': True,
                'fraud_pattern': 'wardrobing',
                'wear_evidence_detected': self.rng.choice([1, 0], p=[0.7, 0.3]),
                'event_season_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'items_in_order': self.rng.integers(3, 8),
                'mass_tryon_flag': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'order_bracketing_ratio': round(self.rng.uniform(0.5, 1.0), 2)
            })
        elif pattern == 'price_arbitrage':
            base.update({
                'category': 'Электроника',
                'days_to_return': self.rng.integers(1, 3),
                'order_amount': max(15000, base['order_amount']),
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'price_arbitrage',
                'discount_percent': round(self.rng.uniform(15, 40), 2),
                'first_order_discount_abuse': self.rng.choice([1, 0], p=[0.7, 0.3]),
                'account_age_days': self.rng.integers(1, 10)
            })
        elif pattern == 'shipping_fraud':
            base.update({
                'days_to_return': self.rng.integers(0, 2),
                'claimed_reason': 'Не получил товар',
                'is_fraud': True,
                'fraud_pattern': 'shipping_fraud',
                'package_weight_vs_expected': round(self.rng.uniform(-0.9, -0.3), 2),
                'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'empty_box_claim_count': self.rng.choice([1, 0], p=[0.5, 0.5]),
                'accounts_per_phone': self.rng.integers(2, 5),
                'device_is_emulator': self.rng.choice([0, 1], p=[0.6, 0.4])
            })
        elif pattern == 'receipt_fraud':
            base.update({
                'receipt_provided': False,
                'days_to_return': self.rng.integers(1, 3),
                'claimed_reason': 'Потерял чек',
                'is_fraud': True,
                'fraud_pattern': 'receipt_fraud',
                'support_ticket_count_30d': self.rng.integers(2, 5),
                'threat_language_detected': self.rng.choice([0, 1], p=[0.6, 0.4])
            })
        elif pattern == 'employee_fraud':
            base.update({
                'days_to_return': 0,
                'claimed_reason': 'Возврат по гарантии',
                'is_fraud': True,
                'fraud_pattern': 'employee_fraud',
                'warranty_doc_provided': 1,
                'legal_claim_threat': 1,
                'same_address_different_accounts': 1,
                'accounts_per_device': self.rng.integers(2, 4)
            })
        elif pattern == 'multi_channel_refund':
            base.update({
                'days_to_return': self.rng.integers(1, 5),
                'claimed_reason': 'Проблема с доставкой',
                'is_fraud': True,
                'fraud_pattern': 'multi_channel_refund',
                'cross_channel_return': 1,
                'duplicate_refund_30d': 1,
                'refund_velocity_7d': self.rng.integers(2, 5),
                'rma_reuse_count': self.rng.integers(1, 3)
            })
        elif pattern == 'discount_fraud':
            base.update({
                'claimed_reason': 'Нашел дешевле',
                'is_fraud': True,
                'fraud_pattern': 'discount_fraud',
                'discount_percent': round(self.rng.uniform(25, 50), 2),
                'promo_code_used': 1,
                'first_order_discount_abuse': 1,
                'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]),
                'order_time_night': 1
            })
        elif pattern == 'damage_fraud':
            base.update({
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'damage_fraud',
                'wear_evidence_detected': 1,
                'package_weight_vs_expected': round(self.rng.uniform(-0.3, 0.3), 2),
                'support_ticket_count_30d': self.rng.integers(1, 3)
            })
        elif pattern == 'points_fraud':
            base.update({
                'claimed_reason': 'Передумал',
                'is_fraud': True,
                'fraud_pattern': 'points_fraud',
                'review_count_30d': self.rng.integers(5, 10),
                'negative_review_cluster': 1,
                'items_in_order': self.rng.integers(5, 10)
            })
        elif pattern == 'bricking':
            base.update({
                'category': 'Электроника',
                'claimed_reason': 'Не работает',
                'is_fraud': True,
                'fraud_pattern': 'bricking',
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'package_weight_vs_expected': round(self.rng.uniform(-0.7, -0.3), 2),
                'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'empty_box_claim_count': self.rng.integers(1, 3)
            })
        elif pattern == 'professional_refunder':
            base.update({
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'professional_refunder',
                'accounts_per_ip': self.rng.integers(3, 8),
                'accounts_per_phone': self.rng.integers(2, 5),
                'accounts_per_device': self.rng.integers(2, 4),
                'ip_velocity_24h': self.rng.integers(5, 15),
                'same_address_different_accounts': 1,
                'device_is_emulator': 1,
                'cross_channel_return': 1,
                'refund_velocity_7d': self.rng.integers(3, 8)
            })
        elif pattern == 'multi_accounting':
            base.update({
                'claimed_reason': 'Первый заказ',
                'is_fraud': True,
                'fraud_pattern': 'multi_accounting',
                'accounts_per_ip': self.rng.integers(3, 6),
                'accounts_per_phone': self.rng.integers(2, 4),
                'first_order_discount_abuse': 1,
                'discount_percent': round(self.rng.uniform(25, 45), 2),
                'order_hour': self.rng.choice([0, 1, 2, 3, 22, 23]),
                'ip_velocity_24h': self.rng.integers(4, 10)
            })
        elif pattern == 'old_item_return':
            base.update({
                'category': 'Одежда',
                'claimed_reason': 'Не подошёл размер',
                'is_fraud': True,
                'fraud_pattern': 'old_item_return',
                'package_weight_vs_expected': round(self.rng.uniform(-0.4, -0.1), 2),
                'wear_evidence_detected': 1,
                'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
                'items_in_order': self.rng.integers(5, 10),
                'mass_tryon_flag': 1,
                'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'brand_mismatch': self.rng.choice([0, 1], p=[0.3, 0.7])
            })
        elif pattern == 'intentional_damage':
            base.update({
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'intentional_damage',
                'legal_claim_threat': 1,
                'support_ticket_count_30d': self.rng.integers(3, 6),
                'threat_language_detected': 1,
                'warranty_doc_provided': 1,
                'review_count_30d': self.rng.integers(5, 10),
                'negative_review_cluster': 1,
                'wear_evidence_detected': 1
            })
        elif pattern == 'self_checkout_theft':
            base.update({
                'claimed_reason': 'Ошибка кассы',
                'is_fraud': True,
                'fraud_pattern': 'self_checkout_theft',
                'device_is_emulator': 1,
                'package_density_score': round(self.rng.uniform(0.3, 0.6), 2),
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'device_new': 1,
                'account_age_days': self.rng.integers(1, 10)
            })
        elif pattern == 'freezing_competitors':
            base.update({
                'claimed_reason': 'Не пришёл товар',
                'is_fraud': True,
                'fraud_pattern': 'freezing_competitors',
                'cross_channel_return': 1,
                'refund_velocity_7d': self.rng.integers(2, 4),
                'distance_from_registration_city': self.rng.integers(500, 2000),
                'same_item_burst': True,
                'negative_review_cluster': 1
            })
        elif pattern == 'mass_try_on':
            base.update({
                'category': 'Одежда',
                'claimed_reason': 'Не подошёл размер',
                'is_fraud': True,
                'fraud_pattern': 'mass_try_on',
                'items_in_order': self.rng.integers(8, 15),
                'order_bracketing_ratio': round(self.rng.uniform(0.8, 1.0), 2),
                'mass_tryon_flag': 1,
                'event_season_flag': 1,
                'days_to_return': self.rng.integers(1, 2),
                'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3])
            })
        elif pattern == 'perishable_fraud':
            base.update({
                'category': self.rng.choice(['Продукты питания', 'Косметика']),
                'claimed_reason': 'Испорченный товар',
                'is_fraud': True,
                'fraud_pattern': 'perishable_fraud',
                'receipt_provided': False,
                'support_ticket_count_30d': self.rng.integers(2, 4),
                'package_weight_vs_expected': round(self.rng.uniform(0.9, 1.1), 2),
                'wear_evidence_detected': 1
            })
        elif pattern == 'review_blackmail':
            base.update({
                'claimed_reason': 'Не соответствует описанию',
                'is_fraud': True,
                'fraud_pattern': 'review_blackmail',
                'threat_language_detected': 1,
                'negative_review_cluster': 1,
                'review_text_similarity_score': round(self.rng.uniform(0.7, 0.95), 2),
                'refund_velocity_7d': 0
            })
        elif pattern == 'pvz_swap':
            base.update({
                'category': 'Одежда',
                'claimed_reason': 'Не подошёл размер',
                'is_fraud': True,
                'fraud_pattern': 'pvz_swap',
                'tag_removed': 1,
                'wear_evidence_detected': 1,
                'items_in_order': 1,
                'brand_mismatch': 1,
                'category_mismatch': self.rng.choice([0, 1], p=[0.7, 0.3])
            })
        elif pattern == 'cashier_no_receipt':
            base.update({
                'claimed_reason': 'Не пришёл товар',
                'is_fraud': True,
                'fraud_pattern': 'cashier_no_receipt',
                'same_address_different_accounts': self.rng.integers(2, 4),
                'accounts_per_device': self.rng.integers(3, 6),
                'device_is_emulator': 1,
                'refund_velocity_7d': 0
            })
        elif pattern == 'fake_return_employee':
            base.update({
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'fake_return_employee',
                'same_address_different_accounts': self.rng.integers(2, 3),
                'accounts_per_phone': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(2, 4),
                'cross_channel_return': 1
            })
        elif pattern == 'cashier_swap':
            base.update({
                'claimed_reason': 'Не тот товар',
                'is_fraud': True,
                'fraud_pattern': 'cashier_swap',
                'package_weight_vs_expected': round(self.rng.uniform(-0.6, -0.3), 2),
                'missing_components': 1,
                'same_address_different_accounts': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(1, 3)
            })
        elif pattern == 'review_manipulation':
            base.update({
                'claimed_reason': 'Не соответствует описанию',
                'is_fraud': True,
                'fraud_pattern': 'review_manipulation',
                'review_text_similarity_score': round(self.rng.uniform(0.8, 0.98), 2),
                'accounts_per_ip': self.rng.integers(5, 10),
                'review_count_30d': self.rng.integers(10, 20),
                'negative_review_cluster': 1,
                'cross_channel_return': 1
            })
        elif pattern == 'post_event_return':
            base.update({
                'category': 'Одежда',
                'claimed_reason': 'Не подошёл размер',
                'is_fraud': True,
                'fraud_pattern': 'post_event_return',
                'wear_evidence_detected': 1,
                'event_season_flag': 1,
                'days_to_return': self.rng.integers(1, 3),
                'tag_removed': 1,
                'items_in_order': 1
            })
        elif pattern == 'serial_refund':
            base.update({
                'claimed_reason': 'Брак',
                'is_fraud': True,
                'fraud_pattern': 'serial_refund',
                'duplicate_refund_30d': self.rng.integers(2, 4),
                'rma_reuse_count': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(3, 6),
                'same_item_burst': True
            })
        else:
            base.update({
                'is_fraud': True,
                'fraud_pattern': pattern,
                'payment_method_risk': round(self.rng.uniform(0.4, 0.9), 2),
                'shipping_region_risk': round(self.rng.uniform(0.4, 0.9), 2)
            })
        return base

    def generate_dataset(self, records_per_pattern=10):
        """Генерирует полный датасет"""
        patterns = [
            'wardrobing', 'price_arbitrage', 'shipping_fraud', 'receipt_fraud',
            'employee_fraud', 'multi_channel_refund', 'discount_fraud', 'damage_fraud',
            'points_fraud', 'bricking', 'professional_refunder', 'multi_accounting',
            'old_item_return', 'intentional_damage', 'self_checkout_theft',
            'freezing_competitors', 'mass_try_on', 'perishable_fraud',
            'review_blackmail', 'pvz_swap', 'cashier_no_receipt', 'fake_return_employee',
            'cashier_swap', 'review_manipulation', 'post_event_return', 'serial_refund'
        ]

        data = []
        for pattern in patterns:
            for _ in range(records_per_pattern):
                base = self.generate_base_user()
                record = self.apply_pattern(base, pattern)
                data.append(record)

        return pd.DataFrame(data)


if __name__ == "__main__":
    generator = FraudDataGenerator(seed=42)

    df = generator.generate_dataset(records_per_pattern=5)

    output_file = 'fraud_patterns_dataset.csv'
    df.to_csv(output_file, index=False, encoding='utf-8-sig')

    print(f"Успешно создано {len(df)} записей.")
    print(f"Файл сохранен как: {output_file}")
    print("\nРаспределение паттернов:")
    print(df['fraud_pattern'].value_counts())

    print("\n🔍 Пример первых 3 записей (транспонировано для удобства):")
    print(df.head(3).T)