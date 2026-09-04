(function(){
  'use strict';
  const retry=document.getElementById('retry-button');
  const settings=document.getElementById('settings-button');
  const status=document.getElementById('inline-status');
  const setBusy=(button,busy,text)=>{button.disabled=busy;if(!button.dataset.label)button.dataset.label=button.textContent;button.textContent=busy?text:button.dataset.label};
  async function initialize(){const state=await window.pywebview.api.get_connection_state();document.getElementById('target-url').textContent=state.url;document.getElementById('error-message').textContent=state.error||'Проверьте соединение и повторите попытку.'}
  retry.addEventListener('click',async()=>{status.className='inline-status';status.textContent='Проверяем соединение…';setBusy(retry,true,'Проверяем…');const result=await window.pywebview.api.retry_connection();if(!result.ok){status.className='inline-status is-error';status.textContent=result.error;setBusy(retry,false)}});
  settings.addEventListener('click',async()=>{setBusy(settings,true,'Открываем…');const result=await window.pywebview.api.reconfigure();if(result.saved){status.className='inline-status';status.textContent='Настройки сохранены. Перезапустите Manticore.'}setBusy(settings,false)});
  window.addEventListener('pywebviewready',initialize,{once:true});
})();
