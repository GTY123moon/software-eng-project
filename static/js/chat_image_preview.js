/* 聊天图片上传预览（支持多图）
 * 作用：一次可选中多张图片，逐张显示缩略图 + 文件名 + 大小，可单独移除；
 *       移除后同步回写 file input，保证提交时只发剩下的图片。
 * 用法：模板里给 form 加 data-chat-image-form，控件按下列 data 属性标记，
 *       脚本自动绑定，无需在模板里写行内 JS。
 *   [data-chat-image-form]   表单（必填）
 *   [data-chat-image-input]  file input（name="image"，需带 multiple）
 *   [data-chat-image-preview] 预览容器（初始 hidden）
 *   [data-chat-image-list]    预览列表容器（脚本动态生成条目）
 *   [data-chat-image-count]   图片按钮上的数量角标
 */
(function () {
    'use strict';

    var MAX_SIZE = 10 * 1024 * 1024; // 单张图片上限，超过提示并忽略
    var MAX_COUNT = 9;               // 与后端 images[:9] 保持一致

    function bytesText(n) {
        if (n >= 1024 * 1024) { return (n / 1024 / 1024).toFixed(1) + ' MB'; }
        return Math.max(1, Math.round(n / 1024)) + ' KB';
    }

    function initOne(form) {
        var input = form.querySelector('[data-chat-image-input]');
        var preview = form.querySelector('[data-chat-image-preview]');
        var list = form.querySelector('[data-chat-image-list]');
        var countEl = form.querySelector('[data-chat-image-count]');
        if (!input || !preview || !list) { return; }

        var items = []; // {file, url}

        function syncInput() {
            // 用 DataTransfer 回写选中文件，用户移除某张后提交的就是剩下的
            var dt = new DataTransfer();
            items.forEach(function (item) { dt.items.add(item.file); });
            input.files = dt.files;
        }

        function release(item) {
            if (item && item.url) { URL.revokeObjectURL(item.url); item.url = ''; }
        }

        function render() {
            list.textContent = '';
            items.forEach(function (item, idx) {
                var row = document.createElement('div');
                row.className = 'chat-preview-item';

                var thumb = document.createElement('img');
                thumb.className = 'chat-preview-thumb';
                thumb.src = item.url;
                thumb.alt = item.file.name;

                var meta = document.createElement('div');
                meta.className = 'chat-preview-meta';
                var name = document.createElement('span');
                name.className = 'chat-preview-name';
                name.textContent = item.file.name;
                var size = document.createElement('span');
                size.className = 'chat-preview-tip';
                size.textContent = bytesText(item.file.size) + ' · 已选中';
                meta.appendChild(name);
                meta.appendChild(size);

                var del = document.createElement('button');
                del.type = 'button';
                del.className = 'chat-preview-clear';
                del.textContent = '移除';
                del.setAttribute('aria-label', '移除 ' + item.file.name);
                del.addEventListener('click', function () { removeAt(idx); });

                row.appendChild(thumb);
                row.appendChild(meta);
                row.appendChild(del);
                list.appendChild(row);
            });

            preview.hidden = items.length === 0;
            if (countEl) { countEl.textContent = items.length ? '（' + items.length + '）' : ''; }
            if (items.length) {
                form.setAttribute('data-image-selected', String(items.length));
            } else {
                form.removeAttribute('data-image-selected');
            }
        }

        function removeAt(index) {
            release(items[index]);
            items.splice(index, 1);
            syncInput();
            render();
        }

        input.addEventListener('change', function () {
            var incoming = Array.prototype.slice.call(input.files || []);
            if (!incoming.length) { return; }

            var badType = [], tooBig = [], duplicated = 0, overflow = 0;

            incoming.forEach(function (file) {
                if (!/^image\//.test(file.type)) { badType.push(file.name); return; }
                if (file.size > MAX_SIZE) { tooBig.push(file.name); return; }
                var dup = items.some(function (item) {
                    return item.file.name === file.name &&
                        item.file.size === file.size &&
                        item.file.lastModified === file.lastModified;
                });
                if (dup) { duplicated += 1; return; }
                if (items.length >= MAX_COUNT) { overflow += 1; return; }
                items.push({ file: file, url: URL.createObjectURL(file) });
            });

            syncInput();
            render();

            var tips = [];
            if (badType.length) { tips.push('以下文件不是图片，已忽略：' + badType.join('、')); }
            if (tooBig.length) { tips.push('以下图片超过 10MB，已忽略：' + tooBig.join('、')); }
            if (overflow) { tips.push('一次最多发送 ' + MAX_COUNT + ' 张，多出的 ' + overflow + ' 张已忽略。'); }
            if (tips.length) { window.alert(tips.join('\n')); }
        });

        // 页面卸载前释放所有 blob，避免内存泄漏
        window.addEventListener('pagehide', function () {
            items.forEach(release);
        });
    }

    function init() {
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-chat-image-form]'), initOne);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
