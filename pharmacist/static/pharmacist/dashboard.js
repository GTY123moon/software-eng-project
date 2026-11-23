/* 渐进增强：普通 GET 链接与表单在禁用脚本时仍能工作。 */
(() => {
    'use strict';
    const root = document.querySelector('.pharmacist-dashboard');
    if (!root) return;
    const feedback = root.querySelector('[data-feedback]');
    const restore = () => {
        root.removeAttribute('aria-busy');
        root.querySelectorAll('[data-busy-label]').forEach((control) => {
            control.textContent = control.dataset.busyLabel;
            delete control.dataset.busyLabel;
            control.removeAttribute('aria-disabled');
        });
        if (feedback) feedback.textContent = '';
    };
    const busy = (control, message) => {
        root.setAttribute('aria-busy', 'true');
        control.dataset.busyLabel = control.textContent;
        control.textContent = message;
        control.setAttribute('aria-disabled', 'true');
        if (feedback) feedback.textContent = message;
    };
    root.querySelectorAll('[data-refresh]').forEach((link) => {
        link.addEventListener('click', (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
            if (link.hasAttribute('data-busy-label')) { event.preventDefault(); return; }
            busy(link, '正在刷新…');
        });
    });
    root.querySelectorAll('[data-query-form]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            const button = form.querySelector('button[type="submit"]');
            if (button.hasAttribute('data-busy-label')) { event.preventDefault(); return; }
            busy(button, '正在查询…');
        });
    });
    window.addEventListener('pageshow', restore);
})();
