'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('preference scripts mount only after the host inserts its fragment',()=>{
 const listeners=new Map(),calls=[];let root=null;
 const window={addEventListener:(type,fn,capture)=>listeners.set(type,{fn,capture}),
  removeEventListener:(type,fn)=>{if(listeners.get(type)?.fn===fn)listeners.delete(type);}};
 const document={getElementById:()=>root};
 const Zotero={zhanhTRAN:{mount:(_win,node)=>{if(node)calls.push(node);}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/settings.js'),'utf8'),{window,document,Zotero});
 assert.equal(calls.length,0);
 assert.equal(listeners.get('load')?.capture,true);
 listeners.get('load').fn();assert.equal(calls.length,0);
 root={dataset:{}};listeners.get('load').fn();
 assert.deepEqual(calls,[root]);assert.equal(listeners.has('load'),false);
});

test('startup awaits successful preference registration',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../bootstrap.js'),'utf8');
 assert.match(source,/await Zotero\.PreferencePanes\.register\(/);
});
test('preference fragment omits an XML declaration and mounts on its native load event',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../src/settings.xhtml'),'utf8');
 assert.doesNotMatch(source,/<\?xml/);
 assert.match(source,/onload="Zotero\.zhanhTRAN\.mount\(/);
});
