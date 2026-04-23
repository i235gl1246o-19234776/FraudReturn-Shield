import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import uuid
import psycopg2
from psycopg2.extras import execute_values

conn_params = {
    'host': 'localhost',
    'port': 5432,
    'database': 'fraud_return_db',
    'user': 'postgres',
    'password': 'OmegaBloody13'
}

categories = ['Electronics', 'Clothing', 'Home', 'Books', 'Toys']
extended_categories = ['Electronics', 'Clothing', 'Home', 'Books', 'Toys', 'Food', 'Cosmetics', 'Sports', 'Garden']

regions = ['Moscow', 'SPB', 'Siberia', 'South', 'FarEast']
address_types = ['home', 'office', 'pickup', 'post_office']


class FraudDataGenerator:
    def __init__(self, seed=42):
        self.rng = np.random.default_rng(seed)
        self.categories = extended_categories
        self.reasons = [
            'Size did not fit',
            'Defective',
            'Item not received',
            'Changed mind',
            'Not as described'
        ]

    def generate_base_user(self):
        return {
            'user_id': str(uuid.uuid4()),
            'account_age_days': self.rng.integers(30, 365),
            'total_orders': self.rng.integers(1, 50),
            'order_amount': round(self.rng.uniform(1000, 10000), 2),
            'device_id': str(uuid.uuid4()),
            'ip_address': f"{self.rng.integers(1, 255)}.{self.rng.integers(0, 255)}.{self.rng.integers(0, 255)}.{self.rng.integers(0, 255)}",
            'registration_city': 'Moscow',  
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
            'package_density_score': 1.0,
            'category_mismatch': 0
        }

    def apply_pattern(self, base, pattern):
        if pattern == 'wardrobing':
            base.update({
                'category': 'Clothing',
                'days_to_return': self.rng.integers(3, 10),
                'tag_removed': self.rng.choice([True, False], p=[0.7, 0.3]),
                'claimed_reason': 'Size did not fit',
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
                'category': 'Electronics',
                'days_to_return': self.rng.integers(1, 3),
                'order_amount': max(15000, base['order_amount']),
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'claimed_reason': 'Defective',
                'is_fraud': True,
                'fraud_pattern': 'price_arbitrage',
                'discount_percent': round(self.rng.uniform(15, 40), 2),
                'first_order_discount_abuse': self.rng.choice([1, 0], p=[0.7, 0.3]),
                'account_age_days': self.rng.integers(1, 10)
            })
        elif pattern == 'shipping_fraud':
            base.update({
                'days_to_return': self.rng.integers(0, 2),
                'claimed_reason': 'Item not received',
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
                'claimed_reason': 'Lost receipt',  
                'is_fraud': True,
                'fraud_pattern': 'receipt_fraud',
                'support_ticket_count_30d': self.rng.integers(2, 5),
                'threat_language_detected': self.rng.choice([0, 1], p=[0.6, 0.4])
            })
        elif pattern == 'employee_fraud':
            base.update({
                'days_to_return': 0,
                'claimed_reason': 'Warranty return',  
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
                'claimed_reason': 'Delivery issue',  
                'is_fraud': True,
                'fraud_pattern': 'multi_channel_refund',
                'cross_channel_return': 1,
                'duplicate_refund_30d': 1,
                'refund_velocity_7d': self.rng.integers(2, 5),
                'rma_reuse_count': self.rng.integers(1, 3)
            })
        elif pattern == 'discount_fraud':
            base.update({
                'claimed_reason': 'Found cheaper',  
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
                'claimed_reason': 'Defective',
                'is_fraud': True,
                'fraud_pattern': 'damage_fraud',
                'wear_evidence_detected': 1,
                'package_weight_vs_expected': round(self.rng.uniform(-0.3, 0.3), 2),
                'support_ticket_count_30d': self.rng.integers(1, 3)
            })
        elif pattern == 'points_fraud':
            base.update({
                'claimed_reason': 'Changed mind',
                'is_fraud': True,
                'fraud_pattern': 'points_fraud',
                'review_count_30d': self.rng.integers(5, 10),
                'negative_review_cluster': 1,
                'items_in_order': self.rng.integers(5, 10)
            })
        elif pattern == 'bricking':
            base.update({
                'category': 'Electronics',
                'claimed_reason': 'Not working',  
                'is_fraud': True,
                'fraud_pattern': 'bricking',
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'package_weight_vs_expected': round(self.rng.uniform(-0.7, -0.3), 2),
                'xray_scan_anomaly': self.rng.choice([1, 0], p=[0.6, 0.4]),
                'empty_box_claim_count': self.rng.integers(1, 3)
            })
        elif pattern == 'professional_refunder':
            base.update({
                'claimed_reason': 'Defective',
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
                'claimed_reason': 'First order',  
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
                'category': 'Clothing',
                'claimed_reason': 'Size did not fit',
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
                'claimed_reason': 'Defective',
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
                'claimed_reason': 'Checkout error',  
                'is_fraud': True,
                'fraud_pattern': 'self_checkout_theft',
                'device_is_emulator': 1,
                'package_density_score': round(self.rng.uniform(0.3, 0.6), 2),
                'missing_components': self.rng.choice([True, False], p=[0.7, 0.3]),
                'account_age_days': self.rng.integers(1, 10)
            })
        elif pattern == 'freezing_competitors':
            base.update({
                'claimed_reason': 'Item not received',  
                'is_fraud': True,
                'fraud_pattern': 'freezing_competitors',
                'cross_channel_return': 1,
                'refund_velocity_7d': self.rng.integers(2, 4),
                'negative_review_cluster': 1
            })
        elif pattern == 'mass_try_on':
            base.update({
                'category': 'Clothing',
                'claimed_reason': 'Size did not fit',
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
                'category': self.rng.choice(['Food', 'Cosmetics']),
                'claimed_reason': 'Spoiled item',  
                'is_fraud': True,
                'fraud_pattern': 'perishable_fraud',
                'receipt_provided': False,
                'support_ticket_count_30d': self.rng.integers(2, 4),
                'package_weight_vs_expected': round(self.rng.uniform(0.9, 1.1), 2),
                'wear_evidence_detected': 1
            })
        elif pattern == 'review_blackmail':
            base.update({
                'claimed_reason': 'Not as described',
                'is_fraud': True,
                'fraud_pattern': 'review_blackmail',
                'threat_language_detected': 1,
                'negative_review_cluster': 1,
                'review_text_similarity_score': round(self.rng.uniform(0.7, 0.95), 2),
                'refund_velocity_7d': 0
            })
        elif pattern == 'pvz_swap':
            base.update({
                'category': 'Clothing',
                'claimed_reason': 'Size did not fit',
                'is_fraud': True,
                'fraud_pattern': 'pickup_point_swap',
                'tag_removed': 1,
                'wear_evidence_detected': 1,
                'items_in_order': 1,
                'brand_mismatch': 1,
                'category_mismatch': self.rng.choice([0, 1], p=[0.7, 0.3])
            })
        elif pattern == 'cashier_no_receipt':
            base.update({
                'claimed_reason': 'Item not received',
                'is_fraud': True,
                'fraud_pattern': 'cashier_no_receipt',
                'same_address_different_accounts': self.rng.integers(2, 4),
                'accounts_per_device': self.rng.integers(3, 6),
                'device_is_emulator': 1,
                'refund_velocity_7d': 0
            })
        elif pattern == 'fake_return_employee':
            base.update({
                'claimed_reason': 'Defective',
                'is_fraud': True,
                'fraud_pattern': 'fake_return_employee',
                'same_address_different_accounts': self.rng.integers(2, 3),
                'accounts_per_phone': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(2, 4),
                'cross_channel_return': 1
            })
        elif pattern == 'cashier_swap':
            base.update({
                'claimed_reason': 'Wrong item',  
                'is_fraud': True,
                'fraud_pattern': 'cashier_swap',
                'package_weight_vs_expected': round(self.rng.uniform(-0.6, -0.3), 2),
                'missing_components': 1,
                'same_address_different_accounts': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(1, 3)
            })
        elif pattern == 'review_manipulation':
            base.update({
                'claimed_reason': 'Not as described',
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
                'category': 'Clothing',
                'claimed_reason': 'Size did not fit',
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
                'claimed_reason': 'Defective',
                'is_fraud': True,
                'fraud_pattern': 'serial_refund',
                'duplicate_refund_30d': self.rng.integers(2, 4),
                'rma_reuse_count': self.rng.integers(2, 3),
                'refund_velocity_7d': self.rng.integers(3, 6)
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


    print(f"Successfully created {len(df)} records.")
    print("\nPattern distribution:")
    print(df['fraud_pattern'].value_counts())

    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        cur = conn.cursor()

        client_inserts = []
        order_inserts = []
        return_inserts = []
        session_inserts = []

        for _, row in df.iterrows():
            client_id = len(client_inserts) + 1
            client_inserts.append((
                row['account_age_days'],
                row['total_orders'],
                0,  
                0.0,  
                row['order_amount'],  
                0.0,  
                0,  
                row['registration_city'],
                None,  
                None,  
                str(uuid.uuid4())[:64]  
            ))

            order_timestamp = datetime.now() - timedelta(days=random.randint(0, 30), hours=row['order_hour'])
            order_inserts.append((
                client_id,
                row['order_amount'],
                row['items_in_order'],
                0.0, 
                'card',  
                order_timestamp,
                0.0,  
                0,  
                row['category'],
                bool(row['category'] == 'Electronics'),
                random.choice(regions),
                0.0,  
                row['registration_city'],
                None,  
                None,  
                None,  
                None,  
                False,  
                random.choice(address_types),
                0.0,  
                True,  
                'completed'
            ))

            return_inserts.append({
                'temp_order_index': len(order_inserts) - 1,
                'client_temp_id': client_id,
                'returns_last_30d': 0,
                'return_rate_last_30d': 0.0,
                'days_since_last_return': 0,
                'days_to_return': row['days_to_return'],
                'return_channel': 'online',
                'has_receipt': bool(row['receipt_provided']),
                'tags_removed': bool(row['tag_removed']),
                'missing_components': bool(row['missing_components']),
                'claimed_reason': row['claimed_reason']
            })

            session_inserts.append((
                client_id,
                row['ip_address'],
                row['device_id'],
                None, 
                bool(row['device_is_emulator']),
                None,  
                order_timestamp,
                False,  
                order_timestamp,
                order_timestamp
            ))

        if client_inserts:
            cur.execute("""
                INSERT INTO clients (
                    account_age_days, total_orders, total_returns, global_return_rate,
                    avg_order_amount, address_change_frequency, category_returns_count,
                    registration_city, client_lat, client_lng, phone_hash
                ) VALUES %s
                RETURNING client_id
            """, client_inserts)
            real_client_ids = [row[0] for row in cur.fetchall()]
        else:
            real_client_ids = []

        if order_inserts and real_client_ids:
            order_inserts_with_real_ids = []
            for i, order in enumerate(order_inserts):
                if i < len(real_client_ids):
                    order_with_real_id = list(order)
                    order_with_real_id[0] = real_client_ids[i]
                    order_inserts_with_real_ids.append(tuple(order_with_real_id))

        if order_inserts and real_client_ids:
            order_inserts_with_real_ids = []
            for i, order in enumerate(order_inserts):
                if i < len(real_client_ids):
                    order_with_real_id = list(order)
                    order_with_real_id[0] = real_client_ids[i]
                    order_inserts_with_real_ids.append(tuple(order_with_real_id))
                    
            execute_values(cur, """
                INSERT INTO orders (
                    client_id, order_amount, items_count, discount_amount, payment_method,
                    order_timestamp, amount_deviation, orders_last_30d, product_category,
                    is_electronics, shipping_region, region_risk_score, delivery_city,
                    delivery_lat, delivery_lng,
                    payment_card_bin, card_issuing_country, card_country_mismatch,
                    delivery_address_type, address_match_score, is_address_match, order_status
                ) VALUES %s
            """, order_inserts_with_real_ids)

        if return_inserts:
            execute_values(cur, """
                INSERT INTO returns (
                    order_id, client_id, returns_last_30d, return_rate_last_30d,
                    days_since_last_return, days_since_purchase, return_channel,
                    has_receipt, tags_removed, missing_components, claimed_reason
                ) VALUES %s
            """, return_inserts)

        if session_inserts:
            execute_values(cur, """
                INSERT INTO client_sessions (
                    client_id, ip_address, device_id, device_fingerprint,
                    is_emulator, user_agent, login_timestamp, is_new_device,
                    device_first_seen_at, created_at
                ) VALUES %s
            """, session_inserts)

        conn.commit()
        print(f"\nSuccessfully inserted {len(df)} records into PostgreSQL database!")
        print(f"   - Clients: {len(client_inserts)}")
        print(f"   - Orders: {len(order_inserts)}")
        print(f"   - Returns: {len(return_inserts)}")
        print(f"   - Sessions: {len(session_inserts)}")

        cur.close()

    except Exception as e:
        print(f"Error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    print("\n🔍 Sample of first 3 records (transposed for readability):")
    print(df.head(3).T)