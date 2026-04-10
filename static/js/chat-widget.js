--- static/js/chat-widget.js (原始)


+++ static/js/chat-widget.js (修改后)
// ============================================
// 💬 AI CHAT WIDGET
// ============================================

class ChatWidget {
    constructor() {
        this.isOpen = false;
        this.messages = [];
        this.isLoading = false;
        this.init();
    }

    init() {
        this.render();
        this.bindEvents();
        this.loadHistory();
    }

    render() {
        const html = `
            <button class="chat-widget-btn" id="chatWidgetBtn" aria-label="Открыть чат">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
            </button>

            <div class="chat-widget-window" id="chatWidgetWindow">
                <div class="chat-widget-header">
                    <div class="chat-widget-title">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                            <line x1="12" y1="19" x2="12" y2="22"/>
                        </svg>
                        AI Помощник
                    </div>
                    <button class="chat-widget-close" id="chatWidgetClose" aria-label="Закрыть чат">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"/>
                            <line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                    </button>
                </div>

                <div class="chat-widget-messages" id="chatWidgetMessages">
                    <div class="chat-message assistant">
                        <div class="chat-message-avatar">🤖</div>
                        <div>
                            <div class="chat-message-content">
                                Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?
                            </div>
                            <div class="chat-message-time">${this.formatTime(new Date())}</div>
                        </div>
                    </div>
                </div>

                <div class="chat-widget-input-area">
                    <textarea
                        class="chat-widget-input"
                        id="chatWidgetInput"
                        placeholder="Введите ваш вопрос..."
                        rows="1"
                    ></textarea>
                    <button class="chat-widget-send" id="chatWidgetSend" aria-label="Отправить">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="22" y1="2" x2="11" y2="13"/>
                            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                        </svg>
                    </button>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', html);
    }

    bindEvents() {
        const btn = document.getElementById('chatWidgetBtn');
        const closeBtn = document.getElementById('chatWidgetClose');
        const sendBtn = document.getElementById('chatWidgetSend');
        const input = document.getElementById('chatWidgetInput');
        const window = document.getElementById('chatWidgetWindow');

        btn.addEventListener('click', () => this.toggle());
        closeBtn.addEventListener('click', () => this.close());
        sendBtn.addEventListener('click', () => this.sendMessage());

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-resize textarea
        input.addEventListener('input', () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        });
    }

    toggle() {
        this.isOpen ? this.close() : this.open();
    }

    open() {
        this.isOpen = true;
        const btn = document.getElementById('chatWidgetBtn');
        const window = document.getElementById('chatWidgetWindow');

        btn.classList.add('active');
        window.classList.add('open');

        setTimeout(() => {
            document.getElementById('chatWidgetInput').focus();
        }, 300);
    }

    close() {
        this.isOpen = false;
        const btn = document.getElementById('chatWidgetBtn');
        const window = document.getElementById('chatWidgetWindow');

        btn.classList.remove('active');
        window.classList.remove('open');
    }

    async sendMessage() {
        const input = document.getElementById('chatWidgetInput');
        const message = input.value.trim();

        if (!message || this.isLoading) return;

        // Добавляем сообщение пользователя
        this.addMessage(message, 'user');
        input.value = '';
        input.style.height = 'auto';

        // Показываем индикатор набора
        this.showTypingIndicator();

        this.isLoading = true;

        try {
            // Отправляем запрос на сервер
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message }),
            });

            const data = await response.json();

            this.hideTypingIndicator();

            if (data.response) {
                this.addMessage(data.response, 'assistant');
            } else if (data.error) {
                this.addMessage('Извините, произошла ошибка. Попробуйте позже.', 'assistant');
            }
        } catch (error) {
            console.error('Chat error:', error);
            this.hideTypingIndicator();
            this.addMessage('Извините, не удалось连接到服务器。Попробуйте позже.', 'assistant');
        }

        this.isLoading = false;
    }

    addMessage(text, type) {
        const messagesContainer = document.getElementById('chatWidgetMessages');
        const time = this.formatTime(new Date());

        const html = `
            <div class="chat-message ${type}">
                <div class="chat-message-avatar">${type === 'user' ? '👤' : '🤖'}</div>
                <div>
                    <div class="chat-message-content">${this.escapeHtml(text)}</div>
                    <div class="chat-message-time">${time}</div>
                </div>
            </div>
        `;

        messagesContainer.insertAdjacentHTML('beforeend', html);
        this.scrollToBottom();

        // Сохраняем в историю
        this.saveToHistory(text, type);
    }

    showTypingIndicator() {
        const messagesContainer = document.getElementById('chatWidgetMessages');
        const html = `
            <div class="chat-message assistant" id="chatTypingIndicator">
                <div class="chat-message-avatar">🤖</div>
                <div class="chat-typing-indicator">
                    <div class="chat-typing-dot"></div>
                    <div class="chat-typing-dot"></div>
                    <div class="chat-typing-dot"></div>
                </div>
            </div>
        `;

        messagesContainer.insertAdjacentHTML('beforeend', html);
        this.scrollToBottom();
    }

    hideTypingIndicator() {
        const indicator = document.getElementById('chatTypingIndicator');
        if (indicator) {
            indicator.remove();
        }
    }

    scrollToBottom() {
        const messagesContainer = document.getElementById('chatWidgetMessages');
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    formatTime(date) {
        return date.toLocaleTimeString('ru-RU', {
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    saveToHistory(message, type) {
        const history = JSON.parse(localStorage.getItem('chatHistory') || '[]');
        history.push({
            timestamp: new Date().toISOString(),
            message,
            type
        });

        // Храним последние 50 сообщений
        if (history.length > 50) {
            history.shift();
        }

        localStorage.setItem('chatHistory', JSON.stringify(history));
    }

    loadHistory() {
        // Можно загрузить историю из localStorage если нужно
        const history = JSON.parse(localStorage.getItem('chatHistory') || '[]');
        if (history.length > 0) {
            // Очищаем приветственное сообщение и загружаем историю
            const messagesContainer = document.getElementById('chatWidgetMessages');
            messagesContainer.innerHTML = '';

            history.forEach(item => {
                this.addMessage(item.message, item.type);
            });
        }
    }

    clearHistory() {
        localStorage.removeItem('chatHistory');
        const messagesContainer = document.getElementById('chatWidgetMessages');
        messagesContainer.innerHTML = `
            <div class="chat-message assistant">
                <div class="chat-message-avatar">🤖</div>
                <div>
                    <div class="chat-message-content">
                        Привет! Я AI-помощник FraudReturn Shield. Чем могу помочь?
                    </div>
                    <div class="chat-message-time">${this.formatTime(new Date())}</div>
                </div>
            </div>
        `;
    }
}

// Инициализация виджета после загрузки DOM
document.addEventListener('DOMContentLoaded', () => {
    window.chatWidget = new ChatWidget();
});