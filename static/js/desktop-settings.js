(function(){
  'use strict';
  const root=document.querySelector('[data-desktop-settings]');if(!root)return;
  const themeButtons=Array.from(root.querySelectorAll('[data-theme-value]'));
  const syncTheme=()=>{const current=window.ManticoreTheme?.get()||'system';themeButtons.forEach(button=>{const selected=button.dataset.themeValue===current;button.classList.toggle('is-active',selected);button.setAttribute('aria-checked',String(selected));button.tabIndex=selected?0:-1})};
  themeButtons.forEach(button=>button.addEventListener('click',()=>{window.ManticoreTheme?.set(button.dataset.themeValue);syncTheme()}));syncTheme();
  document.addEventListener('manticore:theme',syncTheme);
  themeButtons.forEach((button,index)=>button.addEventListener('keydown',event=>{
    const offsets={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1};
    if(!(event.key in offsets))return;
    event.preventDefault();
    const next=themeButtons[(index+offsets[event.key]+themeButtons.length)%themeButtons.length];
    next.focus();next.click();
  }));
  let initialized=false;
  async function initializeDesktop(){if(initialized||!window.pywebview?.api)return;initialized=true;root.querySelectorAll('[data-desktop-only]').forEach(item=>item.hidden=false);root.querySelector('[data-browser-only]').hidden=true;const info=await window.pywebview.api.get_client_info();document.getElementById('desktop-version').textContent=`Manticore ${info.version}`;document.getElementById('desktop-mode').textContent=info.mode==='local'?'Локальная база':'Общий сервер';document.getElementById('desktop-source').textContent=info.mode==='local'?info.database_path:info.server_url}
  window.addEventListener('pywebviewready',initializeDesktop,{once:true});setTimeout(initializeDesktop,100);
  const status=root.querySelector('[data-client-status]');
  root.querySelector('[data-open-config]').addEventListener('click',async event=>{event.currentTarget.disabled=true;status.textContent='Открыта настройка подключения…';const result=await window.pywebview.api.reconfigure();status.textContent=result.saved?'Настройки сохранены. Перезапустите Manticore, чтобы применить их.':'Настройки не изменены.';event.currentTarget.disabled=false});
  root.querySelector('[data-open-log]').addEventListener('click',async()=>{const result=await window.pywebview.api.open_log();status.textContent=result.ok?'Журнал открыт в системном приложении.':result.error});
})();
