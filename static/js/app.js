/* ============================================
   FraudReturn Shield — Frontend Logic
   Версия: 2.0
   ============================================ */

document.addEventListener('DOMContentLoaded', () => {
    console.log('🛡️ FraudReturn Shield Frontend Loaded');
    
    // Инициализация предпросмотра фото
    initPhotoPreview();
    
    // Инициализация валидации формы
    initFormValidation();
    
    // Инициализация анимаций
    initAnimations();
});

/* ============================================
   Предпросмотр фото
   ============================================ */

function initPhotoPreview() {
    const photoInput = document.getElementById('productPhoto');
    const uploadArea = document.querySelector('.upload-area');
    
    if (!photoInput || !uploadArea) return;
    
    // Клик по области загрузки
    uploadArea.addEventListener('click', () => {
        photoInput.click();
    });
    
    // Изменение файла
    photoInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            // Проверка размера (макс 5MB)
            if (file.size > 5 * 1024 * 1024) {
                showToast('Файл слишком большой (макс 5MB)', 'error');
                photoInput.value = '';
                return;
            }
            
            // Проверка типа
            if (!file.type.startsWith('image/')) {
                showToast('Только изображения (JPG, PNG)', 'error');
                photoInput.value = '';
                return;
            }
            
            // Предпросмотр
            const reader = new FileReader();
            reader.onload = (e) => {
                uploadArea.innerHTML = `
                    <img src="${e.target.result}" alt="Предпросмотр" style="max-width: 200px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
                    <p style="margin-top: 10px; font-weight: 600; color: var(--gray-700);">${file.name}</p>
                    <span class="file-hint">${(file.size / 1024).toFixed(1)} KB</span>
                `;
            };
            reader.readAsDataURL(file);
            
            showToast('Фото загружено', 'success');
        }
    });
    
    // Drag & Drop
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

/* ============================================
   Валидация формы
   ============================================ */

function initFormValidation() {
    const form = document.getElementById('fraudForm');
    if (!form) return;
    
    form.addEventListener('submit', (e) => {
        const orderNumber = document.getElementById('orderNumber')?.value.trim();
        
        if (!orderNumber) {
            e.preventDefault();
            showToast('Введите номер заказа', 'error');
            return;
        }
        
        // Анимация кнопки
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="loading"></span> Проверка...';
        }
    });
}

/* ============================================
   Анимации
   ============================================ */

function initAnimations() {
    // Анимация появления карточек
    const cards = document.querySelectorAll('.card');
    cards.forEach((card, index) => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(20px)';
        
        setTimeout(() => {
            card.style.transition = 'all 0.5s ease-out';
            card.style.opacity = '1';
            card.style.transform = 'translateY(0)';
        }, index * 100);
    });
}

/* ============================================
   Уведомления (Toast)
   ============================================ */

function showToast(message, type = 'success') {
    // Удаляем старые уведомления
    const existingToasts = document.querySelectorAll('.toast');
    existingToasts.forEach(toast => toast.remove());
    
    // Создаём новое
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${type === 'success' ? '✅' : type === 'error' ? '❌' : '⚠️'}</span>
        <span>${message}</span>
    `;
    
    document.body.appendChild(toast);
    
    // Автоудаление через 3 секунды
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.5s ease-out forwards';
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}

/* ============================================
   Утилиты
   ============================================ */

// Форматирование номера заказа
function formatOrderNumber(number) {
    return number.toUpperCase().replace(/[^A-Z0-9-]/g, '');
}

// Проверка валидности email (если понадобится)
function isValidEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

// Задержка (для анимаций)
function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/* ============================================
   Экспорт функций (для использования в HTML)
   ============================================ */

window.showToast = showToast;
window.formatOrderNumber = formatOrderNumber;