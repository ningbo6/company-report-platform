document.querySelectorAll('form[data-confirm],form[data-busy]').forEach(form => {
  form.addEventListener('submit', event => {
    const fileName = form.querySelector('input[type="file"]')?.files?.[0]?.name;
    const confirmation = form.dataset.confirm + (fileName ? '\n\n所选文件：' + fileName : '');
    if (form.dataset.confirm && !window.confirm(confirmation)) {
      event.preventDefault(); return;
    }
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = form.dataset.busy || '正在处理…'; }
  });
});
document.querySelectorAll('form[data-export]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    const original = button.textContent;
    button.disabled = true; button.textContent = '正在生成并逐张验证…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
      if (!response.ok) {
        const html = await response.text();
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        throw new Error(parsed.querySelector('h1')?.textContent || '导出失败，请刷新页面重新登录后重试。');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url;
      link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'qrcodes.zip';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) { window.alert(err.message); }
    finally { button.disabled = false; button.textContent = original; }
  });
});
function openUploadPanel() {
  if (window.location.hash === '#upload') {
    const panel = document.getElementById('upload');
    if (panel) { panel.open = true; panel.scrollIntoView({block: 'start'}); }
  }
}
openUploadPanel();
window.addEventListener('hashchange', openUploadPanel);
