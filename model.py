import sys
import json
import os
import onnxruntime as ort
import numpy as np

_session = None
_input_name = None

def _log(msg):
    """ВСЕ логи ТОЛЬКО в stderr"""
    print(msg, file=sys.stderr)

def load_model(model_path):
    """Загружает ONNX модель"""
    global _session, _input_name
    try:
        model_abs = os.path.abspath(model_path).replace('\\', '/')
        if not os.path.exists(model_abs):
            _log(f"[ERROR] File not found: {model_abs}")
            return {'success': False, 'error': f'File not found: {model_abs}'}
        
        _session = ort.InferenceSession(model_abs, providers=['CPUExecutionProvider'])
        inputs = _session.get_inputs()
        _input_name = inputs[0].name if len(inputs) > 0 else 'input_0'
        
        _log(f"[INFO] Model loaded: {model_abs}")
        return {'success': True, 'error': '', 'input_name': _input_name}
    
    except Exception as e:
        _log(f"[ERROR] Load error: {str(e)}")
        return {'success': False, 'error': str(e)}


def predict(features):
    """Выполняет предсказание"""
    global _session, _input_name
    
    if _session is None:
        return {'success': False, 'score': None, 'error': 'Model not loaded'}
    
    try:
        input_data = np.array(features, dtype=np.float32).reshape(1, -1)
        outputs = _session.run(None, {_input_name: input_data})
        
        _log(f"[DEBUG] outputs count: {len(outputs)}")
        
        score = None
        
        # Пробуем извлечь вероятность из outputs[1]
        if len(outputs) >= 2:
            prob_output = outputs[1]
            
            if isinstance(prob_output, (list, np.ndarray)) and len(prob_output) > 0:
                prob_dict = prob_output[0]
            else:
                prob_dict = prob_output
            
            if isinstance(prob_dict, dict):
                fraud_prob = prob_dict.get(1) or prob_dict.get('1') or prob_dict.get(1.0)
                if fraud_prob is not None:
                    score = float(fraud_prob)
                    _log(f"[DEBUG] fraud_prob={score}")
        
        # Fallback
        if score is None and len(outputs) >= 1:
            probs = outputs[0]
            if hasattr(probs, 'shape') and len(probs.shape) > 1 and probs.shape[-1] >= 2:
                score = float(probs[0][1])
            elif hasattr(probs, '__len__') and len(probs) >= 2:
                score = float(probs[1])
            else:
                score = float(probs[0] if hasattr(probs, '__len__') else probs)
            _log(f"[DEBUG] fallback score={score}")
        
        if score is None:
            return {'success': False, 'score': None, 'error': 'Could not extract probability'}
        
        score = max(0.0, min(1.0, score))
        _log(f"[DEBUG] final score={score}")
        
        return {'success': True, 'score': score, 'error': ''}
        
    except Exception as e:
        _log(f"[ERROR] Predict error: {str(e)}")
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
        print(json.dumps(result))  # ← ЕДИНСТВЕННОЕ в stdout
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
        print(json.dumps(prediction))  # ← ЕДИНСТВЕННОЕ в stdout
        sys.exit(0 if prediction['success'] else 1)
    
    else:
        print(json.dumps({'success': False, 'error': f'Unknown mode: {mode}'}))
        sys.exit(1)