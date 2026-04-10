import sys
import json
import os
import onnxruntime as ort
import numpy as np
import traceback

# ============================================
# ГЛОБАЛЬНЫЕ СЕССИИ (КЭШИРОВАНИЕ)
# ============================================
_fraud_session = None
_fraud_input_name = None
_chat_session = None
_chat_input_names = None

def _log(msg):
    """Вывод лога ТОЛЬКО в stderr, чтобы не ломать JSON в stdout"""
    print(msg, file=sys.stderr)

# ============================================
# ЛОГИКА ФРОД-МОДЕЛИ (Fraud Detection)
# ============================================
def load_fraud_model(model_path):
    global _fraud_session, _fraud_input_name
    try:
        if not os.path.exists(model_path):
            return {'success': False, 'error': f'File not found: {model_path}'}
        
        _fraud_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        inputs = _fraud_session.get_inputs()
        _fraud_input_name = inputs[0].name if len(inputs) > 0 else 'input'
        
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
        
        # Логика извлечения скора (зависит от структуры выхода модели)
        score = None
        if len(outputs) >= 2:
            # Если output[1] это вероятности
            score = float(outputs[1][0][1])
        elif len(outputs) >= 1:
            # Если output[0] это вероятности
            if outputs[0].shape[-1] >= 2:
                score = float(outputs[0][0][1])
            else:
                score = float(outputs[0][0][0])
        
        return {'success': True, 'score': max(0.0, min(1.0, score))}
    except Exception as e:
        _log(f"[ERROR] Fraud predict: {str(e)}")
        return {'success': False, 'score': None, 'error': str(e)}

def _tokenize_text(text, max_length=128):
    """Простая токенизация для BERT-подобных моделей без внешних зависимостей"""
    # Базовая токенизация: разбиваем на слова и создаем простые ID
    # Для реальной работы нужно использовать тот же токенизатор, что и при обучении модели
    tokens = text.lower().split()

    # Создаем простые токены (это упрощение, в идеале нужен настоящий токенизатор)
    input_ids = [101]  # [CLS] токен
    attention_mask = [1]
    token_type_ids = [0]

    for token in tokens[:max_length - 2]:
        # Простое хеширование токена в число (для демонстрации)
        # В реальности здесь должен быть словарь токенов из обучения
        token_id = abs(hash(token)) % 1000 + 102  # 102+ чтобы избежать специальных токенов
        input_ids.append(token_id)
        attention_mask.append(1)
        token_type_ids.append(0)

    # Добавляем [SEP] токен
    input_ids.append(102)
    attention_mask.append(1)
    token_type_ids.append(0)

    # Pad до max_length
    while len(input_ids) < max_length:
        input_ids.append(0)
        attention_mask.append(0)
        token_type_ids.append(0)

    return {
        'input_ids': np.array([input_ids], dtype=np.int64),
        'attention_mask': np.array([attention_mask], dtype=np.int64),
        'token_type_ids': np.array([token_type_ids], dtype=np.int64)
    }


# ============================================
# ЛОГИКА ЧАТ-МОДЕЛИ (Chat AI)
# ============================================
def load_chat_model(model_path):
    global _chat_session
    try:
        _log(f"[INFO] Attempting to load Chat model: {model_path}")
        _chat_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        _log(f"[INFO] Chat model loaded successfully")
        return True
    except Exception as e:
        _log(f"[ERROR] Chat load failed: {str(e)}")
        return False

def generate_chat_response(message, model_path):
    """
    Основная функция для генерации ответа через ONNX модель.
    """
    # 1. Загрузка модели (если еще не загружена)
    if _chat_session is None:
        if not load_chat_model(model_path):
            return "Модель чата не загружена. Проверьте модель и консоль сервера."

    try:
        # 2. Подготовка входных данных (Токенизация)
        # ВАЖНО: Здесь предполагается, что модель принимает текст или байты.
        # Если ваша модель требует token_ids, этот шаг нужно адаптировать под ваш токенизатор.
        # Для большинства LLM ONNX экспортов используется UTF-8 или простые ID.
        
        # Попытка 1: Передача текста как есть (если модель принимает string/bytes)
        inputs = _chat_session.get_inputs()
        input_name = inputs[0].name
        
        # Создаем тензор из текста. 
        # dtype=object позволяет передать строку напрямую
        input_tensor = np.array([message], dtype=object)
        
        # 3. Запуск инференса
        _log(f"[DEBUG] Running inference with input: {message[:20]}...")
        outputs = _chat_session.run(None, {input_name: input_tensor})
        
        # 4. Обработка результата
        # ONNX LLM обычно возвращает либо logits (числа), либо token ids, либо текст
        result_output = outputs[0]
        
        # Если это текст (массив байтов или строк)
        if isinstance(result_output, np.ndarray) and result_output.dtype.kind in ['U', 'S', 'O']:
            # Декодируем байты если нужно
            if result_output.dtype == np.dtype('O'):
                # Объектный массив, берем первый элемент
                response = str(result_output.flatten()[0])
            else:
                response = result_output.flatten()[0]
                if isinstance(response, bytes):
                    response = response.decode('utf-8', errors='ignore')
        else:
            # Если модель вернула числа (logits/ids) без декодера
            # Мы не можем вернуть числа пользователю, поэтому выдаем фоллбэк
            _log(f"[WARN] Model returned numeric data instead of text. Output shape: {result_output.shape}")
            response = f"[Модель вернула данные, но нет декодера: {result_output.shape}]"

        return response

    except Exception as e:
        _log(f"[CRITICAL ERROR] Chat inference failed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        
        # Фоллбэк-ответ, чтобы UI не зависал
        return "Произошла ошибка при генерации ответа. Попробуйте позже."

# ============================================
# MAIN (ТОЧКА ВХОДА)
# ============================================
if __name__ == '__main__':
    # Обработка аргументов
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No mode specified'}))
        sys.exit(1)

    mode = sys.argv[1]

    try:
        if mode == '--load':
            # Загрузка фрод-модели
            path = sys.argv[2] if len(sys.argv) > 2 else 'fraud_model_v3_27patterns.onnx'
            res = load_fraud_model(path)
            print(json.dumps(res))

        elif mode == '--predict':
            # Предсказание фрода
            path = sys.argv[2]
            features_str = sys.argv[3]
            
            load_res = load_fraud_model(path)
            if not load_res['success']:
                print(json.dumps(load_res))
                sys.exit(1)
            
            try:
                features = [float(x) for x in features_str.split(',')]
                pred_res = predict_fraud(features)
                print(json.dumps(pred_res))
            except ValueError:
                print(json.dumps({'success': False, 'error': 'Invalid features format'}))

        elif mode == '--chat':
            # Чат с моделью
            # Аргументы: --chat <путь_к_model.onnx> <сообщение>
            if len(sys.argv) < 4:
                print(json.dumps({'response': 'Ошибка: не указано сообщение'}))
                sys.exit(0)
            
            chat_model_path = sys.argv[2]
            user_message = sys.argv[3]
            
            # Вызов функции генерации
            reply = generate_chat_response(user_message, chat_model_path)
            
            # Вывод чистого JSON в stdout для Go-бэкенда
            print(json.dumps({'response': reply}, ensure_ascii=False))
            sys.exit(0)

        else:
            print(json.dumps({'error': f'Unknown mode: {mode}'}))
            sys.exit(1)

    except Exception as e:
        _log(f"[FATAL] Script crashed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        # В случае краша скрипта возвращаем пустой JSON, чтобы Go не паниковал
        print(json.dumps({'error': 'Internal script error'}))
        sys.exit(1)