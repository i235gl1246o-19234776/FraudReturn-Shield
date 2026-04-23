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

            var viewportWidth = window.innerWidth;
            var viewportHeight = window.innerHeight;

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
                shields[k].style.width = baseSize + 'px';
                shields[k].style.height = baseSize + 'px';
                shields[k].style.maxWidth = baseSize + 'px';
                shields[k].style.maxHeight = baseSize + 'px';

                var currentZoom = document.documentElement.clientWidth / window.innerWidth || 1;
                var inverseZoom = 1 / currentZoom;

                shields[k].style.setProperty('--inverse-zoom', inverseZoom);
                shields[k].style.transform = 'translate3d(0, 0, 0) scale(' + inverseZoom + ')';
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixShieldZoom);
    } else {
        fixShieldZoom();
    }

    var resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(fixShieldZoom, 100);
    });

    var wheelTimeout;
    document.addEventListener('wheel', function(e) {
        if (e.ctrlKey) {
            clearTimeout(wheelTimeout);
            wheelTimeout = setTimeout(fixShieldZoom, 50);
        }
    }, { passive: true });

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