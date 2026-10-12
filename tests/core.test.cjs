'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const C = require('../src/custom-provider-core.js');
const cfg = {id:'fixture', name:'Fixture provider',baseURL:'https://provider.invalid',model:'your-model',protocol:'openai',language:'简体中文'};
test('defaults and root/v1 normalization',()=>{
 assert.equal(C.DEFAULT.model,cfg.model); assert.equal(C.DEFAULT.baseURL,cfg.baseURL);
 assert.equal(C.endpoint(cfg),'https://provider.invalid/v1/chat/completions');
 assert.equal(C.endpoint({...cfg,baseURL:cfg.baseURL+'/v1/'}),C.endpoint(cfg));
 assert.equal(C.endpoint({...cfg,protocol:'anthropic'}),'https://provider.invalid/v1/messages');
});
test('provider boundary rejects unsafe input',()=>{
 for(const baseURL of ['http://example.com','https://u:p@example.com','https://example.com/?key=x','https://example.com/#x','file:///x'])
  assert.throws(()=>C.validate({...cfg,baseURL}));
 for(const update of [{name:''},{model:''},{id:'../x'},{protocol:'other'},{language:''}])assert.throws(()=>C.validate({...cfg,...update}));
 assert.equal(C.validate({...cfg,baseURL:'http://127.0.0.1:8080/v1'}).baseURL,'http://127.0.0.1:8080/v1');
});
test('bounded lossless chunking, emoji and newlines',()=>{
 const text='# Title\n\n'+('Hello 😀 world.\n'.repeat(300));
 const chunks=C.chunk(text,500); assert.equal(chunks.join(''),text);
 assert.ok(chunks.length>1); assert.ok(chunks.every(x=>x.length<=500));
 assert.ok(chunks.every(x=>!/[\uD800-\uDBFF]$/.test(x)));
 assert.deepEqual(C.chunk(''),[]); assert.throws(()=>C.chunk('a',1));
});
test('OpenAI and Anthropic request shapes use only supplied key',()=>{
 const a=C.request(cfg,'fixture-key','Hello');
 assert.equal(a.headers.Authorization,'Bearer fixture-key');
 assert.equal(a.body.model,cfg.model);assert.equal(a.body.stream,false);
 const b=C.request({...cfg,protocol:'anthropic'},'fixture-key','Hello');
 assert.equal(b.headers['x-api-key'],'fixture-key');assert.equal(b.headers.Authorization,undefined);
 assert.equal(b.body.messages[0].role,'user');assert.equal(b.body.max_tokens,8192);
 assert.throws(()=>C.request(cfg,'','Hello'));assert.throws(()=>C.request(cfg,'a\nb','Hello'));
});
test('complete response extraction and truncation detection',()=>{
 assert.equal(C.responseText('openai',{choices:[{message:{content:'你好'},finish_reason:'stop'}]}),'你好');
 assert.equal(C.responseText('openai',{choices:[{message:{content:[{type:'text',text:'好'}]}}]}),'好');
 assert.equal(C.responseText('anthropic',{content:[{type:'text',text:'你好'}],stop_reason:'end_turn'}),'你好');
 for(const [p,d] of [['openai',{}],['anthropic',{}],['openai',{choices:[{message:{content:'x'},finish_reason:'length'}]}],['anthropic',{content:[{type:'text',text:'x'}],stop_reason:'max_tokens'}]])assert.throws(()=>C.responseText(p,d));
});
test('translation ordering, progress, preserved Markdown',async()=>{
 let count=0;const progress=[];
 const result=await C.translate(cfg,'fixture-key','Hello\n\nworld',{
  maxChars:8,onProgress:(a,b)=>progress.push([a,b]),
  fetch:async(url,opts)=>{count++;return {ok:true,status:200,json:async()=>({choices:[{message:{content:JSON.parse(opts.body).messages[1].content.toUpperCase()}}]})};}
 });
 assert.equal(result.translated,'HELLO\n\nWORLD');assert.equal(result.original,'Hello\n\nworld');assert.ok(count>1);
 assert.ok(result.bilingual.includes('HELLO'));assert.equal(progress.at(-1)[0],count);
});
test('provider errors cannot echo credentials, network or raw payload',async()=>{
 for(const fetch of [async()=>({ok:false,status:401}),async()=>{throw new Error('fixture-key private-body');},async()=>({ok:true,status:200,json:async()=>{throw new Error('private-body')}})]) {
  await assert.rejects(C.translate(cfg,'fixture-key','Hello',{fetch}),e=>!e.message.includes('fixture-key')&&!e.message.includes('private-body'));
 }
});
test('timeout and abort stop requests',async()=>{
 const stalled=async(u,o)=>new Promise((resolve,reject)=>o.signal.addEventListener('abort',()=>reject(new Error('Aborted')),{once:true}));
 await assert.rejects(C.translate(cfg,'fixture-key','Hello',{fetch:stalled,timeoutMs:15}),/超时/);
 const ac=new AbortController();ac.abort();await assert.rejects(C.translate(cfg,'fixture-key','Hello',{signal:ac.signal,fetch:stalled}),/取消/);
});
