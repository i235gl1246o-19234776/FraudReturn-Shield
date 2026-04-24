{
  "_comment": "Спецификация входных данных для ONNX-модели FraudReturn Shield v3.0",
  "model_input": {
    "name": "input",
    "type": "float32",
    "shape": ["batch_size", 42],
    "description": "Матрица признаков размером (N x 42). Каждая строка — один возврат/транзакция.",
    "feature_order": [
      "account_age_days",
      "total_purchases",
      "total_returns",
      "customer_return_rate",
      "order_amount",
      "category",
      "high_value_flag",
      "weekend_purchase",
      "address_match",
      "device_new",
      "receipt_provided",
      "claimed_reason",
      "discount_percent",
      "promo_code_used",
      "first_order_discount_abuse",
      "is_electronics",
      "items_in_order",
      "payment_method_risk",
      "chargeback_history_90d",
      "card_bin_country_mismatch",
      "shipping_region_risk",
      "delivery_address_type",
      "distance_from_registration_city",
      "order_hour",
      "order_time_night",
      "ip_velocity_24h",
      "ip_velocity_7d",
      "accounts_per_ip",
      "accounts_per_phone",
      "accounts_per_device",
      "device_is_emulator",
      "device_trust_score",
      "ip_trust_score",
      "avg_order_amount",
      "return_rate_30d",
      "refund_velocity_7d",
      "refund_velocity_30d",
      "support_ticket_count_30d",
      "review_count_30d",
      "negative_review_cluster",
      "threat_language_detected",
      "legal_claim_threat"
    ],
    "features": {
      "account_age_days": {
        "type": "integer",
        "range": [0, 730],
        "description": "Возраст аккаунта в днях на момент возврата",
        "comment": "Меньше 7 дней увеличивает риск"
      },
      "total_purchases": {
        "type": "integer",
        "range": [0, 100],
        "description": "Общее количество покупок клиента за всю историю"
      },
      "total_returns": {
        "type": "integer",
        "range": [0, 50],
        "description": "Общее количество возвратов клиента за всю историю"
      },
      "customer_return_rate": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Доля возвратов = total_returns / total_purchases"
      },
      "order_amount": {
        "type": "float",
        "range": [0, 200000],
        "description": "Сумма заказа в рублях",
        "comment": "Высокая сумма (>30000) повышает риск"
      },
      "category": {
        "type": "integer",
        "description": "Категория товара (кодированная)",
        "mapping": {
          "0": "Электроника",
          "1": "Одежда",
          "2": "Косметика",
          "3": "Книги",
          "4": "Спорттовары",
          "-1": "Неизвестная категория"
        },
        "comment": "Кодировка из CategoryEncoder, обученного на тренировочных данных"
      },
      "high_value_flag": {
        "type": "integer",
        "values": [0, 1],
        "description": "Флаг дорогого заказа (1 если order_amount > 30000)"
      },
      "weekend_purchase": {
        "type": "integer",
        "values": [0, 1],
        "description": "Покупка в выходной день (суббота/воскресенье)"
      },
      "address_match": {
        "type": "integer",
        "values": [0, 1],
        "description": "Совпадает ли адрес доставки с адресом регистрации",
        "comment": "0 = не совпадает (подозрительно)"
      },
      "device_new": {
        "type": "integer",
        "values": [0, 1],
        "description": "Новое устройство (1 если устройство首次出现 в истории клиента)"
      },
      "receipt_provided": {
        "type": "integer",
        "values": [0, 1],
        "description": "Предоставлен ли чек при возврате",
        "comment": "0 = чек отсутствует (один из признаков receipt_fraud)"
      },
      "claimed_reason": {
        "type": "integer",
        "description": "Причина возврата (кодированная)",
        "mapping": {
          "0": "Не подошёл размер",
          "1": "Передумал",
          "2": "Брак",
          "3": "Не получил товар",
          "4": "Потерял чек",
          "5": "Возврат по гарантии",
          "6": "Проблема с доставкой",
          "7": "Нашел дешевле",
          "8": "Не работает",
          "9": "Первый заказ",
          "10": "Ошибка кассы",
          "11": "Не пришёл товар",
          "12": "Испорченный товар",
          "13": "Не соответствует описанию",
          "14": "Не тот товар",
          "-1": "Неизвестная причина"
        },
        "comment": "Полный список причин генерируется в процессе обучения"
      },
      "discount_percent": {
        "type": "float",
        "range": [0.0, 50.0],
        "description": "Процент скидки по промокоду или акции"
      },
      "promo_code_used": {
        "type": "integer",
        "values": [0, 1],
        "description": "Был ли использован промокод"
      },
      "first_order_discount_abuse": {
        "type": "integer",
        "values": [0, 1],
        "description": "Признак злоупотребления скидкой первого заказа (много аккаунтов с одним IP)"
      },
      "is_electronics": {
        "type": "integer",
        "values": [0, 1],
        "description": "Является ли товар электроникой (1) или нет (0)"
      },
      "items_in_order": {
        "type": "integer",
        "range": [1, 20],
        "description": "Количество товаров в заказе",
        "comment": "Большое количество (>8) характерно для массовых примерок"
      },
      "payment_method_risk": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Скоринг риска метода оплаты (чем выше, тем рискованнее)"
      },
      "chargeback_history_90d": {
        "type": "integer",
        "values": [0, 1],
        "description": "Были ли чарджбэки за последние 90 дней"
      },
      "card_bin_country_mismatch": {
        "type": "integer",
        "values": [0, 1],
        "description": "Не совпадает страна выпуска карты со страной доставки"
      },
      "shipping_region_risk": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Риск региона доставки (на основе истории мошенничеств)"
      },
      "delivery_address_type": {
        "type": "integer",
        "description": "Тип адреса доставки (кодированный)",
        "mapping": {
          "0": "home (домашний)",
          "1": "office (офис)",
          "2": "pickup_point (ПВЗ)",
          "-1": "Неизвестный тип"
        }
      },
      "distance_from_registration_city": {
        "type": "integer",
        "range": [0, 2000],
        "description": "Расстояние от города регистрации до города доставки (км)"
      },
      "order_hour": {
        "type": "integer",
        "range": [0, 23],
        "description": "Час оформления заказа (0-23)",
        "comment": "Ночные часы (0-5) повышают риск"
      },
      "order_time_night": {
        "type": "integer",
        "values": [0, 1],
        "description": "Флаг ночного времени (1 если order_hour в [0,1,2,3,4,5])"
      },
      "ip_velocity_24h": {
        "type": "integer",
        "range": [0, 50],
        "description": "Количество заказов с того же IP за последние 24 часа"
      },
      "ip_velocity_7d": {
        "type": "integer",
        "range": [0, 100],
        "description": "Количество заказов с того же IP за последние 7 дней"
      },
      "accounts_per_ip": {
        "type": "integer",
        "range": [1, 20],
        "description": "Количество уникальных аккаунтов, использующих данный IP"
      },
      "accounts_per_phone": {
        "type": "integer",
        "range": [1, 10],
        "description": "Количество аккаунтов, привязанных к одному номеру телефона"
      },
      "accounts_per_device": {
        "type": "integer",
        "range": [1, 15],
        "description": "Количество аккаунтов, использующих одно устройство"
      },
      "device_is_emulator": {
        "type": "integer",
        "values": [0, 1],
        "description": "Является ли устройство эмулятором (1) или реальным (0)"
      },
      "device_trust_score": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Скор доверия к устройству (выше = надёжнее)"
      },
      "ip_trust_score": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Скор доверия к IP-адресу"
      },
      "avg_order_amount": {
        "type": "float",
        "range": [0, 200000],
        "description": "Средняя сумма заказа клиента (историческая)"
      },
      "return_rate_30d": {
        "type": "float",
        "range": [0.0, 1.0],
        "description": "Доля возвратов клиента за последние 30 дней"
      },
      "refund_velocity_7d": {
        "type": "integer",
        "range": [0, 20],
        "description": "Количество возвратов за последние 7 дней"
      },
      "refund_velocity_30d": {
        "type": "integer",
        "range": [0, 50],
        "description": "Количество возвратов за последние 30 дней"
      },
      "support_ticket_count_30d": {
        "type": "integer",
        "range": [0, 20],
        "description": "Количество обращений в службу поддержки за 30 дней"
      },
      "review_count_30d": {
        "type": "integer",
        "range": [0, 50],
        "description": "Количество оставленных отзывов за 30 дней"
      },
      "negative_review_cluster": {
        "type": "integer",
        "values": [0, 1],
        "description": "Флаг принадлежности к кластеру негативных отзывов (1 = да)"
      },
      "threat_language_detected": {
        "type": "integer",
        "values": [0, 1],
        "description": "Обнаружены ли угрозы или агрессия в тексте обращения"
      },
      "legal_claim_threat": {
        "type": "integer",
        "values": [0, 1],
        "description": "Была ли угроза судебным иском или жалобой в регуляторы"
      }
    }
  },
  "output": {
    "name": "output",
    "type": "float32",
    "shape": ["batch_size", 2],
    "description": "Вероятности классов [легитимный, мошеннический]. Для принятия решения используйте второй столбец (индекс 1) — вероятность фрода."
  },
  "preprocessing_requirements": [
    "Все категориальные признаки должны быть преобразованы в целые числа с помощью обученного CategoryEncoder (файл models/category_encoder_v3.pkl).",
    "Пропущенные значения не допускаются; заполняйте нулями или значением -1 для категорий.",
    "Порядок признаков в тензоре должен строго соответствовать полю feature_order.",
    "Тип данных — float32. Целочисленные признаки можно безопасно привести к float32."
  ]
}
