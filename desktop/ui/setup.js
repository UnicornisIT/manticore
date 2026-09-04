(function(){
  'use strict';
  const $=selector=>document.querySelector(selector);
  const setBusy=(button,busy,label)=>{button.disabled=busy;button.textContent=busy?label:button.dataset.label};
  const showError=(element,message)=>{element.textContent=message||'';element.hidden=!message};
  const api=()=>window.pywebview?.api;

  async function initialize(){
    try{
      const state=await api().get_state();
      $('#version-label').textContent=`Версия ${state.version}`;
      $('#loading-view').hidden=true;
      if(state.page==='admin-password'){
        $('#password-view').hidden=false;
        $('#admin-password').focus();
        return;
      }
      $('#configuration-view').hidden=false;
      const mode=document.querySelector(`input[name="mode"][value="${state.mode}"]`)||document.querySelector('input[name="mode"]');
      mode.checked=true;
      $('#server-url').value=state.server_url||'';
      $('#database-path').value=state.database_path||'';
      $('#update-server-url').value=state.update_server_url||'';
      syncMode();
    }catch(error){
      $('#loading-view').querySelector('h1').textContent='Не удалось открыть настройку';
      $('#loading-view').querySelector('p').textContent='Закройте окно и попробуйте запустить Manticore снова.';
    }
  }

  function syncMode(){
    const remote=$('input[name="mode"]:checked')?.value==='remote';
    $('#remote-fields').hidden=!remote;
    $('#local-fields').hidden=remote;
    (remote?$('#server-url'):$('#database-path')).focus();
  }
  document.querySelectorAll('input[name="mode"]').forEach(input=>input.addEventListener('change',syncMode));
  $('#browse-database').addEventListener('click',async()=>{const path=await api().browse_database();if(path)$('#database-path').value=path});
  document.querySelectorAll('[data-cancel]').forEach(button=>button.addEventListener('click',()=>api().cancel()));

  const configurationSubmit=$('#configuration-submit');configurationSubmit.dataset.label=configurationSubmit.textContent;
  $('#configuration-form').addEventListener('submit',async event=>{
    event.preventDefault();showError($('#configuration-error'),'');setBusy(configurationSubmit,true,'Проверяем…');
    const result=await api().submit_configuration({mode:$('input[name="mode"]:checked')?.value,server_url:$('#server-url').value,database_path:$('#database-path').value,update_server_url:$('#update-server-url').value});
    if(!result.ok){showError($('#configuration-error'),result.error);setBusy(configurationSubmit,false);return}
    configurationSubmit.textContent='Запускаем…';
  });

  const passwordSubmit=$('#password-submit');passwordSubmit.dataset.label=passwordSubmit.textContent;
  $('#password-form').addEventListener('submit',async event=>{
    event.preventDefault();showError($('#password-error'),'');setBusy(passwordSubmit,true,'Создаём…');
    const result=await api().submit_admin_password($('#admin-password').value,$('#admin-password-repeat').value);
    if(!result.ok){showError($('#password-error'),result.error);setBusy(passwordSubmit,false);return}
    passwordSubmit.textContent='Запускаем…';
  });
  window.addEventListener('pywebviewready',initialize,{once:true});
})();
