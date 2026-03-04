document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('fraudForm');
    const resultCard = document.getElementById('resultCard');
    const photoInput = document.getElementById('productPhoto');
    const photoPreview = document.getElementById('photoPreview');
    
    // Инициализация графиков
    if (typeof chartManager !== 'undefined') {
        chartManager.initShapChart('shapChart');
    }
    
    // Предпросмотр фото
    if (photoInput) {
        photoInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                // Проверка размера
                if (file.size > 5 * 1024 * 1024) {
                    showToast('Файл слишком большой (макс 5MB)', 'error');
                    photoInput.value = '';
                    return;
                }
                
                // Предпросмотр
                const reader = new FileReader();
                reader.onload = (e) => {
                    photoPreview.innerHTML = `
                        <img src="${e.target.result}" alt="Предпросмотр" 
                             style="max-width: 200px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
                        <p style="margin-top: 10px; font-weight: 600; color: var(--gray-700);">${file.name}</p>
                        <span class="file-hint">${(file.size / 1024).toFixed(1)} KB</span>
                    `;
                };
                reader.readAsDataURL(file);
                
                showToast('Фото загружено', 'success');
            }
        });
        
        // Drag & Drop
        const uploadArea = document.querySelector('.upload-area');
        if (uploadArea) {
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = 'var(--primary)';
                uploadArea.style.background = 'var(--gray-100)';
            });
            
            uploadArea.addEventListener('dragleave', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = 'var(--gray-300)';
                uploadArea.style.background = 'var(--gray-50)';
            });
            
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = 'var(--gray-300)';
                uploadArea.style.background = 'var(--gray-50)';
                
                const file = e.dataTransfer.files[0];
                if (file) {
                    photoInput.files = e.dataTransfer.files;
                    photoInput.dispatchEvent(new Event('change'));
                }
            });
        }
    }
    
    // Отправка формы
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const submitBtn = form.querySelector('button[type="submit"]');
            const originalText = submitBtn.innerHTML;
            submitBtn.innerHTML = '<span class="loading"></span> Проверка...';
            submitBtn.disabled = true;
            
            try {
                // Сбор данных формы
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
                    hasTag: formData.get('hasTag') === 'on',
                    hasReceipt: formData.get('hasReceipt') === 'on',
                    hasDamage: formData.get('hasDamage') === 'on',
                    isUsed: formData.get('isUsed') === 'on',
                    reason: formData.get('reason'),
                };
                
                // Вызов API (или заглушка)
                const result = await calculateRisk(data);
                
                // Отображение результата
                displayResult(result);
                
                // Сохранение в историю
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
    }
    
    // Анимации при скролле
    initScrollAnimations();
});

// Расчёт риска (заглушка для демонстрации)
async function calculateRisk(data) {
    let score = 0.0;
    const factors = [];
    
    if (data.accountAgeDays < 30) {
        score += 0.25;
        factors.push({ feature: 'Новый аккаунт', impact: 0.25 });
    }
    if (data.orderAmount > 30000) {
        score += 0.20;
        factors.push({ feature: 'Высокая сумма заказа', impact: 0.20 });
    }
    if (data.daysToReturn <= 3) {
        score += 0.15;
        factors.push({ feature: 'Быстрый возврат', impact: 0.15 });
    }
    if (data.returnRate > 0.3) {
        score += 0.20;
        factors.push({ feature: 'Высокая доля возвратов', impact: 0.20 });
    }
    if (!data.hasTag) {
        score += 0.10;
        factors.push({ feature: 'Бирка отсутствует', impact: 0.10 });
    }
    if (data.hasDamage) {
        score += 0.15;
        factors.push({ feature: 'Есть повреждения', impact: 0.15 });
    }
    if (data.isUsed) {
        score += 0.20;
        factors.push({ feature: 'Следы использования', impact: 0.20 });
    }
    if (data.reason === 'changed_mind') {
        score += 0.15;
        factors.push({ feature: 'Возврат без причины', impact: 0.15 });
    }
    
    if (score > 1.0) score = 1.0;
    
    let riskLevel, riskClass, recommendation;
    if (score <= 0.30) {
        riskLevel = 'Низкий риск';
        riskClass = 'low';
        recommendation = '✅ Автоматическое одобрение возврата';
    } else if (score <= 0.65) {
        riskLevel = 'Средний риск';
        riskClass = 'medium';
        recommendation = '⚠️ Требуется проверка оператором (звонок клиенту)';
    } else {
        riskLevel = 'Высокий риск';
        riskClass = 'high';
        recommendation = '❌ Требуется ручная верификация (фото товара, документы)';
    }
    
    return {
        ...data,
        risk_score: score,
        riskLevel,
        riskClass,
        recommendation,
        shap_values: factors
    };
}

// Отображение результата
function displayResult(result) {
    const resultCard = document.getElementById('resultCard');
    const riskScore = document.getElementById('riskScore');
    const riskText = document.getElementById('riskText');
    const riskBadge = document.getElementById('riskBadge');
    const recommendationText = document.getElementById('recommendationText');
    
    if (!resultCard) return;
    
    resultCard.style.display = 'block';
    resultCard.className = `card result-card ${result.riskClass}`;
    resultCard.scrollIntoView({ behavior: 'smooth' });
    
    // Обновление gauge
    if (typeof chartManager !== 'undefined') {
        chartManager.updateGauge(result.risk_score);
    }
    
    // Обновление текста
    if (riskText) riskText.textContent = result.riskLevel;
    if (recommendationText) recommendationText.textContent = result.recommendation;
    if (riskBadge) riskBadge.className = `info-badge ${result.riskClass}`;
    
    // Обновление SHAP
    if (result.shap_values && result.shap_values.length > 0) {
        updateShapList(result.shap_values);
        if (typeof chartManager !== 'undefined') {
            chartManager.updateShapChart(result.shap_values);
        }
    }
}

// Обновление списка SHAP
function updateShapList(shapValues) {
    const shapList = document.getElementById('shapList');
    if (!shapList) return;
    
    const sorted = [...shapValues].sort((a, b) =>
        Math.abs(b.impact) - Math.abs(a.impact)
    ).slice(0, 5);
    
    shapList.innerHTML = sorted.map(item => `
        <div class="shap-item">
            <span class="shap-feature">${item.feature}</span>
            <span class="shap-value ${item.impact >= 0 ? 'positive' : 'negative'}">
                ${item.impact >= 0 ? '+' : ''}${(item.impact * 100).toFixed(1)}%
            </span>
        </div>
    `).join('');
}

// Сохранение в историю
function saveToHistory(data, result) {
    const history = JSON.parse(localStorage.getItem('fraudHistory') || '[]');
    history.unshift({
        timestamp: new Date().toISOString(),
        data,
        result
    });
    
    // Храним последние 50 записей
    if (history.length > 50) {
        history.pop();
    }
    
    localStorage.setItem('fraudHistory', JSON.stringify(history));
}

// Сброс формы
function resetForm() {
    const form = document.getElementById('fraudForm');
    const resultCard = document.getElementById('resultCard');
    const photoPreview = document.getElementById('photoPreview');
    
    if (form) form.reset();
    if (resultCard) {
        resultCard.style.display = 'none';
        resultCard.className = 'card result-card';
    }
    if (photoPreview) photoPreview.innerHTML = '';
    
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Решения
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

// Уведомления
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${type === 'success' ? '✅' : type === 'error' ? '❌' : '⚠️'}</span>
        <span>${message}</span>
    `;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.5s ease-out forwards';
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}

// Анимации при скролле
function initScrollAnimations() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, { threshold: 0.1 });
    
    document.querySelectorAll('.animate-on-scroll').forEach(el => {
        observer.observe(el);
    });
}

// Загрузка настроек
function loadSettings() {
    const settings = JSON.parse(localStorage.getItem('fraudSettings') || '{}');
    if (settings.greenThreshold) {
        const input = document.getElementById('greenThreshold');
        if (input) input.value = settings.greenThreshold;
    }
    if (settings.yellowThreshold) {
        const input = document.getElementById('yellowThreshold');
        if (input) input.value = settings.yellowThreshold;
    }
    if (settings.shadowMode !== undefined) {
        const input = document.getElementById('shadowMode');
        if (input) input.checked = settings.shadowMode;
    }
    loadStats();
}

function saveThresholds() {
    const settings = JSON.parse(localStorage.getItem('fraudSettings') || '{}');
    const green = document.getElementById('greenThreshold');
    const yellow = document.getElementById('yellowThreshold');
    
    if (green) settings.greenThreshold = parseFloat(green.value);
    if (yellow) settings.yellowThreshold = parseFloat(yellow.value);
    
    localStorage.setItem('fraudSettings', JSON.stringify(settings));
    showToast('Пороги сохранены', 'success');
}

function saveSettings() {
    const settings = JSON.parse(localStorage.getItem('fraudSettings') || '{}');
    const shadow = document.getElementById('shadowMode');
    const notifications = document.getElementById('notificationsEnabled');
    
    if (shadow) settings.shadowMode = shadow.checked;
    if (notifications) settings.notificationsEnabled = notifications.checked;
    
    localStorage.setItem('fraudSettings', JSON.stringify(settings));
    showToast('Настройки сохранены', 'success');
}

function loadStats() {
    const history = JSON.parse(localStorage.getItem('fraudHistory') || '[]');
    
    const totalChecks = document.getElementById('totalChecks');
    const fraudDetected = document.getElementById('fraudDetected');
    const avgRisk = document.getElementById('avgRisk');
    
    if (totalChecks) totalChecks.textContent = history.length;
    
    if (fraudDetected) {
        const fraudCount = history.filter(h => h.result.risk_score > 0.65).length;
        fraudDetected.textContent = fraudCount;
    }
    
    if (avgRisk && history.length > 0) {
        const avgRiskValue = history.reduce((sum, h) => sum + h.result.risk_score, 0) / history.length;
        avgRisk.textContent = (avgRiskValue * 100).toFixed(1) + '%';
    }
}

// Инициализация при загрузке
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadSettings);
} else {
    loadSettings();
}