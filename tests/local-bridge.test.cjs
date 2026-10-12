const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const {Readable}=require('node:stream');
const B=require('../src/local-parser-zotero.js');
function pipe(stream){const iterator=stream[Symbol.asyncIterator]();return {async readString(){const n=await iterator.next();const text=n.done?'':n.value.toString();if(process.env.ZHANHTRAN_QA_DEBUG&&text)console.error(text);return text;}};}
function pythonPath(){return process.env.ZHANHTRAN_PYTHON || execFileSync('python',['-c','import sys;print(sys.executable)'],{encoding:'utf8'}).trim();}
test('one-shot native process bridge: paths with spaces, saved structure and no login or key',async()=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'zhanhtran bridge '));
 const source=path.join(folder,'fixture image.png');
 const python=pythonPath();
 const script=path.resolve(__dirname,'../src/local_parser.py');
 const make=spawn(python,['-c',"from PIL import Image,ImageDraw;import sys;i=Image.new('RGB',(500,140),'white');ImageDraw.Draw(i).text((20,30),'Hello, world.',fill='black',font_size=42);i.save(sys.argv[1])",source]);
 assert.equal(await new Promise(r=>make.on('exit',r)),0);
 let invocation;
 const bridge=B.install({rootURI:'fixture:/',profileDir:folder,join:path.join,dirname:path.dirname,normalize:path.resolve,
  uuid:()=> 'fixture-run',python:()=>python,scriptText:()=>fs.readFile(script,'utf8'),
  pdfScriptText:()=>fs.readFile(path.resolve(__dirname,'../src/local_pdf.py'),'utf8'),
  imageScriptText:()=>fs.readFile(path.resolve(__dirname,'../src/local_image.py'),'utf8'),setTimeout,clearTimeout,
  IO:{exists:async p=>!!(await fs.stat(p).catch(()=>null)),stat:async p=>({size:(await fs.stat(p)).size}),
   makeDirectory:p=>fs.mkdir(p,{recursive:true}),writeUTF8:(p,s)=>fs.writeFile(p,s),readUTF8:p=>fs.readFile(p,'utf8'),
   copy:(from,to)=>fs.copyFile(from,to,require('node:fs').constants.COPYFILE_EXCL),remove:p=>fs.rm(p,{recursive:true,force:true})},
  Subprocess:{async call(options){invocation=options;const p=spawn(options.command,options.arguments,{env:{...process.env,...options.environment}});
   p.stderr.on('data',data=>{if(process.env.ZHANHTRAN_QA_DEBUG)console.error(data.toString());});
   const waited=new Promise(r=>p.on('exit',code=>r({exitCode:code})));
   return {stdout:pipe(p.stdout),stderr:pipe(p.stderr),wait:()=>waited,kill:()=>{p.kill();return waited;}};}}});
 try {
  const doc=await bridge.parseDocument(source,new AbortController().signal);
  assert.match(doc.markdown,/Hello/);assert.equal(doc.schema,1);
  assert.ok(!JSON.stringify(invocation).match(/apiKey|Bearer|private-provider/));
  await bridge.saveResult(doc,{translated:'translated',bilingual:'bilingual',document:{...doc,translated_pages:doc.pages}});
  assert.equal(await fs.stat(path.join(doc.output_dir,'translated.md')).catch(()=>null),null);
  await assert.rejects(bridge.assetPath(doc,'../outside.png'),/资源/);
  await assert.rejects(bridge.parseDocument(source+'.exe'),/文件/);
  const document={...doc,translated_pages:doc.pages.map(page=>({...page,blocks:page.blocks.map(b=>({...b,text:'你好，世界。',
   layout_lines:b.layout_lines?.map(line=>({...line,text:'你好，世界。'}))}))}))};
  await bridge.saveResult(doc,{translated:'你好，世界。',bilingual:'Hello, world. 你好，世界。',document});
  const files=await bridge.exportFile(doc);
  assert.equal(path.dirname(files.translated),path.dirname(source));
  assert.equal(path.dirname(files.bilingual),path.dirname(source));
  assert.equal(path.extname(files.translated),'.png');assert.equal(path.extname(files.bilingual),'.png');
  assert.deepEqual([...((await fs.readFile(files.translated)).subarray(0,8))],[137,80,78,71,13,10,26,10]);
  await assert.rejects(bridge.exportPDF(doc),/输入无效/);
  const ac=new AbortController();ac.abort();await assert.rejects(bridge.parseDocument(source,ac.signal),/取消/);
 } finally {await fs.rm(folder,{recursive:true,force:true});}
});
test('real one-shot PDF export creates two native files and refuses to delete previous outputs',async()=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'zhanhtran pdf bridge '));
 const source=path.join(folder,'fixture source.pdf');
 const python=pythonPath();
 const make=spawn(python,['-c',"import pymupdf,sys;d=pymupdf.open();p=d.new_page();p.insert_text((40,60),'Hello world. Native PDF layout translation fixture.',fontsize=12);d.save(sys.argv[1])",source]);
 assert.equal(await new Promise(r=>make.on('exit',r)),0);
 const calls=[];
 const bridge=B.install({profileDir:folder,join:path.join,dirname:path.dirname,normalize:path.resolve,
  uuid:()=> 'pdf-run',python:()=>python,scriptText:()=>fs.readFile(path.resolve(__dirname,'../src/local_parser.py'),'utf8'),
  pdfScriptText:()=>fs.readFile(path.resolve(__dirname,'../src/local_pdf.py'),'utf8'),setTimeout,clearTimeout,
  IO:{exists:async p=>!!(await fs.stat(p).catch(()=>null)),stat:async p=>({size:(await fs.stat(p)).size}),
   makeDirectory:p=>fs.mkdir(p,{recursive:true}),writeUTF8:(p,s)=>fs.writeFile(p,s),readUTF8:p=>fs.readFile(p,'utf8'),
   copy:(from,to)=>fs.copyFile(from,to,require('node:fs').constants.COPYFILE_EXCL),remove:p=>fs.rm(p,{recursive:true,force:true})},
  Subprocess:{async call(options){calls.push(options);const p=spawn(options.command,options.arguments,{env:{...process.env,...options.environment}});
   const waited=new Promise(r=>p.on('exit',code=>r({exitCode:code})));
   return {stdout:pipe(p.stdout),stderr:pipe(p.stderr),wait:()=>waited,kill:()=>{p.kill();return waited;}};}}});
 try {
  const doc=await bridge.parseDocument(source,new AbortController().signal);
  const document={...doc,translated_pages:doc.pages.map(page=>({...page,blocks:page.blocks.map(b=>({...b,text:'本地布局翻译测试。'}))}))};
  await bridge.saveResult(doc,{translated:'translated',bilingual:'bilingual',document});
  const aborted=new AbortController();aborted.abort();
  await assert.rejects(bridge.exportPDF(doc,aborted.signal),/取消/);
  const pdfs=await bridge.exportPDF(doc,new AbortController().signal);
  assert.equal(path.dirname(pdfs.translated),path.dirname(source));
  assert.equal(path.dirname(pdfs.bilingual),path.dirname(source));
  assert.equal((await fs.readFile(pdfs.translated)).subarray(0,5).toString(),'%PDF-');
  assert.equal((await fs.readFile(pdfs.bilingual)).subarray(0,5).toString(),'%PDF-');
  const bytes=await fs.readFile(pdfs.translated);
  await assert.rejects(bridge.exportPDF(doc,new AbortController().signal),/已存在/);
  assert.deepEqual(await fs.readFile(pdfs.translated),bytes);
  assert.equal(calls.length,2);
  assert.ok(!JSON.stringify(calls).match(/apiKey|Bearer|private-provider/));
 }finally{await fs.rm(folder,{recursive:true,force:true});}
});
