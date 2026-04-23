
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
    }

    render() {
        const html = `
            <button class="chat-widget-btn" id="chatWidgetBtn" aria-label="Открыть чат">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
            </button>
        `;

        document.body.insertAdjacentHTML('beforeend', html);
    }

    bindEvents() {
        const btn = document.getElementById('chatWidgetBtn');
    }
    handleClick() {
        window.location.href = '/client/chat';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.chatWidget = new ChatWidget();
});