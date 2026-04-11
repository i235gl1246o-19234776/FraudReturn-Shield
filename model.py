# model.py — AI Chat + Fraud Detection (ФИНАЛЬНАЯ ВЕРСИЯ)
import sys
import json
import os
import re
import onnxruntime as ort
import numpy as np
import traceback
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
        _log(f"[INFO] Fraud model loaded: {model_path}")
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
def _init_chat_model(model_path, tokenizer_dir=None):
    global _chat_session, _chat_tokenizer, _chat_input_names
    try:
        tok_dir = tokenizer_dir or os.path.dirname(model_path)
        tok_path = os.path.join(tok_dir, 'tokenizer.json')
        if not os.path.exists(tok_path):
            raise FileNotFoundError(f"Tokenizer not found: {tok_path}")
        
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
    
    encoded = _chat_tokenizer.encode(text)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids, dtype=np.int64)  # 🔥 КРИТИЧНО!
    
    model_inputs = {
        _chat_input_names[0]: input_ids,
        'attention_mask': attention_mask,
        'token_type_ids': token_type_ids
    }
    
    outputs = _chat_session.run(None, model_inputs)
    last_hidden = outputs[0][0]
    
    mask = np.array(encoded.attention_mask)[:, None].astype(np.float32)
    pooled = np.sum(last_hidden * mask, axis=0) / (np.sum(mask) + 1e-9)
    
    return pooled / (np.linalg.norm(pooled) + 1e-9)

def _load_qa_data(qa_path: str):
    global _qa_data, _qa_embeddings, _bm25_index
    
    try:
        with open(qa_path, 'r', encoding='utf-8') as f:
            _qa_data = json.load(f)
        
        answer_texts = [item.get('answer', '') for item in _qa_data]
        _log("[QA] Computing embeddings...")
        _qa_embeddings = np.array([_get_embedding(a) for a in answer_texts])
        
        def preprocess(t):
            return re.findall(r'[\wа-яё]+', t.lower())
        
        tokenized = [preprocess(a) for a in answer_texts]
        _bm25_index = BM25Okapi(tokenized)
        
        _log(f"[QA] Loaded {len(_qa_data)} Q&A pairs")
        return True
    except Exception as e:
        _log(f"[ERROR] QA load: {e}")
        return False

def _hybrid_search(query: str, top_k: int = 5, alpha: float = 0.7) -> str:
    """Гибридный поиск с проверкой намерения"""
    global _qa_data, _qa_embeddings, _bm25_index
    
    if not _qa_data or _qa_embeddings is None or _bm25_index is None:
        return _chat_fallback(query)
    
    query_lower = query.lower().strip()
    
    # 1. Семантический поиск
    query_emb = _get_embedding(query)
    semantic_scores = np.dot(_qa_embeddings, query_emb)
    
    # 2. BM25
    def preprocess(t):
        return re.findall(r'[\wа-яё]+', t.lower())
    query_tokens = preprocess(query)
    bm25_scores = _bm25_index.get_scores(query_tokens)
    
    # 3. RRF
    def rrf_rank(scores, k=60):
        ranks = np.argsort(np.argsort(-scores))
        return 1.0 / (k + len(scores) - ranks)
    
    combined = alpha * rrf_rank(semantic_scores) + (1 - alpha) * rrf_rank(bm25_scores)
    top_indices = combined.argsort()[-top_k:][::-1]
    
    # 🔍 ОТЛАДКА
    _log(f"[SEARCH] 🔍 Query: '{query}'")
    for i, idx in enumerate(top_indices):
        q = _qa_data[idx].get('question', '')
        _log(f"[SEARCH] Top-{i+1}: sem={semantic_scores[idx]:.3f}, bm25={bm25_scores[idx]:.3f} | Q: {q[:70]}...")
    
    # ✅ ПРОВЕРКА НАМЕРЕНИЯ: ищем лучший ответ с учётом ключевых слов
    best_answer = None
    
    for idx in top_indices:
        qa_question = _qa_data[idx].get('question', '').lower()
        qa_answer = _qa_data[idx].get('answer', '')
        
        # Считаем общие значимые слова
        qa_words = set(preprocess(qa_question))
        query_words = set(query_tokens)
        common = query_words & qa_words
        common_filtered = {w for w in common if len(w) > 3}  # только слова >3 символов
        
        # 🔥 КЛЮЧЕВОЕ: проверяем, есть ли в вопросе из БД слова-маркеры намерения
        intent_match = False
        
        # Если пользователь спрашивает "когда/в какой момент" — ищем вопросы с временными маркерами
        if any(w in query_lower for w in ['когда', 'момент', 'время', 'срок', 'после', 'до']):
            if any(w in qa_question for w in ['когда', 'момент', 'время', 'срок', 'после', 'до', 'итог', 'результат']):
                intent_match = True
        
        # Если пользователь спрашивает "какой/что" — ищем вопросы с вопросительными словами
        elif any(w in query_lower for w in ['какой', 'какая', 'какое', 'что', 'кто']):
            if any(w in qa_question for w in ['какой', 'какая', 'какое', 'что', 'кто']):
                intent_match = True
        
        # Если есть ≥2 общих слова И совпадение намерения — возвращаем ответ
        if len(common_filtered) >= 2 and (intent_match or semantic_scores[idx] >= 0.75):
            _log(f"[SEARCH] ✅ Match: common={common_filtered}, intent={intent_match}")
            return qa_answer
        
        # Запоминаем лучший вариант на случай если ничего не найдём
        if best_answer is None and semantic_scores[idx] >= 0.5:
            best_answer = qa_answer
    
    # Fallback: если нашли что-то близкое — возвращаем, иначе — вежливый отказ
    if best_answer:
        _log(f"[SEARCH] ⚠️ Using fallback best answer")
        return best_answer
    
    _log(f"[SEARCH] ❌ No good match found")
    return "🤷‍♂️ Не нашёл точного ответа. Попробуйте переформулировать: 'Когда формируется накладная?' или 'Какой документ после приемки?'"


def _chat_fallback(message: str) -> str:
    msg_lower = message.lower().strip()
    if any(w in msg_lower for w in ['привет', 'здравствуй']):
        return "Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?"
    elif any(w in msg_lower for w in ['риск', '100%']):
        return "⚠️ Высокий риск: новый аккаунт, нет чека, быстрый возврат, сумма >30к ₽."
    elif any(w in msg_lower for w in ['штраф', 'накладная', 'приемка', 'возврат']):
        return "Я могу найти ответ в базе знаний. Попробуйте спросить конкретнее."
    return "Я специализируюсь на оценке риска возвратов. Спросите о факторах риска."

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
            # 🔧 ИСПРАВЛЕНО: поддержка обоих форматов вызова
            # Формат 1 (ручной): --chat qa.json "вопрос" model.onnx
            # Формат 2 (Go):     --chat model.onnx "вопрос"
            
            if len(sys.argv) < 4:
                print(json.dumps({'response': 'Ошибка: не указан вопрос'}))
                sys.exit(0)
            
            # Определяем формат по расширению файла
            arg2 = sys.argv[2]
            arg3 = sys.argv[3]
            
            if arg2.endswith('.json'):
                # Формат 1: qa.json вопрос model.onnx
                qa_path = arg2
                message = arg3
                model_path = sys.argv[4] if len(sys.argv) > 4 else 'model.onnx'
            else:
                # Формат 2: model.onnx вопрос (qa.json ищем автоматически)
                model_path = arg2
                message = arg3
                qa_path = os.path.join(os.path.dirname(__file__), 'qa.json')
            
            if not message.strip():
                print(json.dumps({'response': 'Пожалуйста, задайте вопрос.'}))
                sys.exit(0)
            
            _log(f"[CHAT] Query: {message}")
            _log(f"[CHAT] QA path: {qa_path}")
            _log(f"[CHAT] Model path: {model_path}")
            
            # Инициализация модели
            if _chat_session is None:
                if not _init_chat_model(model_path):
                    print(json.dumps({'response': '⚠️ Ошибка загрузки модели'}))
                    sys.exit(0)
            
            # Загрузка QA базы
            if _qa_data is None:
                if not os.path.exists(qa_path):
                    _log(f"[WARN] qa.json not found: {qa_path}")
                    print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
                    sys.exit(0)
                if not _load_qa_data(qa_path):
                    _log("[WARN] Using fallback chat")
                    print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
                    sys.exit(0)
            
            # Поиск ответа
            try:
                response = _hybrid_search(message)
                print(json.dumps({'response': response}, ensure_ascii=False))
            except Exception as e:
                _log(f"[ERROR] Search failed: {e}")
                print(json.dumps({'response': _chat_fallback(message)}, ensure_ascii=False))
            
            sys.exit(0)
        
        else:
            print(json.dumps({'error': f'Unknown mode: {mode}'}))
            sys.exit(1)
    
    except Exception as e:
        _log(f"[FATAL] Script crashed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': 'Internal script error'}))
        sys.exit(1)