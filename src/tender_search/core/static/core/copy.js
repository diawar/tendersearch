document.addEventListener('click', async event => {
 const button=event.target.closest('[data-copy-text]');
 if(!button||button.disabled)return;
 button.disabled=true;
 const feedback=button.querySelector('.copy-feedback');
 try {
  await navigator.clipboard.writeText(button.dataset.copyText);
  feedback.textContent='Скопировано';
 } catch {
  feedback.textContent='Не удалось скопировать';
 } finally {
  button.disabled=false;
  clearTimeout(button.copyTimer);
  button.copyTimer=setTimeout(()=>{feedback.textContent='';},2000);
 }
});
