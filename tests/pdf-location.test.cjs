'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {constants}=require('node:fs');
const B=require('../src/local-parser-zotero.js');

async function fixture(t,copy,failure){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'zhanhtran location '));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const source=path.join(root,'source.pdf');await fs.writeFile(source,'source unchanged');
 const IO={exists:async p=>!!await fs.stat(p).catch(()=>null),stat:fs.stat,
  makeDirectory:p=>fs.mkdir(p,{recursive:true}),writeUTF8:fs.writeFile,readUTF8:p=>fs.readFile(p,'utf8'),
  remove:p=>fs.rm(p,{force:true,recursive:true}),
  copy:async(from,to,options)=>{assert.equal(options.noOverwrite,true);if(copy)await copy(from,to);else await fs.copyFile(from,to,constants.COPYFILE_EXCL);}};
 const bridge=B.install({IO,profileDir:path.join(root,'profile'),join:path.join,dirname:path.dirname,normalize:path.resolve,
  python:()=>source,uuid:()=> 'fixture',scriptText:async()=>'',pdfScriptText:async()=>'',setTimeout,clearTimeout,
  Subprocess:{async call({arguments:args}){
   const folder=args[args.indexOf('--output')+1];await fs.mkdir(folder,{recursive:true});
   let result={};
   if(args.includes('--document')){
    if(failure)result={ok:false,error:failure};
    else{
    result={ok:true,translated:path.join(folder,'source-translated.pdf'),bilingual:path.join(folder,'source-bilingual.pdf')};
    for(const file of [result.translated,result.bilingual])await fs.writeFile(file,'%PDF-1.7 fixture generated output');
    }
   }else await fs.writeFile(path.join(folder,'document.json'),JSON.stringify({schema:1,pages:[{blocks:[]}],output_dir:folder}));
   let read=false;
   return {wait:async()=>({exitCode:result.ok===false?2:0}),kill:async()=>{},stdout:{readString:async()=>{if(read)return '';read=true;return JSON.stringify(result);}},stderr:{readString:async()=>''}};
  }}});
 const doc=await bridge.parseDocument(source);
 await bridge.saveResult(doc,{translated:'',bilingual:'',document:doc});
 return {root,source,doc,bridge};
}

test('existing sidecars survive and both new outputs use the next matching suffix',async t=>{
 const f=await fixture(t);const old=path.join(f.root,'source-translated.pdf');await fs.writeFile(old,'keep');
 const files=await f.bridge.exportPDF(f.doc);
 assert.equal(files.translated,path.join(f.root,'source-translated-2.pdf'));
 assert.equal(files.bilingual,path.join(f.root,'source-bilingual-2.pdf'));
 assert.equal(await fs.readFile(old,'utf8'),'keep');assert.equal(await fs.readFile(f.source,'utf8'),'source unchanged');
});

test('PDF overlap failure is actionable and never publishes sidecars',async t=>{
 const f=await fixture(t,null,'PDF_OVERLAPPING_TRANSLATIONS');
 await assert.rejects(f.bridge.exportPDF(f.doc),/原文文本区域重叠.*重新解析并翻译/);
 assert.equal(await fs.stat(path.join(f.root,'source-translated.pdf')).catch(()=>null),null);
 assert.equal(await fs.readFile(f.source,'utf8'),'source unchanged');
});

test('unexpected PDF errors never expose external error text',async t=>{
 const f=await fixture(t,null,'private document text or key');
 await assert.rejects(f.bridge.exportPDF(f.doc),error=>/检查文档结构和本地依赖/.test(error.message)&&!error.message.includes('private'));
});

test('second copy failure removes only the new first sidecar',async t=>{
 let copies=0;const f=await fixture(t,async(from,to)=>{if(++copies===2)throw new Error('EACCES');await fs.copyFile(from,to,constants.COPYFILE_EXCL);});
 await assert.rejects(f.bridge.exportPDF(f.doc),/目录/);
 assert.equal(await fs.stat(path.join(f.root,'source-translated.pdf')).catch(()=>null),null);
 assert.equal(await fs.readFile(f.source,'utf8'),'source unchanged');
});

test('copy race never overwrites or cleans a foreign file',async t=>{
 const f=await fixture(t,async(from,to)=>{await fs.writeFile(to,'foreign');await fs.copyFile(from,to,constants.COPYFILE_EXCL);});
 await assert.rejects(f.bridge.exportPDF(f.doc),/目录/);
 assert.equal(await fs.readFile(path.join(f.root,'source-translated.pdf'),'utf8'),'foreign');
});

test('cancellation after first sidecar copy removes the partial delivery',async t=>{
 const ac=new AbortController();const f=await fixture(t,async(from,to)=>{await fs.copyFile(from,to,constants.COPYFILE_EXCL);ac.abort();});
 await assert.rejects(f.bridge.exportPDF(f.doc,ac.signal),/取消/);
 assert.equal(await fs.stat(path.join(f.root,'source-translated.pdf')).catch(()=>null),null);
 assert.equal(await fs.readFile(f.source,'utf8'),'source unchanged');
});
