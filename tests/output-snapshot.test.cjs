const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../src/custom-provider-core.js'),output=require('../src/output-settings.js');
function fixture(settings){
 const prefs=new Map([['extensions.zotero.zhanhtran.output',JSON.stringify(settings)]]),state={imports:[],alerts:[],calls:0,progress:[]};
 const context={AbortController,ZhanhTRANOutput:output};vm.createContext(context);
 vm.runInContext(fs.readFileSync(require.resolve('../src/custom-provider-zotero.js'),'utf8'),context);
 const api=context.ZhanhTRANCustom.install({Zotero:{Prefs:{get:k=>prefs.get(k),set:(k,v)=>prefs.set(k,v)},Libraries:{userLibraryID:1},
  Attachments:{linkFromFile:async o=>{state.imports.push(o);return {id:state.imports.length,setField(){},saveTx:async()=>{}};}}},
  Services:{logins:{findLogins:async()=>{prefs.set('extensions.zotero.zhanhtran.output',JSON.stringify({translated:true,bilingual:true,order:'original-left'}));prefs.set('extensions.zotero.zhanhtran.translationConcurrency',10);return [{password:'fixture'}];}},prompt:{alert:(_w,_t,m)=>state.alerts.push(m)}},core:{...core,translateDocument:async(_p,_key,_doc,options)=>{state.calls++;state.concurrency=options.concurrency;return {document:{},provider:'Fixture',model:'fixture'};}},
  bridge:{parseDocument:async()=>({}),saveResult:async()=>{},exportFile:async(_d,_s,output,onProgress)=>{state.output=output;onProgress?.({done:1,total:1});state.progressBeforeExport=state.progress.at(-1);return {translated:'/same/translated.pdf',bilingual:'/same/bilingual.pdf'};}},
  createProgress:()=>({setText(){},setProgress(value){state.progress.push(value);},finish(){}})});
 const win={ZoteroPane:{getSelectedItems:()=>[{isNote:()=>false,isRegularItem:()=>false,isAttachment:()=>true,attachmentContentType:'application/pdf',libraryID:1,parentID:7,getFilePathAsync:async()=>'/same/source.pdf',getField:()=> 'Fixture'}]}};
 return {api,state,win};
}
test('output snapshot precedes credential await, selective import ignores later preference changes',async()=>{
 for(const output of [{translated:true,bilingual:false,order:'original-left'},{translated:false,bilingual:true,order:'translated-left'},{translated:true,bilingual:true,order:'translated-left'}]){
  const {api,state,win}=fixture(output);api.saveConcurrency(5);await api.translateSelected(win);
  assert.deepEqual(state.alerts,[]);assert.deepEqual(state.output,output);
  assert.equal(state.concurrency,5);
  assert.equal(state.progressBeforeExport,96);
  assert.equal(state.progress.at(-1),100);
  assert.equal(state.imports.length,Number(output.translated)+Number(output.bilingual));
 }
});
test('no selected output rejects before credentials, parsing or provider calls',async()=>{
 const {api,state,win}=fixture({translated:false,bilingual:false,order:'original-left'});await api.translateSelected(win);
 assert.equal(state.calls,0);assert.equal(state.imports.length,0);assert.match(state.alerts[0],/至少选择/);
});
