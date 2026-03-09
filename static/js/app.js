document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.progress-segment').forEach(function (el) {
        var width = el.getAttribute('data-width');
        if (width) {
            el.style.width = width + '%';
        }
    });
});
