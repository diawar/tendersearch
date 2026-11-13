// Preserve the user's price/product form if polling encounters a transport error.
(()=>{
 for(const name of ['htmx:responseError','htmx:sendError','htmx:timeout'])document.addEventListener(name,event=>{
  const root=document.getElementById('preparation-specification'),target=event.detail?.elt||event.target;
  if(!root||!(target===root||root.contains(target)))return;
  const status=root.querySelector('[data-specification-status]');
  if(status)status.textContent='Не удалось проверить загрузку. Повторим при восстановлении соединения.';
  const retry=root.querySelector('[data-specification-retry]');if(retry)retry.hidden=false;
 });
})();
