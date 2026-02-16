
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('fraudForm');
  const resultCard = document.getElementById('resultCard');
  const photoInput = document.getElementById('productPhoto');
  const photoPreview = document.getElementById('photoPreview');

  chartManager.initShapChart('shapChart');

  photoInput?.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        photoPreview.innerHTML = `
          <img src="${e.target.result}" alt="Предпросмотр" style="max-width: 200px;">
        `;
      };
      reader.readAsDataURL(file);
    }
  });

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const submitBtn = form.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<span class="loading"></span> Проверка...';
    submitBtn.disabled = true;

    try {
      const formData = new FormData(form);
      const data = {
        orderNumber: formData.get('orderNumber'),
        orderAmount: parseFloat(formData.get('orderAmount')),
        accountAgeDays: parseInt(formData.get('accountAgeDays')),
        totalOrders: parseInt(formData.get('totalOrders')),
        returnRate: parseFloat(formData.get('returnRate')),
        daysToReturn: parseInt(formData.get('daysToReturn')),
        category: formData.get('category'),
        addressMatch: formData.get('addressMatch') === 'on',
        deviceNew: formData.get('deviceNew') === 'on',
        isWeekend: formData.get('isWeekend') === 'on',
      };

      const result = await api.predict(data);

      displayResult(result);

      saveToHistory(data, result);

      showToast('Проверка завершена', 'success');

    } catch (error) {
      console.error('Error:', error);
      showToast('Ошибка проверки. Попробуйте снова.', 'error');
    } finally {
      submitBtn.innerHTML = originalText;
      submitBtn.disabled = false;
    }
  });
});

function displayResult(result) {
  const resultCard = document.getElementById('resultCard');
  const riskScore = document.getElementById('riskScore');
  const riskText = document.getElementById('riskText');
  const riskBadge = document.getElementById('riskBadge');
  const recommendationText = document.getElementById('recommendationText');

  resultCard.style.display = 'block';
  resultCard.scrollIntoView({ behavior: 'smooth' });

  chartManager.updateGauge(result.risk_score);

  let riskZone, riskClass, recommendation;
  if (result.risk_score <= 0.30) {
    riskZone = 'Низкий риск';
    riskClass = 'low';
    recommendation = 'Автоматическое одобрение возврата';
  } else if (result.risk_score <= 0.65) {
    riskZone = 'Средний риск';
    riskClass = 'medium';
    recommendation = 'Требуется проверка оператором (звонок клиенту)';
  } else {
    riskZone = 'Высокий риск';
    riskClass = 'high';
    recommendation = 'Требуется ручная верификация (фото товара, документы)';
  }

  riskText.textContent = riskZone;
  recommendationText.textContent = recommendation;
  riskBadge.className = `info-badge ${riskClass}`;

  if (result.shap_values) {
    chartManager.updateShapChart(result.shap_values);
    updateShapList(result.shap_values);
  }
}

function updateShapList(shapValues) {
  const shapList = document.getElementById('shapList');
  if (!shapList) return;

  const sorted = [...shapValues].sort((a, b) => 
    Math.abs(b.impact) - Math.abs(a.impact)
  ).slice(0, 3);

  const names = {
    'account_age_days': 'Возраст аккаунта',
    'address_match': 'Совпадение адресов',
    'days_to_return': 'Дней до возврата',
    'order_amount': 'Сумма заказа',
    'return_rate': 'Доля возвратов',
    'device_new': 'Новое устройство',
    'category': 'Категория товара'
  };

  shapList.innerHTML = sorted.map(item => `
    <div class="shap-item">
      <span class="shap-feature">${names[item.feature] || item.feature}</span>
      <span class="shap-value ${item.impact >= 0 ? 'positive' : 'negative'}">
        ${item.impact >= 0 ? '+' : ''}${(item.impact * 100).toFixed(1)}%
      </span>
    </div>
  `).join('');
}

function saveToHistory(data, result) {
  const history = JSON.parse(localStorage.getItem('fraudHistory') || '[]');
  history.unshift({
    timestamp: new Date().toISOString(),
    data,
    result
  });
  
  if (history.length > 50) {
    history.pop();
  }
  
  localStorage.setItem('fraudHistory', JSON.stringify(history));
}

function resetForm() {
  document.getElementById('fraudForm').reset();
  document.getElementById('resultCard').style.display = 'none';
  document.getElementById('photoPreview').innerHTML = '';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function approveReturn() {
  saveDecision('approved');
  showToast('Возврат одобрен', 'success');
}

function manualReview() {
  saveDecision('manual_review');
  showToast('Отправлено на проверку', 'warning');
}

function rejectReturn() {
  saveDecision('rejected');
  showToast('Возврат отклонён', 'error');
}

function saveDecision(decision) {
  const history = JSON.parse(localStorage.getItem('fraudHistory') || '[]');
  if (history.length > 0) {
    history[0].decision = decision;
    history[0].decisionTime = new Date().toISOString();
    localStorage.setItem('fraudHistory', JSON.stringify(history));
  }
}

function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 3000);
}