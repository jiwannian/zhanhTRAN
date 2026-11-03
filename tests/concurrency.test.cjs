'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../src/custom-provider-core.js');
const doc=(n=100)=>({schema:1,pages:[{number:1,blocks:Array.from({length:n},(_,i)=>({id:'b'+i,type:'paragraph',text:'Synthetic paragraph '+i}))}]});
const units=options=>JSON.parse(JSON.parse(options.body).messages.at(-1).content);
const envelope=entries=>({ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:JSON.stringify(entries.map(e=>({...e,text:'译文 '+e.text})))}}]})});
const pause=ms=>new Promise(r=>setTimeout(r,ms));

test('default two requests complete out of order and retain source order and unique progress',async()=>{
 let active=0,peak=0;const completed=[],progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc(),{onTranslationProgress:(done,total,n)=>progress.push([done,total,n]),fetch:async(_url,options)=>{
  const entries=units(options);active++;peak=Math.max(peak,active);
  await pause(entries[0].id==='u0'?55:5);active--;completed.push(entries[0].id);return envelope(entries);
 }});
 assert.equal(peak,2);assert.equal(completed[0],'u40');
 assert.deepEqual(result.document.translated_pages[0].blocks.map(b=>b.text),doc().pages[0].blocks.map(b=>'译文 '+b.text));
 assert.equal(progress.at(-1)[2],100);assert.equal(progress.at(-1)[0],progress.at(-1)[1]);
 assert.ok(progress.every((p,i)=>!i||p[0]>progress[i-1][0]));
});

test('one and ten request limits are enforced and invalid values fail before requests',async()=>{
 for(const concurrency of [1,10]){
  let active=0,peak=0;
  await C.translateDocument(C.DEFAULT,'fixture-key',doc(401),{concurrency,fetch:async(_url,options)=>{
   active++;peak=Math.max(peak,active);await pause(4);active--;return envelope(units(options));
  }});
  assert.equal(peak,concurrency);
 }
 for(const concurrency of [0,11,1.5,NaN,'2'])
  await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',doc(),{concurrency,fetch:()=>assert.fail('invalid settings sent a request')}),/并发/);
});

test('rate limiting obeys Retry-After and reduces subsequent requests to one',async()=>{
 const starts=[],counts=new Map();let first429;
 await C.translateDocument(C.DEFAULT,'fixture-key',doc(161),{retryAttempts:5,retryDelayMs:1,fetch:async(_url,options)=>{
  const entries=units(options),id=entries[0].id,count=(counts.get(id)||0)+1;counts.set(id,count);starts.push([id,Date.now()]);
  if(id==='u0'&&count===1){first429=Date.now();return {ok:false,status:429,headers:{get:()=>'0.03'}};}
  await pause(4);return envelope(entries);
 }});
 assert.ok(starts.find(([id,t])=>id==='u0'&&t>first429)?.[1]-first429>=25);
 assert.ok(starts.findIndex(([id])=>id==='u80')>starts.findIndex(([id],i)=>id==='u0'&&i>0));
});

test('failure aborts active siblings and prevents queued work or late progress',async()=>{
 let aborted=false;const calls=[],progress=[];
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',doc(161),{onProgress:n=>progress.push(n),fetch:async(_url,options)=>{
  const entries=units(options);calls.push(entries[0].id);
  if(entries[0].id==='u0'){await pause(5);return {ok:false,status:401};}
  return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{aborted=true;reject(new Error('aborted'));},{once:true}));
 }}),/HTTP 401/);
 assert.equal(aborted,true);assert.deepEqual(calls,['u0','u40']);assert.deepEqual(progress,[]);
});
