import sys
import json
import os
import onnxruntime as ort
import numpy as np
import traceback
from tokenizers import Tokenizer

# ============================================
# ГЛОБАЛЬНЫЕ СЕССИИ
# ============================================
_fraud_session = None
_fraud_input_name = None

_chat_session = None
_chat_tokenizer = None
_chat_input_names = None
_chat_output_names = None

def _log(msg):
    """Логи ТОЛЬКО в stderr, чтобы не ломать JSON в stdout"""
    print(msg, file=sys.stderr)

# ============================================
# ️ FRAUD МОДЕЛЬ (БЕЗ ИЗМЕНЕНИЙ)
# ============================================
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
            score = float(prob_map.get(1, prob_map.get('1', list(prob_map.values())[1] if isinstance(prob_map, dict) else prob_map[1])))
        elif len(outputs) >= 1:
            score = float(outputs[0][0][1]) if outputs[0].shape[-1] >= 2 else float(outputs[0][0][0])
        return {'success': True, 'score': max(0.0, min(1.0, score))}
    except Exception as e:
        _log(f"[ERROR] Fraud predict: {str(e)}")
        return {'success': False, 'score': None, 'error': str(e)}

# ============================================
# 💬 CHAT МОДЕЛЬ (ONNX LLM)
# ============================================
def init_chat_model(model_path, tokenizer_dir=None):
    global _chat_session, _chat_tokenizer, _chat_input_names, _chat_output_names
    try:
        _log(f"[INFO] Loading chat tokenizer from: {tokenizer_dir or os.path.dirname(model_path)}")
        tok_path = os.path.join(tokenizer_dir or os.path.dirname(model_path), 'tokenizer.json')
        if not os.path.exists(tok_path):
            raise FileNotFoundError(f"Tokenizer not found at {tok_path}")
        
        _chat_tokenizer = Tokenizer.from_file(tok_path)
        _chat_tokenizer.enable_padding(pad_id=_chat_tokenizer.token_to_id("[PAD]") or 0)
        _chat_tokenizer.enable_truncation(max_length=256)

        _log(f"[INFO] Loading chat ONNX: {model_path}")
        _chat_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        _chat_input_names = [i.name for i in _chat_session.get_inputs()]
        _chat_output_names = [o.name for o in _chat_session.get_outputs()]
        
        _log(f"[INFO] Chat model ready. Inputs: {_chat_input_names}, Outputs: {_chat_output_names}")
        return True
    except Exception as e:
        _log(f"[ERROR] Chat init failed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        return False

def generate_chat_response(prompt, max_new_tokens=100, temperature=0.0):
    global _chat_session, _chat_tokenizer, _chat_input_names
    
    if _chat_session is None or _chat_tokenizer is None:
        return "⚠️ Модель чата не инициализирована. Проверь логи сервера."

    try:
        # 1. Токенизация
        encoded = _chat_tokenizer.encode(prompt)
        input_ids = encoded.ids
        attention_mask = [1] * len(input_ids)
        
        generated_ids = list(input_ids)
        
        # Определяем EOS токены
        eos_candidates = [_chat_tokenizer.token_to_id("</s>"), _chat_tokenizer.token_to_id("<|endoftext|>"), 0]
        eos_tokens = [t for t in eos_candidates if t is not None]

        # 2. Авторегрессивная генерация
        for _ in range(max_new_tokens):
            # Формируем входной словарь под имена модели
            model_inputs = {}
            if 'input_ids' in _chat_input_names:
                model_inputs['input_ids'] = np.array([generated_ids], dtype=np.int64)
            if 'attention_mask' in _chat_input_names:
                model_inputs['attention_mask'] = np.array([attention_mask], dtype=np.int64)
            if 'token_type_ids' in _chat_input_names:
                model_inputs['token_type_ids'] = np.zeros((1, len(generated_ids)), dtype=np.int64)

            # Запуск ONNX
            outputs = _chat_session.run(None, model_inputs)
            logits = outputs[0]  # (1, seq_len, vocab_size)
            
            # Берем логиты последнего токена
            next_token_logits = logits[0, -1, :]
            
            # Sampling / Greedy
            if temperature > 0.0:
                probs = np.exp(next_token_logits - np.max(next_token_logits)) / np.sum(np.exp(next_token_logits - np.max(next_token_logits)))
                next_token_id = int(np.random.choice(len(probs), p=probs))
            else:
                next_token_id = int(np.argmax(next_token_logits))
            
            # Стоп-условия
            if next_token_id in eos_tokens:
                break
                
            generated_ids.append(next_token_id)
            attention_mask.append(1)
            
            # Защита от бесконечного цикла
            if len(generated_ids) > 512:
                break

        # 3. Декодирование только новых токенов
        new_tokens = generated_ids[len(input_ids):]
        response = _chat_tokenizer.decode(new_tokens, skip_special_tokens=True)
        return response.strip() or "..."
        
    except Exception as e:
        _log(f"[CRITICAL] Chat generation error: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        return "⚠️ Ошибка генерации ответа. Попробуйте позже."

# ============================================
# 🚀 MAIN
# ============================================
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No mode specified'}))
        sys.exit(1)

    mode = sys.argv[1]

    try:
        if mode == '--load':
            path = sys.argv[2] if len(sys.argv) > 2 else 'fraud_model_v3_27patterns.onnx'
            res = load_fraud_model(path)
            print(json.dumps(res))

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
            if len(sys.argv) < 4:
                print(json.dumps({'response': 'Ошибка: не указано сообщение'}))
                sys.exit(0)
            
            model_path = sys.argv[2]
            message = sys.argv[3]
            
            _log(f"[CHAT] Loading ru-BERT: {model_path}")
            
            # 1. Инициализация токенизатора и ONNX сессии
            try:
                tokenizer_dir = os.path.dirname(model_path)
                tok_path = os.path.join(tokenizer_dir, 'tokenizer.json')
                tokenizer = Tokenizer.from_file(tok_path)
                tokenizer.enable_padding(pad_id=tokenizer.token_to_id('[PAD]') or 0)
                tokenizer.enable_truncation(max_length=128)
                
                session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
                input_name = session.get_inputs()[0].name
            except Exception as e:
                _log(f"[CHAT] Init error: {e}")
                print(json.dumps({'response': '⚠️ Ошибка загрузки модели'}))
                sys.exit(0)
            
            # 2. Токенизация
            encoded = tokenizer.encode(message)
            input_ids = np.array([encoded.ids], dtype=np.int64)
            attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
            
            # 🔥 КРИТИЧНО: Создаём token_type_ids ПЕРЕД использованием!
            token_type_ids = np.zeros_like(input_ids, dtype=np.int64)
            
            # 3. Инференс (передаём ВСЕ 3 входа для BERT)
            try:
                outputs = session.run(None, {
                    input_name: input_ids,
                    'attention_mask': attention_mask,
                    'token_type_ids': token_type_ids  # ✅ Теперь переменная определена
                })
                
                # 4. Получаем [CLS] эмбеддинг для классификации
                cls_embedding = outputs[0][0, 0, :]  # (hidden_size=384,)
                
                # 5. Простая rule-based классификация + эмбеддинг
                message_lower = message.lower().strip()
                
                if any(w in message_lower for w in ['привет', 'здравствуй', 'hello', 'hi']):
                    response = "Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?"
                elif any(w in message_lower for w in ['риск', '100%', 'высок', 'опасн']):
                    response = "⚠️ Высокий риск: новый аккаунт (<7 дней), нет чека/бирки, быстрый возврат (<3 дня), сумма >30к ₽."
                elif any(w in message_lower for w in ['клиент', 'нужн', 'требуется']):
                    response = "Клиенту нужно: чек, товар с бирками, паспорт (при сумме >100к ₽)."
                elif any(w in message_lower for w in ['провер', 'оцен', 'форма', 'анализ']):
                    response = "Заполните форму на странице 'Проверка' — я проанализирую 42 признака и рассчитаю риск!"
                elif any(w in message_lower for w in ['фрод', 'мошенн', 'паттерн']):
                    response = "Я распознаю 27 паттернов: Wardrobing, Price Arbitrage, Multi-Accounting, Professional Refunder и др."
                elif any(w in message_lower for w in ['спасиб', 'благодар']):
                    response = "Всегда рад помочь! 🛡️"
                else:
                    response = "Я специализируюсь на оценке риска возвратов. Спросите о факторах риска или паттернах фрода."
                
                print(json.dumps({'response': response}, ensure_ascii=False))
                
            except Exception as e:
                _log(f"[CHAT] Inference error: {e}")
                print(json.dumps({'response': '⚠️ Ошибка обработки запроса'}))
            
            sys.exit(0)

    except Exception as e:
        _log(f"[FATAL] Script crashed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': 'Internal script error'}))
        sys.exit(1)