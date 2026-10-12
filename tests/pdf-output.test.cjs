'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('personal-library PDF delivery links two files beside the source under the same parent and cleans partial links',async()=>{
 const imported=[],removed=[];
 const context={AbortController,ZhanhTRANOutput:require("../src/output-settings.js")};vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/custom-provider-zotero.js'),'utf8'),context);
 const Z={Libraries:{userLibraryID:1},Attachments:{linkFromFile:async options=>{
   imported.push(options);if(imported.length===2&&Z.fail)throw new Error('fixture import error');
   return {id:imported.length,setField:(name,value)=>{options[name]=value;},saveTx:async()=>{}};
 }},Items:{get:id=>({eraseTx:async()=>removed.push(id)})}};
 const api=context.ZhanhTRANCustom.install({Zotero:Z,Services:{},IOUtils:{},PathUtils:{},core:{},bridge:{}});
 assert.equal(typeof api.createFileAttachments,'function');
 const source={libraryID:1,parentID:9,title:'Paper',contentType:'application/pdf'};
 const pdfs={translated:'C:/qa/translated.pdf',bilingual:'C:/qa/bilingual.pdf'};
 const result={provider:'Provider',model:'Model'};
 const ids=await api.createFileAttachments(source,result,pdfs,new AbortController().signal);
 assert.deepEqual([...ids],[1,2]);assert.equal(imported[0].parentItemID,9);
 assert.match(imported[0].title,/译文/);assert.match(imported[1].title,/双语/);
 assert.equal(imported[0].file,pdfs.translated);
 assert.equal(imported[0].contentType,'application/pdf');
 imported.length=0;Z.fail=true;
 await assert.rejects(api.createFileAttachments(source,result,pdfs,new AbortController().signal),/fixture/);
 assert.deepEqual(removed,[1]);
});

test('standalone PDFs retain source collections; group PDFs use the same library import',async()=>{
 const calls=[];
 const context={AbortController};vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/custom-provider-zotero.js'),'utf8'),context);
 const attachment=async(mode,options)=>{calls.push({mode,...options});return {id:calls.length,setField(){},saveTx:async()=>{}};};
 const Z={Libraries:{userLibraryID:1},Attachments:{linkFromFile:o=>attachment('link',o),importFromFile:o=>attachment('import',o)}};
 const api=context.ZhanhTRANCustom.install({Zotero:Z,Services:{},IOUtils:{},PathUtils:{},core:{},bridge:{}});
 const pdfs={translated:'C:/paper/translated.pdf',bilingual:'C:/paper/bilingual.pdf'};
 await api.createFileAttachments({libraryID:1,title:'Paper',collections:[12],contentType:'application/pdf'}, {}, pdfs);
 assert.equal(calls[0].mode,'link');assert.deepEqual([...calls[0].collections],[12]);
 assert.equal(calls[0].parentItemID,undefined);
 await api.createFileAttachments({libraryID:3,parentID:7,title:'Group',contentType:'application/pdf'}, {}, pdfs);
 assert.equal(calls[2].mode,'import');assert.equal(calls[2].libraryID,3);assert.equal(calls[2].parentItemID,7);
 assert.equal(calls[2].collections,undefined);
});
