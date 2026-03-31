import sys
import json
import os
import onnxruntime as ort
import numpy as np

_session = None
_input_name = None

def load_model(model_path):
    """Загружает ONNX модель"""
    global _session, _input_name
    try:
        model_abs = os.path.abspath(model_path)
        if not os.path.exists(model_abs):
            return {
                'success': False,
                'error': f'File not found: {model_abs}',
            }
        
        _session = ort.InferenceSession(
            model_abs,
            providers=['CPUExecutionProvider']
        )
        inputs = _session.get_inputs()
        _input_name = inputs[0].name if len(inputs) > 0 else 'input_0'
        
        print(f"Model loaded: {model_abs}", file=sys.stderr)
        return {'success': True, 'error': '', 'input_name': _input_name}
    
    except Exception as e:
        print(f"Load error: {str(e)}", file=sys.stderr)
        return {'success': False, 'error': str(e)}

def predict(features):
    """Выполняет предсказание — ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    global _session, _input_name
    if _session is None:
        return {'success': False, 'score': None, 'error': 'Model not loaded'}
    try:
        input_data = np.array(features, dtype=np.float32).reshape(1, -1)
        outputs = _session.run(None, {_input_name: input_data})
        
        # 🔧 DEBUG: показываем структуру
        print(f"DEBUG: outputs count: {len(outputs)}", file=sys.stderr)
        for i, out in enumerate(outputs):
            if hasattr(out, 'shape'):
                print(f"DEBUG: outputs[{i}] shape={out.shape}, value={out}", file=sys.stderr)
            else:
                print(f"DEBUG: outputs[{i}] type={type(out)}, value={out}", file=sys.stderr)
        
        # 🔧 ИСПРАВЛЕНИЕ: берём вероятности из outputs[1]
        if len(outputs) >= 2:
            # outputs[1] содержит словарь {класс: вероятность}
            prob_dict = outputs[1][0] if isinstance(outputs[1], (list, np.ndarray)) else outputs[1]
            
            if isinstance(prob_dict, dict):
                # Извлекаем вероятность фрода (класс 1)
                fraud_prob = prob_dict.get(1, prob_dict.get('1', 0.0))
                score = float(fraud_prob)
                print(f"DEBUG: Using dict probabilities, fraud_prob={score}", file=sys.stderr)
            else:
                # Fallback: если не словарь, пробуем стандартный подход
                probs = outputs[0][0] if len(outputs[0].shape) > 1 else outputs[0]
                if len(probs) >= 2:
                    score = float(probs[1])
                else:
                    score = float(probs[0])
        else:
            # Fallback для моделей с одним выходом
            probs = outputs[0][0] if len(outputs[0].shape) > 1 else outputs[0]
            if len(probs) >= 2:
                score = float(probs[1])
            else:
                score = float(probs[0])
        
        # Нормализация
        score = max(0.0, min(1.0, score))
        print(f"DEBUG: final score={score}", file=sys.stderr)
        
        return {'success': True, 'score': score, 'error': ''}
        
    except Exception as e:
        print(f"Predict error: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {'success': False, 'score': None, 'error': str(e)}
    
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'Usage: --load or --predict'}))
        sys.exit(1)
    
    mode = sys.argv[1]
    
    if mode == '--load':
        model_path = sys.argv[2] if len(sys.argv) > 2 else 'fraud_model_v3_27patterns.onnx'
        result = load_model(model_path)
        print(json.dumps(result))
        sys.exit(0 if result['success'] else 1)
    
    elif mode == '--predict':
        model_path = sys.argv[2]
        features_str = sys.argv[3]
        
        load_result = load_model(model_path)
        if not load_result['success']:
            print(json.dumps(load_result))
            sys.exit(1)
        
        try:
            features = [float(x.strip()) for x in features_str.split(',')]
        except ValueError:
            print(json.dumps({'success': False, 'error': 'Invalid features'}))
            sys.exit(1)
        
        prediction = predict(features)
        print(json.dumps(prediction))
        sys.exit(0 if prediction['success'] else 1)
    
    else:
        print(json.dumps({'success': False, 'error': f'Unknown mode: {mode}'}))
        sys.exit(1)