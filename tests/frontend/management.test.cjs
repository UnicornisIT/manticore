const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function element(dataset={}) {
  return {dataset, hidden:true, tabIndex:0, textContent:'', handlers:{}, attrs:{}, classList:{toggle(){}},
    setAttribute(k,v){this.attrs[k]=v}, addEventListener(k,f){this.handlers[k]=f},
    focus(){this.focused=true}, click(){this.handlers.click?.({currentTarget:this})}};
}
function fixture(desktop) {
  const themes=['system','light','dark'].map(themeValue=>element({themeValue}));
  const nodes=new Map();
  const get=s=>{if(!nodes.has(s))nodes.set(s,element());return nodes.get(s)};
  const desktopCards=[element(),element()];
  const root={querySelector:get,querySelectorAll:s=>s==='[data-theme-value]'?themes:desktopCards};
  const handlers={}, events={};let current='system';
  const document={querySelector:()=>root,getElementById:id=>get('#'+id),addEventListener:(k,f)=>events[k]=f};
  const window={addEventListener:(k,f)=>handlers[k]=f,ManticoreTheme:{get:()=>current,set:v=>{current=v;events['manticore:theme']?.()}}};
  if(desktop)window.pywebview={api:{get_client_info:async()=>({version:'1.2.3',mode:'local',database_path:'C:/test/ui.db'})}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/js/desktop-settings.js'),'utf8'),{document,window,setTimeout:f=>handlers.timer=f});
  return {themes,desktopCards,get,window,handlers};
}
test('browser keeps Windows controls hidden; theme keyboard and topbar stay synchronized',async()=>{
  const f=fixture(false);await f.handlers.timer();
  assert.ok(f.desktopCards.every(x=>x.hidden));
  assert.equal(f.themes[0].tabIndex,0);
  f.themes[0].handlers.keydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(f.window.ManticoreTheme.get(),'light');
  assert.equal(f.themes[1].attrs['aria-checked'],'true');
  assert.equal(f.themes[0].tabIndex,-1);
  f.window.ManticoreTheme.set('dark');
  assert.equal(f.themes[2].attrs['aria-checked'],'true');
});
test('late pywebview bridge exposes client settings and reads local connection',async()=>{
  const f=fixture(true);await f.handlers.pywebviewready();
  assert.ok(f.desktopCards.every(x=>!x.hidden));
  assert.equal(f.get('[data-browser-only]').hidden,true);
  assert.equal(f.get('#desktop-version').textContent,'Manticore 1.2.3');
  assert.equal(f.get('#desktop-source').textContent,'C:/test/ui.db');
});
