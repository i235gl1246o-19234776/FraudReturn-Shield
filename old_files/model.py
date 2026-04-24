import sys
import json
import os
import re
import onnxruntime as ort
import numpy as np
from tokenizers import Tokenizer
from rank_bm25 import BM25Okapi

# =============================================================================
# 🔧 ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =============================================================================
_fraud_session = None
_fraud_input_name = None

_chat_session = None
_chat_tokenizer = None
_chat_input_names = None

_qa_data = None
_qa_embeddings = None
_bm25_index = None
_qa_question_tokens = None  # Токенизированные вопросы для BM25
_qa_answer_tokens = None    # Токенизированные ответы для BM25

def _log(msg):
    print(msg, file=sys.stderr)

# =============================================================================
# 🛡️ FRAUD МОДЕЛЬ
# =============================================================================
def load_fraud_model(model_path):
    global _fraud_session, _fraud_input_name
    try:
        if not os.path.exists(model_path):
            return {'success': False, 'error': f'File not found: {model_path}'}
        _fraud_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        _fraud_input_name = _fraud_session.get_inputs()[0].name
        _log(f"[INFO] Fraud models loaded: {model_path}")
        return {'success': True}
    except Exception as e:
        _log(f"[ERROR] Fraud load: {str(e)}")
        return {'success': False, 'error': str(e)}

def predict_fraud(features_list):
    global _fraud_session, _fraud_input_name
    if _fraud_session is None:
        return {'success': False, 'score': None, 'error': 'Model not loaded'}
    try:
        input_data = np.array(features_list, dtype=np.float32).reshape(1, -1)
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
        return {'success': True, 'score': max(0.0, min(1.0, score))}
    except Exception as e:
        _log(f"[ERROR] Fraud predict: {str(e)}")
        return {'success': False, 'score': None, 'error': str(e)}

# =============================================================================
# 💬 CHAT: ГИБРИДНЫЙ ПОИСК ПО QA
# =============================================================================

def _preprocess_text(text):
    """Простая токенизация: оставляем буквы и цифры, нижний регистр"""
    return re.findall(r'[\wа-яё]+', text.lower())

def _init_chat_model(model_path, tokenizer_dir=None):
    global _chat_session, _chat_tokenizer, _chat_input_names
    try:
        tok_dir = tokenizer_dir or os.path.dirname(model_path)
        tok_path = os.path.join(tok_dir, 'tokenizer.json')
        
        if not os.path.exists(tok_path):
            # Пробуем найти tokenizer.json рядом с запускаемым скриптом, если не нашли рядом с моделью
            if os.path.exists('../models/tokenizer.json'):
                tok_path = '../models/tokenizer.json'
            else:
                raise FileNotFoundError(f"Tokenizer not found at: {tok_path} or current dir")
        
        _chat_tokenizer = Tokenizer.from_file(tok_path)
        _chat_tokenizer.enable_padding(pad_id=_chat_tokenizer.token_to_id('[PAD]') or 0)
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
    
    # Убеждаемся, что token_type_ids есть, если модель этого требует
    model_inputs = {
        _chat_input_names[0]: input_ids,
        'attention_mask': attention_mask,
    }
    
    # Добавляем token_type_ids только если модель их ожидает явно и они есть во входных данных
    if 'token_type_ids' in _chat_input_names:
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)
        model_inputs['token_type_ids'] = token_type_ids
    
    outputs = _chat_session.run(None, model_inputs)
    last_hidden = outputs[0][0]
    
    # Mean pooling с учетом маски
    mask = np.array(encoded.attention_mask)[:, None].astype(np.float32)
    sum_embeddings = np.sum(last_hidden * mask, axis=0)
    count_embeddings = np.sum(mask) + 1e-9
    pooled = sum_embeddings / count_embeddings
    
    # L2 нормализация
    norm = np.linalg.norm(pooled)
    if norm > 0:
        pooled = pooled / norm
    
    return pooled

def _load_qa_data(qa_path: str):
    global _qa_data, _qa_embeddings, _bm25_index, _qa_question_tokens, _qa_answer_tokens
    
    try:
        with open(qa_path, 'r', encoding='utf-8') as f:
            _qa_data = json.load(f)
        
        if not _qa_data:
            raise ValueError("QA file is empty")

        questions = []
        answers = []
        
        for item in _qa_data:
            questions.append(item.get('question', ''))
            answers.append(item.get('answer', ''))
        
        # 1. Вычисляем эмбеддинги для ответов (семантический поиск)
        _log("[QA] Computing embeddings...")
        if _chat_session is not None:
            try:
                _qa_embeddings = np.array([_get_embedding(a) for a in answers])
            except Exception as e:
                _log(f"[WARN] Embedding computation failed: {e}. Falling back to BM25 only.")
                _qa_embeddings = None
        else:
            _qa_embeddings = None
        
        # 2. Подготовка токенов для BM25 (и вопросы, и ответы)
        def tokenize(t):
            return _preprocess_text(t)
        
        _qa_question_tokens = [tokenize(q) for q in questions]
        _qa_answer_tokens = [tokenize(a) for a in answers]
        
        # Создаем индекс BM25 на основе КОНКАТЕНАЦИИ вопроса и ответа для лучшего поиска
        combined_corpus = []
        for i in range(len(_qa_data)):
            # Объединяем токены вопроса и ответа для более полного контекста
            combined_tokens = _qa_question_tokens[i] + _qa_answer_tokens[i]
            combined_corpus.append(combined_tokens)
            
        _bm25_index = BM25Okapi(combined_corpus)
        
        _log(f"[QA] Loaded {len(_qa_data)} Q&A pairs (BM25 index built on Q+A)")
        return True
    except Exception as e:
        _log(f"[ERROR] QA load: {e}")
        return False

def _hybrid_search(query: str, top_k: int = 5) -> str:
    """Гибридный поиск: Семантика + BM25 + Эвристика намерений"""
    global _qa_data, _qa_embeddings, _bm25_index, _qa_question_tokens

    if not _qa_data:
        return _chat_fallback(query)
    
    query_tokens = _preprocess_text(query)
    if not query_tokens:
        return "Пожалуйста, задайте вопрос словами."

    # --- 1. Семантический поиск (Cosine Similarity) ---
    semantic_scores = np.zeros(len(_qa_data))
    use_semantic = False
    
    if _chat_session is not None and _qa_embeddings is not None:
        try:
            query_emb = _get_embedding(query)
            # Матричное умножение для косинусного сходства (векторы уже нормализованы)
            semantic_scores = np.dot(_qa_embeddings, query_emb)
            use_semantic = True
        except Exception as e:
            _log(f"[SEARCH] Semantic search error: {e}")

    # --- 2. BM25 Поиск (по объединенному корпусу Вопрос+Ответ) ---
    bm25_scores = _bm25_index.get_scores(query_tokens)

    # --- 3. Комбинирование и ранжирование ---
    # Нормализуем оценки, чтобы привести их к общему знаменателю (опционально, но полезно)
    # Здесь используем простую взвешенную сумму, так как BM25 и Cosine имеют разные масштабы
    
    final_scores = np.zeros(len(_qa_data))
    
    if use_semantic:
        # Вес семантики 0.6, вес ключевых слов 0.4
        # Нормализуем BM25 к диапазону [0, 1] грубо, если максимум большой
        max_bm25 = np.max(bm25_scores) if np.max(bm25_scores) > 0 else 1.0
        norm_bm25 = bm25_scores / (max_bm25 + 1e-6)
        
        final_scores = 0.6 * semantic_scores + 0.4 * norm_bm25
    else:
        final_scores = bm25_scores

    # Получаем индексы топ-K результатов
    top_indices = final_scores.argsort()[-top_k:][::-1]

    # Логирование для отладки
    _log(f"[SEARCH] 🔍 Query: '{query}'")
    for i, idx in enumerate(top_indices):
        q_text = _qa_data[idx].get('question', '')[:60]
        s_score = semantic_scores[idx] if use_semantic else 0.0
        b_score = bm25_scores[idx]
        _log(f"[SEARCH] Top-{i+1}: sem={s_score:.3f}, bm25={b_score:.3f} | Q: {q_text}...")

    # --- 4. Пост-обработка и выбор лучшего ответа ---
    
    # Пороговые значения
    SEMANTIC_THRESHOLD = 0.65  # Высокое доверие к нейросети
    BM25_THRESHOLD = 5.0       # Хорошее совпадение по ключевым словам
    
    best_candidate = None
    best_score = -1.0

    for idx in top_indices:
        score = final_scores[idx]
        sem_val = semantic_scores[idx] if use_semantic else 0.0
        bm25_val = bm25_scores[idx]
        
        qa_question = _qa_data[idx].get('question', '').lower()
        qa_answer = _qa_data[idx].get('answer', '')
        
        # Эвристика: если вопрос пользователя очень короткий или специфичный, 
        # доверяем больше BM25, если длинный и описательный — семантике.
        
        candidate_quality = 0.0
        
        # Сценарий А: Высокая семантическая близость
        if sem_val >= SEMANTIC_THRESHOLD:
            candidate_quality = sem_val + 0.1 # Бонус за уверенность нейросети
            
        # Сценарий Б: Хорошее совпадение по ключевым словам (BM25)
        elif bm25_val >= BM25_THRESHOLD:
            candidate_quality = 0.5 + (bm25_val / 20.0) # Нормализация BM25
            
        # Сценарий В: Смешанный сигнал (средняя семантика + наличие ключевых слов)
        elif sem_val >= 0.4 and bm25_val >= 2.0:
            candidate_quality = sem_val * 0.7 + (bm25_val / 20.0) * 0.3

        if candidate_quality > best_score:
            best_score = candidate_quality
            best_candidate = qa_answer

    # Финальное решение
    if best_candidate and best_score > 0.4:
        return best_candidate
    
    # Fallback: Если лучший результат слабый, но хоть что-то найдено по BM25
    if len(top_indices) > 0 and bm25_scores[top_indices[0]] >= 3.0:
        idx = top_indices[0]
        _log(f"[SEARCH] ⚠️ Using weak BM25 match")
        return _qa_data[idx].get('answer', '')

    _log(f"[SEARCH] ❌ No good match found (best_score={best_score:.3f})")
    return "🤷‍♂️ Не нашёл точного ответа. Попробуйте переформулировать вопрос, используя ключевые слова из документации (например: 'штраф', 'накладная', 'акт')."


def _chat_fallback(message: str) -> str:
    msg_lower = message.lower().strip()
    if any(w in msg_lower for w in ['привет', 'здравствуй']):
        return "Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?"
    elif any(w in msg_lower for w in ['риск', 'мошенничество', '100%']):
        return "⚠️ Высокий риск: новый аккаунт, нет чека, быстрый возврат, сумма >30к ₽."
    elif any(w in msg_lower for w in ['штраф', 'накладная', 'приемка', 'возврат']):
        return "Я могу найти ответ в базе знаний. Попробуйте спросить конкретнее, например: 'Как формируется накладная?'"
    return "Я специализируюсь на оценке риска возвратов и документации. Спросите о факторах риска или процедурах."

# =============================================================================
# 🚀 MAIN
# =============================================================================
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No mode specified'}))
        sys.exit(1)
    
    mode = sys.argv[1]
    
    try:
        if mode == '--load':
            path = sys.argv[2] if len(sys.argv) > 2 else 'fraud_model_v3_27patterns.onnx'
            print(json.dumps(load_fraud_model(path)))
        
        elif mode == '--predict':
            path = sys.argv[2]
            features_str = sys.argv[3]
            load_res = load_fraud_model(path)
            if not load_res['success']:
                print(json.dumps(load_res))
                sys.exit(1)
            try:
                features = [float(x) for x in features_str.split(',')]
                print(json.dumps(predict_fraud(features)))
            except ValueError:
                print(json.dumps({'success': False, 'error': 'Invalid features'}))
        
        elif mode == '--chat':
            # Поддержка обоих форматов вызова
            if len(sys.argv) < 4:
                print(json.dumps({'response': 'Ошибка: не указан вопрос'}))
                sys.exit(0)
            
            arg2 = sys.argv[2]
            arg3 = sys.argv[3]
            
            if arg2.endswith('.json'):
                # Формат: --chat qa.json "вопрос" Bert.onnx
                qa_path = arg2
                message = arg3
                model_path = sys.argv[4] if len(sys.argv) > 4 else 'Bert.onnx'
            else:
                # Формат: --chat Bert.onnx "вопрос" (qa.json ищем рядом)
                model_path = arg2
                message = arg3
                # Ищем qa.json в той же папке, где лежит скрипт, или в текущей рабочей
                script_dir = os.path.dirname(os.path.abspath(__file__))
                qa_path = os.path.join(script_dir, 'qa.json')
                if not os.path.exists(qa_path):
                    qa_path = '../other/qa.json'
            
            if not message.strip():
                print(json.dumps({'response': 'Пожалуйста, задайте вопрос.'}))
                sys.exit(0)
            
            _log(f"[CHAT] Query: {message}")
            _log(f"[CHAT] QA path: {qa_path}")
            _log(f"[CHAT] Model path: {model_path}")
            
            # Инициализация модели (если еще не загружена)
            if _chat_session is None:
                if not _init_chat_model(model_path):
                    # Если модель не загрузилась (нет файла, нет токенизатора), работаем в режиме BM25-only
                    _log("[WARN] Neural models unavailable. Running in BM25-only mode.")
            
            # Загрузка QA базы (если еще не загружена)
            if _qa_data is None:
                if not os.path.exists(qa_path):
                    _log(f"[WARN] qa.json not found: {qa_path}")
                    print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
                    sys.exit(0)
                if not _load_qa_data(qa_path):
                    _log("[WARN] Failed to load QA data")
                    print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
                    sys.exit(0)
            
            # Поиск ответа
            try:
                response = _hybrid_search(message)
                print(json.dumps({'response': response}, ensure_ascii=False))
            except Exception as e:
                _log(f"[ERROR] Search failed: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)
                print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
            
            sys.exit(0)
        
        else:
            print(json.dumps({'error': f'Unknown mode: {mode}'}))
            sys.exit(1)
    
    except Exception as e:
        _log(f"[FATAL] Script crashed: {str(e)}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': 'Internal script error'}))
        sys.exit(1)