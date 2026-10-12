'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const core=require('../src/custom-provider-core.js');
const context={AbortController,ZhanhTRANOutput:require("../src/output-settings.js")};vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const state={notes:[],files:[],alerts:[],confirmations:0};
const api=context.ZhanhTRANCustom.install({Zotero:{Prefs:{get:()=>undefined},Libraries:{userLibraryID:1},
 Item:class {constructor(type){state.notes.push(type);}setNote(){}async saveTx(){return state.notes.length;}},
 Attachments:{linkFromFile:async options=>{state.files.push(options);return {id:state.files.length,setField(){},saveTx:async()=>{}};}},
 ProgressWindow:class{ItemProgress=class{setText(){}setProgress(){}};changeHeadline(){}show(){}startCloseTimer(){}}},
 createProgress:require('./progress-fixture.cjs')(state),
 Services:{logins:{findLogins:()=>[{password:'synthetic'}]},prompt:{confirm:()=>{state.confirmations++;return !process.argv.includes('--deny-confirmation');},alert:(_w,_t,m)=>state.alerts.push(m)}},
 core:{...core,translateDocument:async()=>({document:{},translated:'你好',bilingual:'Hello 你好'})},IOUtils:{},PathUtils:{},
 bridge:{parseDocument:async()=>({pages:[]}),saveResult:async()=>{},renderMarkdown:async()=>'<p>你好</p>',
  exportFile:async()=>({translated:'/source/image-translated.png',bilingual:'/source/image-bilingual.png'})}});
const item={isNote:()=>false,isRegularItem:()=>false,isAttachment:()=>true,attachmentContentType:'image/png',
 getFilePathAsync:async()=>'/source/image.png',getField:()=> 'Image',parentID:7,libraryID:1};
const win={ZoteroPane:{getSelectedItems:()=>[item]},DOMParser:class{parseFromString(){return {
 body:{innerHTML:'<p>你好</p>',querySelectorAll:()=>[]},querySelectorAll:()=>[]};}}};
api.translateSelected(win).then(()=>console.log(JSON.stringify({input:'/source/image.png',notes:state.notes.length,confirmations:state.confirmations,
 files:state.files.map(f=>({path:f.file,type:f.contentType,parent:f.parentItemID,library:f.libraryID})),alerts:state.alerts}))).catch(error=>{console.error(error);process.exitCode=1;});
