'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const core=require('../src/custom-provider-core.js');

function deferred() {
  let resolve,reject;
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
  return {promise,resolve,reject};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture() {
  const state={confirms:0,allow:true,alerts:[],signals:[],retries:[],lookup:async()=>[{password:'fixture-only'}]};
  const context={AbortController,ZhanhTRANOutput:require("../src/output-settings.js")};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/custom-provider-zotero.js'),'utf8'),context);
  const win={ZoteroPane:{getSelectedItems:()=>[{isNote:()=>false,isRegularItem:()=>false,isAttachment:()=>true,
    attachmentContentType:'application/pdf',getFilePathAsync:async()=>'/fixture.pdf',getField:()=> 'Fixture',libraryID:1}]},
    DOMParser:class {parseFromString(){return {body:{textContent:'Hello'}};}}};
  const api=context.ZhanhTRANCustom.install({
    Zotero:{Prefs:{get:()=>undefined}},createProgress:require('./progress-fixture.cjs')(state),
    Services:{logins:{findLogins:()=>state.lookup()},prompt:{alert:(_win,_title,text)=>state.alerts.push(text),
      confirm:()=>{state.confirms++;return state.allow;}}},
    core:{...core,translateDocument:async(_p,_key,_document,{signal,retryAttempts})=>{
      state.signals.push(signal);state.retries.push(retryAttempts);
      return new Promise((_resolve,reject)=>{
        const timer=setTimeout(()=>reject(new Error('fixture request timeout')),500);
        signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new Error('翻译已取消'));},{once:true});
      });
    }},bridge:{parseDocument:async()=>({pages:[]})},IOUtils:{},PathUtils:{},Blob
  });
  return {api,win,state};
}

test('a second selected-item action cancels pending credentials before confirmation or requests',{timeout:2000},async()=>{
  const {api,win,state}=fixture();const lookup=deferred();state.lookup=()=>lookup.promise;
  const first=api.translateSelected(win),second=api.translateSelected(win);
  lookup.resolve([{password:'fixture-only'}]);await Promise.all([first,second]);
  assert.equal(state.confirms,0);assert.equal(state.signals.length,0);
});

test('running selected-item cancellation aborts its only request and permits a new task',async()=>{
  const {api,win,state}=fixture();
  const first=api.translateSelected(win);await tick();
  await api.translateSelected(win);await first;
  assert.equal(state.signals.length,1);assert.equal(state.signals[0].aborted,true);
  const second=api.translateSelected(win);await tick();
  await api.translateSelected(win);await second;
  assert.equal(state.signals.length,2);assert.equal(state.signals[1].aborted,true);
  assert.deepEqual(state.retries,[5,5]);
});

test('an old cancelled credential lookup cannot remove the replacement task',{timeout:2000},async()=>{
  const {api,win,state}=fixture();const old=deferred(),current=deferred();let count=0;
  state.lookup=()=>++count===1?old.promise:current.promise;
  const first=api.translateSelected(win);api.unregisterWindow(win);
  const second=api.translateSelected(win);
  old.resolve([{password:'fixture-only'}]);await first;
  const cancel=api.translateSelected(win);
  current.resolve([{password:'fixture-only'}]);await Promise.all([second,cancel]);
  assert.equal(state.confirms,0);assert.equal(state.signals.length,0);
});

test('credential lookup failure releases the selected-item task',async()=>{
  const {api,win,state}=fixture();state.lookup=async()=>{throw new Error('fixture lookup unavailable');};
  await api.translateSelected(win).catch(()=>{});
  state.lookup=async()=>[{password:'fixture-only'}];
  const next=api.translateSelected(win);await tick();await api.translateSelected(win);await next;
  assert.equal(state.signals.length,1);assert.equal(state.signals[0].aborted,true);
});

test('translation starts without confirmation and remains cancellable',async()=>{
  const {api,win,state}=fixture();state.allow=false;
  const task=api.translateSelected(win);await tick();
  assert.equal(state.confirms,0);assert.equal(state.signals.length,1);
  await api.translateSelected(win);await task;
  assert.equal(state.signals[0].aborted,true);assert.deepEqual(state.retries,[5]);
});

test('progress initialization failure releases the selected-item task',async()=>{
  const {api,win,state}=fixture();state.progressError=true;await api.translateSelected(win).catch(()=>{});
  state.progressError=false;
  const next=api.translateSelected(win);await tick();await api.translateSelected(win);await next;
  assert.equal(state.signals.length,1);assert.equal(state.signals[0].aborted,true);
});

test('unregister and shutdown close all owned messages, including unregistered owners',async()=>{
 const {api,win,state}=fixture();
 const task=api.translateSelected(win);await tick();api.unregisterWindow(win);await task;
 assert.equal(state.messages[0].closed,true);assert.equal(state.signals[0].aborted,true);
 const next=api.translateSelected(win);await tick();api.shutdown();await next;
 assert.equal(state.messages[1].closed,true);assert.equal(state.signals[1].aborted,true);
});
