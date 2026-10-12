'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture() {
 const context={};vm.createContext(context);
 const filename=path.join(__dirname,'../src/translation-progress.js');
 vm.runInContext(fs.readFileSync(filename,'utf8'),context,{filename});
 const elements=Object.fromEntries(['translation-status','translation-progress','translation-percent','translation-action']
  .map(id=>[id,Object.assign(new EventTarget(),{textContent:'',disabled:false,attributes:{},
   setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(key){delete this.attributes[key];}})]));
 const dialog=Object.assign(new EventTarget(),{closed:false,document:{getElementById:id=>elements[id]},
  close(){this.closed=true;this.dispatchEvent(new Event('unload'));}});
 const owner=Object.assign(new EventTarget(),{calls:[],openDialog(...args){this.calls.push(args);return dialog;}});
 const controller=new AbortController();let closed=0;
 const progress=context.ZhanhTRANTranslationProgress.create(owner,controller,()=>closed++);
 return {owner,dialog,elements,controller,progress,closed:()=>closed};
}

test('progress is a nonmodal titled/resizable window and buffers updates before load',()=>{
 const {owner,dialog,elements,controller,progress}=fixture();
 assert.match(owner.calls[0][0],/translation-progress.xhtml$/);
 assert.match(owner.calls[0][2],/titlebar=yes/);assert.match(owner.calls[0][2],/resizable=yes/);
 assert.match(owner.calls[0][2],/dialog=no/);assert.doesNotMatch(owner.calls[0][2],/modal|titlebar=no/);
 progress.setText('模型翻译 2/5');progress.setProgress(40);progress.bind(dialog);
 assert.equal(elements['translation-status'].textContent,'模型翻译 2/5');
 assert.equal(elements['translation-progress'].attributes.value,'40');
 assert.equal(elements['translation-percent'].textContent,'40%');assert.equal(controller.signal.aborted,false);
 progress.close();
});

test('cancel button aborts exactly the current job; finish turns it into close',()=>{
 const {dialog,elements,controller,progress,closed}=fixture();progress.bind(dialog);
 elements['translation-action'].dispatchEvent(new Event('click'));
 assert.equal(controller.signal.aborted,true);assert.equal(dialog.closed,false);
 assert.equal(elements['translation-action'].disabled,true);
 progress.setText('翻译已取消');progress.finish();
 assert.equal(elements['translation-action'].textContent,'关闭');assert.equal(elements['translation-action'].disabled,false);
 elements['translation-action'].dispatchEvent(new Event('click'));
 assert.equal(dialog.closed,true);assert.equal(closed(),1);
});

test('titlebar close cancels a running job and late callbacks safely do nothing',()=>{
 const {dialog,controller,progress,closed}=fixture();dialog.close();
 assert.equal(controller.signal.aborted,true);assert.equal(closed(),1);
 progress.setText('late');progress.setProgress(90);progress.bind(dialog);progress.finish();progress.close();
 assert.equal(closed(),1);
});

test('initial about:blank unload is not a user close and does not consume the close listener',()=>{
 const {dialog,elements,controller,progress,closed}=fixture();
 dialog.dispatchEvent(new Event('unload'));
 assert.equal(controller.signal.aborted,false);assert.equal(closed(),0);
 progress.setText('model ready');progress.bind(dialog);
 assert.equal(elements['translation-status'].textContent,'model ready');
 dialog.close();assert.equal(controller.signal.aborted,true);assert.equal(closed(),1);
});

test('completed messages persist, do not abort on close and close with their owner',()=>{
 const {owner,dialog,elements,controller,progress}=fixture();
 progress.finish();progress.bind(dialog);progress.setText('已创建译文与双语文件附件');progress.setProgress(100);
 assert.equal(elements['translation-action'].textContent,'关闭');assert.equal(dialog.closed,false);
 owner.dispatchEvent(new Event('unload'));
 assert.equal(dialog.closed,true);assert.equal(controller.signal.aborted,false);
});

test('unavailable dialog fails clearly and progress values cannot overflow',()=>{
 const {dialog,elements,progress}=fixture();progress.bind(dialog);
 progress.setProgress(120);assert.equal(elements['translation-percent'].textContent,'100%');
 progress.setProgress(-10);assert.equal(elements['translation-percent'].textContent,'0%');
 progress.setProgress(NaN);assert.equal(elements['translation-progress'].attributes.value,undefined);
 progress.close();
 const context={};vm.createContext(context);
 const filename=path.join(__dirname,'../src/translation-progress.js');
 vm.runInContext(fs.readFileSync(filename,'utf8'),context,{filename});
 assert.throws(()=>context.ZhanhTRANTranslationProgress.create({openDialog:()=>null},new AbortController()),/消息窗口/);
});
