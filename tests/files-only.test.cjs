'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const core=require('../src/custom-provider-core.js');

function fixture() {
 const state={notes:[],files:[],alerts:[],exports:[],requests:0};
 const context={AbortController,ZhanhTRANOutput:require("../src/output-settings.js")};vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/custom-provider-zotero.js'),'utf8'),context);
 const Z={Prefs:{get:()=>undefined},Libraries:{userLibraryID:1},
  Item:class {constructor(type){state.notes.push(type);}setNote(){}async saveTx(){return 1;}},
  Attachments:{linkFromFile:async options=>{state.files.push(options);return {id:state.files.length,setField(){},saveTx:async()=>{}};}}};
 const bridge={parseDocument:async()=>({schema:1,pages:[{number:1,blocks:[{id:'ocr',type:'paragraph',text:'Hello'}]}]}),
  saveResult:async()=>{},exportFile:async doc=>{state.exports.push(doc);return {translated:'/source/image-translated.png',bilingual:'/source/image-bilingual.png'};},
  renderMarkdown:async x=>x};
 const api=context.ZhanhTRANCustom.install({Zotero:Z,createProgress:require('./progress-fixture.cjs')(state),Services:{logins:{findLogins:()=>[{password:'test'}]},
  prompt:{confirm:()=>true,alert:(_w,_t,m)=>state.alerts.push(m)}},IOUtils:{},PathUtils:{},bridge,
  core:{...core,translateDocument:async()=>{state.requests++;return {document:{},translated:'你好',bilingual:'Hello 你好'};},
   translate:async()=>{state.requests++;return {translated:'你好',bilingual:'Hello 你好'};}}});
 const DOMParser=class {parseFromString(){return {body:{textContent:'Hello',innerHTML:'<p>你好</p>',querySelectorAll:()=>[]},querySelectorAll:()=>[]};}};
 return {api,state,DOMParser};
}

test('an image delivers exactly two files of its own type, never Zotero notes',async()=>{
 const {api,state,DOMParser}=fixture();
 const item={isNote:()=>false,isRegularItem:()=>false,isAttachment:()=>true,attachmentContentType:'image/png',
  getFilePathAsync:async()=>'/source/image.png',getField:()=> 'Image',parentID:7,libraryID:1};
 await api.translateSelected({DOMParser,ZoteroPane:{getSelectedItems:()=>[item]}});
 assert.deepEqual(state.alerts,[]);assert.deepEqual(state.notes,[]);
 assert.equal(state.files.length,2);assert.equal(state.exports.length,1);
 assert.ok(state.files.every(f=>f.contentType==='image/png'&&f.parentItemID===7&&f.libraryID===1));
 assert.equal(api.createNotes,undefined);
 assert.equal(state.messages[0].finished,true);assert.equal(state.messages[0].closed,false);
 assert.equal(state.messages[0].percent,100);
});

test('a note has no source-file type and is rejected before any model request or new item',async()=>{
 const {api,state,DOMParser}=fixture();
 const item={isNote:()=>true,libraryID:1,getNote:()=>'<p>Hello</p>'};
 await api.translateSelected({DOMParser,ZoteroPane:{getSelectedItems:()=>[item]}});
 assert.equal(state.requests,0);assert.deepEqual(state.notes,[]);assert.deepEqual(state.files,[]);
 assert.match(state.alerts[0],/请选择.*PDF.*图片/);
});
