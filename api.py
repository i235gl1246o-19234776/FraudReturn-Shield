# =============================================================================
# FRAUDRETURN SHIELD — FASTAPI SERVICE
# Объединённый сервис: Fraud модель + Chat + Feature Pipeline
# =============================================================================

import sys
import asyncio
import json
import os
import re
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import pandas as pd
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from rank_bm25 import BM25Okapi
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor
from onnx_feature_pipeline2 import FraudDetectionService

# =============================================================================
# 🔧 ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =============================================================================

_executor = ThreadPoolExecutor(max_workers=4)

_fraud_service: Optional[FraudDetectionService] = None

# Fraud модель
_fraud_session = None
_fraud_input_name = None

# Chat модель
_chat_session = None
_chat_tokenizer = None
_chat_input_names = None

# QA данные
_qa_data = None
_qa_embeddings = None
_bm25_index = None
_qa_question_tokens = None
_qa_answer_tokens = None


def _log(msg: str):
    print(msg, file=sys.stderr)


# =============================================================================
# 📊 PYDANTIC МОДЕЛИ ДЛЯ API
# =============================================================================

class FraudFeatures(BaseModel):
    """Признаки для fraud модели"""
    # Из clients
    account_age_days: int = 0
    total_purchases: int = 0
    total_returns: int = 0
    customer_return_rate: float = 0.0
    avg_order_amount: float = 0.0

    # Из orders
    order_amount: float = 0.0
    items_in_order: int = 1
    discount_percent: float = 0.0
    payment_method_risk: float = 0.3
    amount_deviation: float = 0.0
    orders_last_30d: int = 0

    # Из returns
    return_rate_30d: float = 0.0
    refund_velocity_30d: int = 0
    days_since_last_return: int = 999
    days_since_purchase: int = 0
    has_receipt: int = 1
    receipt_provided: int = 1
    tags_removed: int = 0
    missing_components: int = 0
    return_channel: str = "online"

    # Временные признаки
    order_hour: int = 12

    # Флаги
    high_value_flag: int = 0
    order_time_night: int = 0
    fast_return_flag: int = 0
    new_account_flag: int = 0
    first_order_discount_abuse: int = 0

    # Категории
    category: str = "Электроника"
    is_electronics: int = 1
    claimed_reason: str = "Брак"

    # IP/Device stats
    ip_velocity_24h: int = 0
    ip_velocity_7d: int = 0
    accounts_per_ip: int = 1
    accounts_per_phone: int = 1
    accounts_per_device: int = 1
    device_is_emulator: int = 0
    device_trust_score: float = 0.85
    ip_trust_score: float = 0.80

    # Дополнительные
    address_match: int = 1
    device_new: int = 0
    promo_code_used: int = 0
    weekend_purchase: int = 0
    refund_velocity_7d: int = 0
    support_ticket_count_30d: int = 0
    review_count_30d: int = 0
    negative_review_cluster: int = 0
    shipping_region_risk: float = 0.3
    delivery_address_type: str = "home"
    distance_from_registration_city: float = 0.0
    card_bin_country_mismatch: int = 0
    chargeback_history_90d: int = 0
    threat_language_detected: int = 0
    legal_claim_threat: int = 0


class FraudPredictionResponse(BaseModel):
    success: bool
    score: Optional[float] = None
    error: Optional[str] = None
    risk_level: Optional[str] = None
    recommendation: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    error: Optional[str] = None


class LoadModelRequest(BaseModel):
    model_path: str
    model_type: str = "fraud"  # "fraud" или "chat"
    use_v4: bool = True  # Использовать ли v4 модель через onnx_feature_pipeline

class FraudV4PredictionRequest(BaseModel):
    """Запрос для предсказания через v4 модель (требует return_id)"""
    return_id: int = Field(..., description="ID возврата в БД")

class FraudPayloadRequest(BaseModel):
    """Запрос для предсказания через v4 модель с передачей данных напрямую (без return_id)"""
    client_id: int = Field(..., description="ID клиента")
    order_id: int = Field(..., description="ID заказа")
    return_id: Optional[int] = Field(0, description="ID возврата (если есть)")
    # Из clients
    account_age_days: int = 0
    total_orders: int = 0
    total_returns: int = 0
    global_return_rate: float = 0.0
    avg_order_amount: float = 0.0
    # Из orders
    order_amount: float = 0.0
    items_count: int = 1
    discount_amount: float = 0.0
    payment_method: str = "card"
    order_timestamp: Optional[str] = None
    amount_deviation: float = 0.0
    orders_last_30d: int = 0
    product_category: str = "Electronics"
    is_electronics: bool = False
    shipping_region: str = "Moscow"
    region_risk_score: float = 0.3
    delivery_city: str = "Moscow"
    distance_from_registration_km: float = 0.0
    payment_card_bin: Optional[str] = None
    card_issuing_country: Optional[str] = None
    card_country_mismatch: bool = False
    delivery_address_type: str = "home"
    address_match_score: float = 1.0
    is_address_match: bool = True
    # Из returns
    returns_last_30d: int = 0
    return_rate_last_30d: float = 0.0
    days_since_last_return: int = 999
    days_since_purchase: int = 0
    return_channel: str = "online"
    has_receipt: bool = True
    tags_removed: bool = False
    missing_components: bool = False
    claimed_reason: str = "Defective"

class FraudV4PredictionResponse(BaseModel):
    success: bool
    return_id: Optional[int] = None
    client_id: Optional[int] = None
    order_id: Optional[int] = None
    probability_fraud: Optional[float] = None
    anomaly_score: Optional[float] = None
    is_anomaly: Optional[bool] = None
    combined_score: Optional[float] = None
    decision: Optional[str] = None
    error: Optional[str] = None

class LoadModelResponse(BaseModel):
    success: bool
    error: Optional[str] = None


# =============================================================================
# 🛡️ FRAUD МОДЕЛЬ
# =============================================================================

def load_fraud_model_v4(onnx_path: str, metadata_path: str,
                        anomaly_scaler_path: str, anomaly_model_path: str,
                        db_connection_string: Optional[str] = None) -> Dict[str, Any]:
    global _fraud_service
    try:
        if not os.path.exists(onnx_path):
            return {'success': False, 'error': f'ONNX file not found: {onnx_path}'}
        if not os.path.exists(metadata_path):
            return {'success': False, 'error': f'Metadata file not found: {metadata_path}'}
        if not os.path.exists(anomaly_scaler_path):
            return {'success': False, 'error': f'Anomaly scaler not found: {anomaly_scaler_path}'}
        if not os.path.exists(anomaly_model_path):
            return {'success': False, 'error': f'Anomaly model not found: {anomaly_model_path}'}

        # Для работы требуется БД connection string
        # Если не передан - используем заглушку (для тестов)
        if db_connection_string is None:
            db_connection_string = os.getenv('DATABASE_URL', 'postgresql://postgres:OmegaBloody13@localhost:5432/fraud_return_db')

        # Парсинг PostgreSQL connection string
        from urllib.parse import urlparse
        parsed = urlparse(db_connection_string)
        conn_params = {
            'host': parsed.hostname or 'localhost',
            'port': parsed.port or 5432,
            'database': parsed.path.lstrip('/') or 'postgres',
            'user': parsed.username or 'postgres',
            'password': parsed.password or '1234',
            'options': '-c client_encoding=UTF8'
        }

        _fraud_service = FraudDetectionService(
            conn_params=conn_params,
            onnx_path=onnx_path,
            metadata_path=metadata_path,
            anomaly_scaler_path=anomaly_scaler_path,
            anomaly_model_path=anomaly_model_path
        )

        _log(f"[INFO] Fraud v4 model loaded via FraudDetectionService: {onnx_path}")
        return {'success': True}
    except Exception as e:
        _log(f"[ERROR] Fraud v4 load: {str(e)}")
        return {'success': False, 'error': str(e)}


def load_fraud_model_legacy(model_path: str) -> Dict[str, Any]:
    """
    Legacy загрузка fraud модели (CatBoost CBM или простой ONNX без пайплайна)
    Используется для обратной совместимости.
    """
    global _fraud_session, _fraud_input_name
    try:
        if not os.path.exists(model_path):
            return {'success': False, 'error': f'File not found: {model_path}'}

        # Проверяем тип модели по расширению
        if model_path.endswith('.cbm'):
            # Загрузка CatBoost модели напрямую
            import catboost
            _fraud_session = catboost.CatBoostClassifier()
            _fraud_session.load_model(model_path)
            _fraud_input_name = 'cbm_native'
            _log(f"[INFO] Fraud CatBoost model loaded (CBM): {model_path}")
        else:
            # Загрузка ONNX модели
            _fraud_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            _fraud_input_name = _fraud_session.get_inputs()[0].name
            _log(f"[INFO] Fraud ONNX model loaded: {model_path}")

        return {'success': True}
    except Exception as e:
        _log(f"[ERROR] Fraud legacy load: {str(e)}")
        return {'success': False, 'error': str(e)}

def load_fraud_model(model_path: str, use_v4: bool = True) -> Dict[str, Any]:
    """
    Универсальная функция загрузки fraud модели.
    По умолчанию использует v4 через onnx_feature_pipeline.py.
    """
    if use_v4 and model_path.endswith('.onnx'):
        try:
            with open(model_path, 'rb') as f:
                header = f.read(20)
                if b'git-lfs' in header or not header.startswith(b'\x08'):
                    # Это Git LFS placeholder или невалидный файл, используем legacy
                    _log(f"[WARN] {model_path} is not a valid ONNX file (Git LFS placeholder?), using legacy mode")
                    return load_fraud_model_legacy(model_path.replace('.onnx', '.cbm'))
        except Exception:
            pass
        
        # v4 модель через FraudDetectionService
        base_dir = os.path.dirname(model_path) or 'models'
        metadata_path = os.path.join(base_dir, 'metadata_v4_27patterns.json')
        anomaly_scaler_path = os.path.join(base_dir, 'scaler_v4.pkl')
        anomaly_model_path = os.path.join(base_dir, 'anomaly_model_v4.pkl')

        return load_fraud_model_v4(
            onnx_path=model_path,
            metadata_path=metadata_path,
            anomaly_scaler_path=anomaly_scaler_path,
            anomaly_model_path=anomaly_model_path
        )
    else:
        # Legacy режим
        return load_fraud_model_legacy(model_path)



async def load_fraud_model_async(model_path: str, use_v4: bool = True) -> Dict[str, Any]:
    """Асинхронная загрузка fraud модели"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, load_fraud_model, model_path, use_v4)

def predict_fraud_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Предсказание через FraudDetectionService с передачей данных напрямую (без return_id)
    Использует predict_from_web_payload для работы с данными от сайта
    """
    global _fraud_service
    if _fraud_service is None:
        return {'success': False, 'error': 'Fraud v4 model not loaded'}

    try:
        result = _fraud_service.predict_from_web_payload(payload)
        return {'success': True, **result}
    except Exception as e:
        _log(f"[ERROR] Fraud v4 predict from payload: {str(e)}")
        return {'success': False, 'error': str(e)}

def predict_fraud_v4(return_id: int) -> Dict[str, Any]:
    """
    Предсказание через FraudDetectionService (v4 модель из onnx_feature_pipeline.py)
    Требует return_id для загрузки данных из БД
    """
    global _fraud_service
    if _fraud_service is None:
        return {'success': False, 'error': 'Fraud v4 model not loaded'}

    try:
        result = _fraud_service.predict_for_return(return_id)
        return {'success': True, **result}
    except Exception as e:
        _log(f"[ERROR] Fraud v4 predict: {str(e)}")
        return {'success': False, 'error': str(e)}

def predict_fraud(features: FraudFeatures) -> FraudPredictionResponse:
    global _fraud_session, _fraud_input_name, _fraud_service

    if _fraud_service is not None:
        try:
            # Преобразуем FraudFeatures в payload для predict_from_web_payload
            payload = {
                "client_id": 0,  # Не используется в legacy режиме
                "order_id": 0,   # Не используется в legacy режиме
                "account_age_days": features.account_age_days,
                "total_orders": features.total_purchases,
                "total_returns": features.total_returns,
                "global_return_rate": features.customer_return_rate,
                "avg_order_amount": features.avg_order_amount,
                "order_amount": features.order_amount,
                "items_count": features.items_in_order,
                "discount_amount": features.order_amount * features.discount_percent / 100.0,
                "payment_method": "card",
                "orders_last_30d": features.orders_last_30d,
                "product_category": features.category,
                "is_electronics": bool(features.is_electronics),
                "shipping_region": "Moscow",
                "region_risk_score": features.shipping_region_risk,
                "delivery_city": "Moscow",
                "distance_from_registration_km": features.distance_from_registration_city,
                "card_country_mismatch": bool(features.card_bin_country_mismatch),
                "delivery_address_type": "home",
                "address_match_score": float(features.address_match),
                "is_address_match": bool(features.address_match),
                "returns_last_30d": features.refund_velocity_30d,
                "return_rate_last_30d": features.return_rate_30d,
                "days_since_last_return": features.days_since_last_return,
                "days_since_purchase": features.days_since_purchase,
                "return_channel": features.return_channel,
                "has_receipt": bool(features.has_receipt),
                "tags_removed": bool(features.tags_removed),
                "missing_components": bool(features.missing_components),
                "claimed_reason": features.claimed_reason,
                # Дополнительные поля
                "ip_velocity_24h": features.ip_velocity_24h,
                "ip_velocity_7d": features.ip_velocity_7d,
                "accounts_per_ip": features.accounts_per_ip,
                "accounts_per_phone": features.accounts_per_phone,
                "accounts_per_device": features.accounts_per_device,
                "device_is_emulator": features.device_is_emulator,
                "device_trust_score": features.device_trust_score,
                "ip_trust_score": features.ip_trust_score,
                "device_new": features.device_new,
                "promo_code_used": features.promo_code_used,
                "weekend_purchase": features.weekend_purchase,
                "refund_velocity_7d": features.refund_velocity_7d,
                "support_ticket_count_30d": features.support_ticket_count_30d,
                "review_count_30d": features.review_count_30d,
                "negative_review_cluster": features.negative_review_cluster,
                "chargeback_history_90d": features.chargeback_history_90d,
                "threat_language_detected": features.threat_language_detected,
                "legal_claim_threat": features.legal_claim_threat,
            }
            result = _fraud_service.predict_from_web_payload(payload)
            if result.get('success'):
                final_score = result.get('combined_score', result.get('probability_fraud', 0.0))
                risk_level = "HIGH" if final_score >= 0.7 else ("MEDIUM" if final_score >= 0.4 else "LOW")
                recommendation = "Отклонить возврат. Высокий риск мошенничества." if final_score >= 0.7 else \
                                ("Требуется дополнительная проверка." if final_score >= 0.4 else "Одобрить возврат. Низкий риск.")
                return FraudPredictionResponse(
                    success=True,
                    score=final_score,
                    risk_level=risk_level,
                    recommendation=recommendation
                )
            else:
                return FraudPredictionResponse(success=False, score=None, error=result.get('error'))
        except Exception as e:
            _log(f"[ERROR] Fraud v4 predict from features: {str(e)}")
            return FraudPredictionResponse(success=False, score=None, error=str(e))

    if _fraud_session is None and _fraud_service is None:
        return FraudPredictionResponse(success=False, score=None, error='Model not loaded')

    if _fraud_session is not None:
        try:
            feature_list = [
                float(features.account_age_days),
                float(features.total_purchases),
                float(features.total_returns),
                float(features.customer_return_rate),
                float(features.avg_order_amount),
                float(features.order_amount),
                float(features.items_in_order),
                float(features.discount_percent),
                float(features.payment_method_risk),
                float(features.amount_deviation),
                float(features.orders_last_30d),
                float(features.return_rate_30d),
                float(features.refund_velocity_30d),
                float(features.days_since_last_return),
                float(features.days_since_purchase),
                float(features.has_receipt),
                float(features.receipt_provided),
                float(features.tags_removed),
                float(features.missing_components),
                float(features.order_hour),
                float(features.high_value_flag),
                float(features.order_time_night),
                float(features.fast_return_flag),
                float(features.new_account_flag),
                float(features.first_order_discount_abuse),
                float(features.is_electronics),
                float(features.ip_velocity_24h),
                float(features.ip_velocity_7d),
                float(features.accounts_per_ip),
                float(features.accounts_per_phone),
                float(features.accounts_per_device),
                float(features.device_is_emulator),
                float(features.device_trust_score),
                float(features.ip_trust_score),
                float(features.address_match),
                float(features.device_new),
                float(features.promo_code_used),
                float(features.weekend_purchase),
                float(features.refund_velocity_7d),
                float(features.support_ticket_count_30d),
                float(features.review_count_30d),
                float(features.negative_review_cluster),
                float(features.shipping_region_risk),
                float(features.distance_from_registration_city),
                float(features.card_bin_country_mismatch),
                float(features.chargeback_history_90d),
                float(features.threat_language_detected),
                float(features.legal_claim_threat),
            ]

            input_data = np.array(feature_list, dtype=np.float32).reshape(1, -1)
            outputs = _fraud_session.run(None, {_fraud_input_name: input_data})

            score = None
            if len(outputs) >= 2:
                prob_map = outputs[1][0]
                if isinstance(prob_map, dict):
                    score = float(prob_map.get(1, prob_map.get('1', list(prob_map.values())[1])))
                else:
                    score = float(prob_map[1]) if len(prob_map) > 1 else float(prob_map[0])
            elif len(outputs) >= 1:
                score = float(outputs[0][0][1]) if outputs[0].shape[-1] >= 2 else float(outputs[0][0][0])

            final_score = max(0.0, min(1.0, score)) if score is not None else 0.0

            # Определяем уровень риска и рекомендацию
            if final_score >= 0.7:
                risk_level = "HIGH"
                recommendation = "Отклонить возврат. Высокий риск мошенничества."
            elif final_score >= 0.4:
                risk_level = "MEDIUM"
                recommendation = "Требуется дополнительная проверка."
            else:
                risk_level = "LOW"
                recommendation = "Одобрить возврат. Низкий риск."

            return FraudPredictionResponse(
                success=True,
                score=final_score,
                risk_level=risk_level,
                recommendation=recommendation
            )
        except Exception as e:
            _log(f"[ERROR] Fraud legacy predict: {str(e)}")
            return FraudPredictionResponse(success=False, score=None, error=str(e))

    return FraudPredictionResponse(
        success=False,
        score=None,
        error='No model available'
    )

async def predict_fraud_async(features: FraudFeatures) -> FraudPredictionResponse:
    """Асинхронное предсказание fraud модели"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, predict_fraud, features)


# =============================================================================
# 💬 CHAT: ГИБРИДНЫЙ ПОИСК ПО QA
# =============================================================================

def _preprocess_text(text: str) -> List[str]:
    """Простая токенизация: оставляем буквы и цифры, нижний регистр"""
    return re.findall(r'[\wа-яё]+', text.lower())


def _init_chat_model(model_path: str, tokenizer_dir: Optional[str] = None) -> bool:
    global _chat_session, _chat_tokenizer, _chat_input_names
    try:
        tok_dir = tokenizer_dir or os.path.dirname(model_path)
        tok_path = os.path.join(tok_dir, 'tokenizer.json')

        if not os.path.exists(tok_path):
            if os.path.exists('models/tokenizer.json'):
                tok_path = 'models/tokenizer.json'
            else:
                raise FileNotFoundError(f"Tokenizer not found at: {tok_path} or current dir")

        _chat_tokenizer = Tokenizer.from_file(tok_path)
        pad_id = _chat_tokenizer.token_to_id('[PAD]') or 0
        _chat_tokenizer.enable_padding(pad_id=pad_id)
        _chat_tokenizer.enable_truncation(max_length=128)

        _chat_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        _chat_input_names = [i.name for i in _chat_session.get_inputs()]
        _log(f"[CHAT] Model ready. Inputs: {_chat_input_names}")
        return True
    except Exception as e:
        _log(f"[ERROR] Chat init: {e}")
        return False


def _get_embedding(text: str) -> np.ndarray:
    global _chat_tokenizer, _chat_session, _chat_input_names

    if _chat_tokenizer is None or _chat_session is None:
        raise RuntimeError("Chat models not initialized")

    encoded = _chat_tokenizer.encode(text)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)

    model_inputs = {
        _chat_input_names[0]: input_ids,
        'attention_mask': attention_mask,
    }

    if 'token_type_ids' in _chat_input_names:
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)
        model_inputs['token_type_ids'] = token_type_ids

    outputs = _chat_session.run(None, model_inputs)
    last_hidden = outputs[0][0]

    mask = np.array(encoded.attention_mask)[:, None].astype(np.float32)
    sum_embeddings = np.sum(last_hidden * mask, axis=0)
    count_embeddings = np.sum(mask) + 1e-9
    pooled = sum_embeddings / count_embeddings

    norm = np.linalg.norm(pooled)
    if norm > 0:
        pooled = pooled / norm

    return pooled


def _load_qa_data(qa_path: str) -> bool:
    global _qa_data, _qa_embeddings, _bm25_index, _qa_question_tokens, _qa_answer_tokens

    try:
        with open(qa_path, 'r', encoding='utf-8-sig') as f:
            _qa_data = json.load(f)

        if not _qa_data:
            raise ValueError("QA file is empty")

        questions = []
        answers = []

        for item in _qa_data:
            questions.append(item.get('question', ''))
            answers.append(item.get('answer', ''))

        _log("[QA] Computing embeddings...")
        if _chat_session is not None:
            try:
                _qa_embeddings = np.array([_get_embedding(a) for a in answers])
            except Exception as e:
                _log(f"[WARN] Embedding computation failed: {e}. Falling back to BM25 only.")
                _qa_embeddings = None
        else:
            _qa_embeddings = None

        def tokenize(t: str) -> List[str]:
            return _preprocess_text(t)

        _qa_question_tokens = [tokenize(q) for q in questions]
        _qa_answer_tokens = [tokenize(a) for a in answers]

        combined_corpus = []
        for i in range(len(_qa_data)):
            combined_tokens = _qa_question_tokens[i] + _qa_answer_tokens[i]
            combined_corpus.append(combined_tokens)

        _bm25_index = BM25Okapi(combined_corpus)

        _log(f"[QA] Loaded {len(_qa_data)} Q&A pairs (BM25 index built on Q+A)")
        return True
    except Exception as e:
        _log(f"[ERROR] QA load: {e}")
        return False


def _chat_fallback(message: str) -> str:
    msg_lower = message.lower().strip()
    if any(w in msg_lower for w in ['привет', 'здравствуй']):
        return "Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?"
    elif any(w in msg_lower for w in ['риск', 'мошенничество', '100%']):
        return "⚠️ Высокий риск: новый аккаунт, нет чека, быстрый возврат, сумма >30к ₽."
    elif any(w in msg_lower for w in ['штраф', 'накладная', 'приемка', 'возврат']):
        return "Я могу найти ответ в базе знаний. Попробуйте спросить конкретнее, например: 'Как формируется накладная?'"
    return "Я специализируюсь на оценке риска возвратов и документации. Спросите о факторах риска или процедурах."


def _hybrid_search(query: str, top_k: int = 5) -> str:
    """Гибридный поиск: Семантика + BM25 + Эвристика намерений"""
    global _qa_data, _qa_embeddings, _bm25_index, _qa_question_tokens

    if not _qa_data:
        return _chat_fallback(query)

    query_tokens = _preprocess_text(query)
    if not query_tokens:
        return "Пожалуйста, задайте вопрос словами."

    semantic_scores = np.zeros(len(_qa_data))
    use_semantic = False

    if _chat_session is not None and _qa_embeddings is not None:
        try:
            query_emb = _get_embedding(query)
            semantic_scores = np.dot(_qa_embeddings, query_emb)
            use_semantic = True
        except Exception as e:
            _log(f"[SEARCH] Semantic search error: {e}")

    bm25_scores = _bm25_index.get_scores(query_tokens)

    final_scores = np.zeros(len(_qa_data))

    if use_semantic:
        max_bm25 = np.max(bm25_scores) if np.max(bm25_scores) > 0 else 1.0
        norm_bm25 = bm25_scores / (max_bm25 + 1e-6)
        final_scores = 0.6 * semantic_scores + 0.4 * norm_bm25
    else:
        final_scores = bm25_scores

    top_indices = final_scores.argsort()[-top_k:][::-1]

    _log(f"[SEARCH] 🔍 Query: '{query}'")
    for i, idx in enumerate(top_indices):
        q_text = _qa_data[idx].get('question', '')[:60]
        s_score = semantic_scores[idx] if use_semantic else 0.0
        b_score = bm25_scores[idx]
        _log(f"[SEARCH] Top-{i+1}: sem={s_score:.3f}, bm25={b_score:.3f} | Q: {q_text}...")

    SEMANTIC_THRESHOLD = 0.65
    BM25_THRESHOLD = 5.0

    best_candidate = None
    best_score = -1.0

    for idx in top_indices:
        score = final_scores[idx]
        sem_val = semantic_scores[idx] if use_semantic else 0.0
        bm25_val = bm25_scores[idx]

        candidate_quality = 0.0

        if sem_val >= SEMANTIC_THRESHOLD:
            candidate_quality = sem_val + 0.1
        elif bm25_val >= BM25_THRESHOLD:
            candidate_quality = 0.5 + (bm25_val / 20.0)
        elif sem_val >= 0.4 and bm25_val >= 2.0:
            candidate_quality = sem_val * 0.7 + (bm25_val / 20.0) * 0.3

        if candidate_quality > best_score:
            best_score = candidate_quality
            best_candidate = _qa_data[idx].get('answer', '')

    if best_candidate and best_score > 0.4:
        return best_candidate

    if len(top_indices) > 0 and bm25_scores[top_indices[0]] >= 3.0:
        idx = top_indices[0]
        _log(f"[SEARCH] ⚠️ Using weak BM25 match")
        return _qa_data[idx].get('answer', '')

    _log(f"[SEARCH] ❌ No good match found (best_score={best_score:.3f})")
    return "🤷‍♂️ Не нашёл точного ответа. Попробуйте переформулировать вопрос, используя ключевые слова из документации (например: 'штраф', 'накладная', 'акт')."


def chat_query(message: str, qa_path: Optional[str] = None, model_path: Optional[str] = None) -> str:
    """Основной метод для чата"""
    global _chat_session, _qa_data

    script_dir = os.path.dirname(os.path.abspath(__file__))

    if model_path and _chat_session is None:
        _init_chat_model(model_path)

    if _qa_data is None:
        if qa_path is None:
            qa_path = os.path.join(script_dir, 'qa.json')
            if not os.path.exists(qa_path):
                qa_path = 'qa.json'

        if os.path.exists(qa_path):
            _load_qa_data(qa_path)

    if _qa_data is None:
        return _chat_fallback(message)

    try:
        response = _hybrid_search(message)
        return response
    except Exception as e:
        _log(f"[ERROR] Search failed: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return _chat_fallback(message)



async def chat_query_async(message: str, qa_path: Optional[str] = None, model_path: Optional[str] = None) -> str:
    """Асинхронный метод для чата"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, chat_query, message, qa_path, model_path)

# =============================================================================
# 🚀 FASTAPI ПРИЛОЖЕНИЕ
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/Shutdown события"""
    # Startup
    _log("[INFO] Starting FraudReturn Shield API...")

    # Автоматическая загрузка Fraud v4 модели при старте
    onnx_path = "models/fraud_model_v4_27patterns.onnx"
    metadata_path = "models/metadata_v4_27patterns.json"
    anomaly_scaler_path = "models/scaler_v4.pkl"
    anomaly_model_path = "models/anomaly_model_v4.pkl"

    if os.path.exists(onnx_path) and os.path.exists(metadata_path) and \
       os.path.exists(anomaly_scaler_path) and os.path.exists(anomaly_model_path):
        _log(f"[INFO] Auto-loading Fraud v4 model: {onnx_path}")
        result = load_fraud_model_v4(
            onnx_path=onnx_path,
            metadata_path=metadata_path,
            anomaly_scaler_path=anomaly_scaler_path,
            anomaly_model_path=anomaly_model_path,
            db_connection_string=None  # Будет использована DATABASE_URL или дефолт
        )
        if result['success']:
            _log("[INFO] ✅ Fraud v4 model loaded successfully at startup")
        else:
            _log(f"[WARNING] ⚠️  Fraud v4 model load failed at startup: {result.get('error')}")
    else:
        _log("[WARNING] ⚠️  Model files not found, skipping auto-load")

    yield
    # Shutdown
    _log("[INFO] Shutting down FraudReturn Shield API...")


app = FastAPI(
    title="FraudReturn Shield API",
    description="API для оценки риска мошеннических возвратов и чат-помощник",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/predict-fraud-payload", response_model=FraudV4PredictionResponse)
async def api_predict_fraud_payload(request: FraudPayloadRequest):
    """
    Предсказание риска мошенничества через v4 модель с передачей данных напрямую.
    Не требует return_id в БД - данные передаются в запросе.
    Использует predict_from_web_payload из onnx_feature_pipeline2.py
    """
    global _fraud_service
    if _fraud_service is None:
        return FraudV4PredictionResponse(
            success=False,
            error='Fraud v4 model not loaded. Call /api/load-models first with fraud_model_v4_27patterns.onnx'
        )

    try:
        # Преобразуем Pydantic модель в dict
        payload = request.model_dump()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, predict_fraud_from_payload, payload)

        if result.get('success'):
            return FraudV4PredictionResponse(
                success=True,
                return_id=result.get('return_id'),
                client_id=result.get('client_id'),
                order_id=result.get('order_id'),
                probability_fraud=result.get('probability_fraud'),
                anomaly_score=result.get('anomaly_score'),
                is_anomaly=result.get('is_anomaly'),
                combined_score=result.get('combined_score'),
                decision=result.get('decision')
            )
        else:
            return FraudV4PredictionResponse(success=False, error=result.get('error'))
    except Exception as e:
        _log(f"[ERROR] API predict fraud payload: {str(e)}")
        return FraudV4PredictionResponse(success=False, error=str(e))

@app.get("/")
async def root():
    return {
        "service": "FraudReturn Shield API",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "load_model": "/api/load-models",
            "predict_fraud": "/api/predict-fraud",
            "chat": "/api/chat"
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "fraud_model_loaded": _fraud_session is not None,
        "chat_model_loaded": _chat_session is not None,
        "qa_data_loaded": _qa_data is not None
    }


@app.post("/api/load-models", response_model=LoadModelResponse)
async def api_load_model(request: LoadModelRequest):
    """Загрузка модели (fraud или chat)"""
    if request.model_type == "fraud":
        result = load_fraud_model(request.model_path, use_v4=request.use_v4)
        return LoadModelResponse(success=result['success'], error=result.get('error'))
    elif request.model_type == "chat":
        success = _init_chat_model(request.model_path)
        return LoadModelResponse(success=success, error=None if success else "Failed to initialize chat models")
    else:
        return LoadModelResponse(success=False, error=f"Unknown models type: {request.model_type}")


@app.post("/api/predict-fraud", response_model=FraudPredictionResponse)
async def api_predict_fraud(features: FraudFeatures):
    """Предсказание риска мошенничества (асинхронно)"""
    return await predict_fraud_async(features)

@app.post("/api/predict-fraud-v4", response_model=FraudV4PredictionResponse)
async def api_predict_fraud_v4(request: FraudV4PredictionRequest):
    """
    Предсказание риска мошенничества через v4 модель (onnx_feature_pipeline.py).
    Требует return_id для загрузки данных из БД.
    """
    global _fraud_service
    if _fraud_service is None:
        return FraudV4PredictionResponse(
            success=False,
            error='Fraud v4 model not loaded. Call /api/load-models first with fraud_model_v4_27patterns.onnx'
        )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, predict_fraud_v4, request.return_id)

        if result.get('success'):
            return FraudV4PredictionResponse(
                success=True,
                return_id=result.get('return_id'),
                client_id=result.get('client_id'),
                order_id=result.get('order_id'),
                probability_fraud=result.get('probability_fraud'),
                anomaly_score=result.get('anomaly_score'),
                is_anomaly=result.get('is_anomaly'),
                combined_score=result.get('combined_score'),
                decision=result.get('decision')
            )
        else:
            return FraudV4PredictionResponse(success=False, error=result.get('error'))
    except Exception as e:
        _log(f"[ERROR] API predict fraud v4: {str(e)}")
        return FraudV4PredictionResponse(success=False, error=str(e))


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(request: ChatRequest):
    """Чат с AI-помощником"""
    if not request.message.strip():
        return ChatResponse(response="Пожалуйста, задайте вопрос.", error=None)

    response = await chat_query_async(request.message)
    return ChatResponse(response=response, error=None)


# =============================================================================
# ЗАПУСК
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)