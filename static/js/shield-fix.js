// 🛡️ FIX: Предотвращаем масштабирование щитов при zoom страницы
(function() {
    'use strict';

    var animationFrameId = null;

    function fixShieldZoom() {
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }

        animationFrameId = requestAnimationFrame(function() {
            var shields = document.querySelectorAll('.shield-svg');
            var wrappers = document.querySelectorAll('.shield-wrapper');
            var containers = document.querySelectorAll('.shield-container');

            if (shields.length === 0) return;

            // Получаем текущий размер окна
            var viewportWidth = window.innerWidth;
            var viewportHeight = window.innerHeight;

            // Фиксируем размер щита в пикселях экрана (не документа)
            // Используем меньший процент чтобы щиты точно влезали
            var baseSize = Math.min(viewportWidth, viewportHeight) * 0.35;

            for (var i = 0; i < containers.length; i++) {
                containers[i].style.width = baseSize + 'px';
                containers[i].style.height = baseSize + 'px';
            }

            for (var j = 0; j < wrappers.length; j++) {
                wrappers[j].style.width = baseSize + 'px';
                wrappers[j].style.height = baseSize + 'px';
            }

            for (var k = 0; k < shields.length; k++) {
                // Применяем фиксированный размер через inline style
                shields[k].style.width = baseSize + 'px';
                shields[k].style.height = baseSize + 'px';
                shields[k].style.maxWidth = baseSize + 'px';
                shields[k].style.maxHeight = baseSize + 'px';

                // КРИТИЧЕСКИ ВАЖНО: Инвертируем браузерный зум
                // Если страница увеличена на 150%, мы уменьшаем щит на 1/1.5 = 0.67
                var currentZoom = document.documentElement.clientWidth / window.innerWidth || 1;
                var inverseZoom = 1 / currentZoom;

                // Сохраняем пульсацию (scale 1.0-1.05) но компенсируем зум
                shields[k].style.setProperty('--inverse-zoom', inverseZoom);
                shields[k].style.transform = 'translate3d(0, 0, 0) scale(' + inverseZoom + ')';
            }
        });
    }

    // Запускаем при загрузке
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixShieldZoom);
    } else {
        fixShieldZoom();
    }

    // Пересчитываем при изменении размера окна
    var resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(fixShieldZoom, 100);
    });

    // Отслеживаем зум через wheel с Ctrl
    var wheelTimeout;
    document.addEventListener('wheel', function(e) {
        if (e.ctrlKey) {
            clearTimeout(wheelTimeout);
            wheelTimeout = setTimeout(fixShieldZoom, 50);
        }
    }, { passive: true });

    // Также отслеживаем через mutation observer для надежности
    var observer = new MutationObserver(function(mutations) {
        var needsUpdate = false;
        for (var m = 0; m < mutations.length; m++) {
            if (mutations[m].type === 'attributes' &&
                (mutations[m].attributeName === 'style' || mutations[m].attributeName === 'class')) {
                needsUpdate = true;
                break;
            }
        }
        if (needsUpdate) {
            clearTimeout(wheelTimeout);
            wheelTimeout = setTimeout(fixShieldZoom, 100);
        }
    });

    observer.observe(document.documentElement, {
        attributes: true,
        subtree: true,
        attributeFilter: ['style', 'class']
    });
})();