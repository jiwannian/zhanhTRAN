'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require(process.env.ZHANHTRAN_CORE_PATH || '../src/custom-provider-core.js');
const cfg={...C.DEFAULT,baseURL:'http://127.0.0.1:8080',model:'timeout-fixture'};
const unitsFrom=options=>JSON.parse(JSON.parse(options.body).messages.at(-1).content);
const documentWith=count=>({schema:1,pages:[{number:1,blocks:Array.from({length:count},(_,i)=>({id:'b'+i,type:'paragraph',text:'Source '+i}))}]});
function success(protocol,text){
 return new Response(JSON.stringify(protocol==='anthropic'?{stop_reason:'end_turn',content:[{type:'text',text}]}:
  {choices:[{finish_reason:'stop',message:{content:text}}]}));
}
const translated=units=>JSON.stringify(units.map(x=>({...x,text:'译文 '+x.text})));

test('HTTP 524 recovers on the fifth retry for both protocols with accurate safe diagnostics',async()=>{
 for(const protocol of ['openai','anthropic']){
  let calls=0;const retries=[];
  const result=await C.translate({...cfg,protocol},'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
   onRetry:(n,total,reason)=>retries.push({n,total,reason}),fetch:async()=>{
    calls++;return calls<=5?new Response('fixture-key private-body',{status:524}):success(protocol,'你好');
   }});
  assert.equal(calls,6);assert.equal(result.translated,'你好');
  assert.deepEqual(retries.map(x=>x.n),[1,2,3,4,5]);
  assert.ok(retries.every(x=>x.total===5&&/524.*超时/.test(x.reason)));
 }
});

test('persistent 524 stops after six calls, identifies upstream timeout, never blames credentials',async()=>{
 let calls=0,bodyRead=false;const retries=[];
 await assert.rejects(C.translate(cfg,'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
  onRetry:n=>retries.push(n),fetch:async()=>{
   calls++;return {ok:false,status:524,text:async()=>{bodyRead=true;return 'fixture-key private-body';}};
  }}),error=>/HTTP 524/.test(error.message)&&/超时/.test(error.message)&&
   !/密钥|配额|fixture-key|private-body/.test(error.message));
 assert.equal(calls,6);assert.deepEqual(retries,[1,2,3,4,5]);assert.equal(bodyRead,false);
});

test('HTTP 524 with retries disabled still reports upstream timeout',async()=>{
 let calls=0;
 await assert.rejects(C.translate(cfg,'fixture-key','Hello',{fetch:async()=>{calls++;return new Response('',{status:524});}}),
  error=>/524.*超时/.test(error.message)&&!error.message.includes('密钥'));
 assert.equal(calls,1);
});

test('gateway timeouts shrink only the current structured group, preserving ids and source',async()=>{
 for(const status of [408,504,524])for(const protocol of ['openai','anthropic']){
  const doc=documentWith(4),before=JSON.stringify(doc),requests=[],retries=[],systems=[];
  const result=await C.translateDocument({...cfg,protocol},'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
   onRetry:(n,total,reason)=>retries.push({n,total,reason}),fetch:async(_url,options)=>{
    const units=unitsFrom(options),body=JSON.parse(options.body);requests.push(units.map(x=>x.id));
    systems.push(protocol==='anthropic'?body.system:body.messages[0].content);
    return units.length>2?new Response('',{status}):success(protocol,translated(units));
   }});
  assert.deepEqual(requests,[['u0','u1','u2','u3'],['u0','u1'],['u2','u3']]);
  assert.match(retries[0].reason,new RegExp(status+'.*超时'));
  assert.deepEqual(retries.map(x=>x.n),[1,2]);
  assert.ok(systems.slice(1).every(x=>x.includes('Expected input ids:')));
  assert.deepEqual(result.document.translated_pages[0].blocks.map(x=>x.text),doc.pages[0].blocks.map(x=>'译文 '+x.text));
  assert.equal(JSON.stringify(doc),before);
 }
});

test('timeout recovery retains completed batches and accepted partial cells',async()=>{
 const doc=documentWith(44),requests=[],progress=[];
 const result=await C.translateDocument(cfg,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
  onProgress:(n,total)=>progress.push([n,total]),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   if(requests.length===3)return new Response('',{status:524});
   return success('openai',translated(requests.length===2?units.slice(0,2):units));
  }});
 assert.deepEqual(requests.map(x=>x.length),[40,4,2,1,1]);
 assert.deepEqual(requests.slice(2),[['u42','u43'],['u42'],['u43']]);
 assert.deepEqual(progress,[[1,2],[2,2]]);
 assert.equal(result.document.translated_pages[0].blocks.length,44);
});

test('mixed timeout, network and incomplete JSON recovery share one six-request budget',async()=>{
 const requests=[],retries=[];
 const result=await C.translateDocument(cfg,'fixture-key',documentWith(2),{retryAttempts:5,retryDelayMs:1,
  onRetry:n=>retries.push(n),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   if(requests.length===1||requests.length===5)return new Response('',{status:524});
   if(requests.length===3)throw new Error('private network');
   return success('openai',requests.length===2?'[]':translated(units));
  }});
 assert.deepEqual(requests,[['u0','u1'],['u0'],['u0'],['u0'],['u1'],['u1']]);
 assert.deepEqual(retries,[1,2,3,4,5]);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(x=>x.text),['译文 Source 0','译文 Source 1']);
});

test('persistent structured timeout never exports partial success or exceeds the retry budget',async()=>{
 const doc=documentWith(4),before=JSON.stringify(doc),requests=[],progress=[];
 await assert.rejects(C.translateDocument(cfg,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
  onProgress:n=>progress.push(n),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));return new Response('',{status:524});
  }}),/524.*超时/);
 assert.deepEqual(requests.map(x=>x.length),[4,2,1,1,1,1]);
 assert.deepEqual(progress,[]);assert.equal(JSON.stringify(doc),before);
});

test('cancellation during HTTP 524 backoff prevents all subsequent calls',async()=>{
 const controller=new AbortController();let calls=0;
 await assert.rejects(C.translateDocument(cfg,'fixture-key',documentWith(2),{retryAttempts:5,retryDelayMs:30,
  signal:controller.signal,onRetry:()=>setTimeout(()=>controller.abort(),5),
  fetch:async()=>{calls++;return new Response('',{status:524});}}),/取消/);
 assert.equal(calls,1);
});

test('non-timeout transient status retains the original structured group',async()=>{
 const requests=[];
 await C.translateDocument(cfg,'fixture-key',documentWith(2),{retryAttempts:5,retryDelayMs:1,
  fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units.map(x=>x.id));
   return requests.length===1?new Response('',{status:503}):success('openai',translated(units));
  }});
 assert.deepEqual(requests,[['u0','u1'],['u0','u1']]);
});

test('authentication and permanent HTTP errors still do not retry',async()=>{
 for(const status of [400,401,403,404,422,501,525,526]){
  let calls=0;
  await assert.rejects(C.translate(cfg,'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
   fetch:async()=>{calls++;return new Response('',{status});}}),new RegExp('HTTP '+status));
  assert.equal(calls,1);
 }
});
