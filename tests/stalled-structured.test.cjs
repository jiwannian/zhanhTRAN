const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require(process.env.ZHANHTRAN_CORE_PATH || '../src/custom-provider-core.js');
const doc={schema:1,pages:[{number:1,blocks:[
 {id:'a',type:'paragraph',text:'First'}, {id:'b',type:'paragraph',text:'Second'}]}]};
const unitsFrom=options=>JSON.parse(JSON.parse(options.body).messages.at(-1).content);
function envelope(protocol,units){
 const content=JSON.stringify(units);
 return {ok:true,json:async()=>protocol==='anthropic'?{stop_reason:'end_turn',content:[{type:'text',text:content}]}:
  {choices:[{finish_reason:'stop',message:{content}}]}};
}

test('stalled two-item recovery shrinks to isolated units without changing the shared budget',async()=>{
 for(const protocol of ['openai','anthropic']){
  const requests=[],systems=[],progress=[],retries=[],before=JSON.stringify(doc);
  const result=await C.translateDocument({...C.DEFAULT,protocol},'fixture-key',doc,{
   retryAttempts:5,retryDelayMs:1,onRetry:n=>retries.push(n),onProgress:(n,total)=>progress.push([n,total]),
   fetch:async(_url,options)=>{
    const units=unitsFrom(options),body=JSON.parse(options.body);requests.push(units.map(x=>x.id));
    systems.push(protocol==='anthropic'?body.system:body.messages[0].content);
    return envelope(protocol,units.length>1?[]:units.map(x=>({...x,text:'译文 '+x.text})));
   }});
  assert.deepEqual(requests,[['u0','u1'],['u0','u1'],['u0'],['u1']]);
  systems.slice(1).forEach((system,i)=>{
   assert.match(system,/Do not renumber/);
   for(const id of requests[i+1])assert.ok(system.includes('"'+id+'"'));
  });
  assert.deepEqual(retries,[1,2,3]);assert.deepEqual(progress,[[1,1]]);
  assert.deepEqual(result.document.translated_pages[0].blocks.map(x=>x.text),['译文 First','译文 Second']);
  assert.equal(JSON.stringify(doc),before);
 }
});

test('stalled recovery preserves completed batches and accepted cells in a final partial batch',async()=>{
 const source={schema:1,pages:[{number:1,blocks:Array.from({length:90},(_,i)=>({id:'b'+i,type:'paragraph',text:'Paragraph '+i}))}]};
 const requests=[],progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',source,{retryAttempts:5,retryDelayMs:1,
  onProgress:(n,total)=>progress.push([n,total]),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   const accepted=requests.length===3?units.slice(0,-2):requests.length===4?[]:units;
   return envelope('openai',accepted.map(x=>({...x,text:'译文 '+x.text})));
  }});
 assert.deepEqual(requests.map(x=>x.length),[40,40,10,2,1,1]);
 assert.deepEqual(requests.slice(3),[['u88','u89'],['u88'],['u89']]);
 assert.deepEqual(progress,[[1,3],[2,3],[3,3]]);
 assert.ok(result.document.translated_pages[0].blocks.every(x=>x.text==='译文 Paragraph '+x.id.slice(1)));
});

test('stalled formula recovery isolates units while retaining exact markers and layout',async()=>{
 const source={schema:1,pages:[{number:1,blocks:[0,1].map(i=>({id:'m'+i,type:'paragraph',text:'Velocity remains constant.',
  translation_text:'Velocity [[M'+i+']] remains constant.',inline_math:[{id:'M'+i,bbox:[10,10,20,20]}]}))}]};
 const before=JSON.stringify(source),requests=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',source,{retryAttempts:5,retryDelayMs:1,
  fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   return envelope('openai',units.map(x=>({...x,text:units.length>1?'速度保持不变。':'译文 '+x.text})));
  }});
 assert.deepEqual(requests,[['u0','u1'],['u0','u1'],['u0'],['u1']]);
 assert.equal(JSON.stringify(source),before);
 result.document.translated_pages[0].blocks.forEach((x,i)=>{
  assert.equal(x.translation_text,'译文 Velocity [[M'+i+']] remains constant.');
  assert.deepEqual(x.inline_math,source.pages[0].blocks[i].inline_math);
 });
});

test('stalled recovery shares six requests across network failures and isolated missing cells',async()=>{
 const requests=[],retries=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
  onRetry:n=>retries.push(n),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   if(requests.length===3)throw new Error('private network fixture');
   return envelope('openai',[4,6].includes(requests.length)?units.map(x=>({...x,text:'译文 '+x.text})):[]);
  }});
 assert.deepEqual(requests,[['u0','u1'],['u0','u1'],['u0'],['u0'],['u1'],['u1']]);
 assert.deepEqual(retries,[1,2,3,4,5]);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(x=>x.text),['译文 First','译文 Second']);
});

test('stalled recovery never fabricates missing cells or accepts invalid identities after six calls',async()=>{
 for(const invalid of [()=>[],()=>[{id:'wrong',text:'不可信'}],
  units=>[{id:units[0].id,text:'不可信'},{id:units[0].id,text:'重复'}]]){
  let calls=0;const progress=[];
  await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
   onProgress:n=>progress.push(n),fetch:async(_url,options)=>{calls++;return envelope('openai',invalid(unitsFrom(options)));}}),
   /结构化翻译缺少/);
  assert.equal(calls,6);assert.deepEqual(progress,[]);
 }
});

test('stalled recovery cancellation after one isolated success prevents all further requests',async()=>{
 const controller=new AbortController(),requests=[],progress=[];
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
  signal:controller.signal,onRetry:n=>{if(n===3)controller.abort();},onProgress:n=>progress.push(n),
  fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   return envelope('openai',units.length>1?[]:units.map(x=>({...x,text:'译文 '+x.text})));
  }}),/取消/);
 assert.deepEqual(requests,[['u0','u1'],['u0','u1'],['u0']]);assert.deepEqual(progress,[]);
});
