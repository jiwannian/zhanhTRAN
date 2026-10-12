'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../src/custom-provider-core.js');
const envelope=text=>({choices:[{index:0,message:{content:text},finish_reason:'stop'}]});
const event=data=>'data: '+JSON.stringify(data)+'\n\n';
const delta=(text,finish=null)=>event({choices:[{index:0,delta:{content:text},finish_reason:finish}]});
const stream=text=>delta(text)+delta('','stop')+'data: [DONE]\n\n';
const translate=(body,type='application/json',extra={})=>C.translate(C.DEFAULT,'fixture-key','Hello',{
 fetch:async()=>new Response(body,{headers:{'Content-Type':type}}),...extra});

test('nonstream JSON accepts BOM and gateway keepalive comments',async()=>{
 for(const prefix of ['\uFEFF',': keep-alive\n\n',': ping\r\n\r\n: ping\r\n\r\n'])
  assert.equal((await translate(prefix+JSON.stringify(envelope('你好')))).translated,'你好');
});
test('OpenAI SSE is accepted despite stream:false or incorrect Content-Type',async()=>{
 for(const type of ['text/event-stream','application/json'])
  assert.equal((await translate(': ping\n\n'+stream('你好'),type)).translated,'你好');
 assert.equal((await translate(event(envelope('你好'))+'data: [DONE]\n\n','text/event-stream')).translated,'你好');
});
test('SSE handles CRLF, multiline data, usage-only and reasoning events',async()=>{
 const body=': ping\r\n\r\n'+event({choices:[{index:0,delta:{reasoning_content:'private-reasoning'}}]})+
  'event: message\ndata: {"choices":\ndata: [{"index":0,"delta":{"content":"你好"}}]}\n\n'+
  delta('','stop')+event({choices:[],usage:{total_tokens:5}})+'data: [DONE]\n\n';
 const result=await translate(body,'text/event-stream');
 assert.equal(result.translated,'你好');assert.ok(!result.translated.includes('private-reasoning'));
});
test('Anthropic SSE retains only text with complete message termination',async()=>{
 const body=event({type:'message_start',message:{content:[]}})+event({type:'ping'})+
  event({type:'content_block_start',index:0,content_block:{type:'text',text:'你'}})+
  event({type:'content_block_delta',index:0,delta:{type:'thinking_delta',thinking:'private'}})+
  event({type:'content_block_delta',index:0,delta:{type:'text_delta',text:'好'}})+
  event({type:'message_delta',delta:{stop_reason:'end_turn'}})+event({type:'message_stop'});
 const result=await C.translate({...C.DEFAULT,protocol:'anthropic'},'fixture-key','Hello',{
  fetch:async()=>new Response(body,{headers:{'Content-Type':'text/event-stream'}})});
 assert.equal(result.translated,'你好');
});
test('incomplete SSE and output truncation are never returned as translations',async()=>{
 for(const body of [delta('partial'),delta('partial')+'data: [DONE]\n\n',delta('partial')+delta('','stop'),
  delta('partial')+delta('','length')+'data: [DONE]\n\n'])
  await assert.rejects(translate(body,'text/event-stream'),/截断|未完整/);
});

test('failure termination cannot be overwritten by a later successful event',async()=>{
 for(const reason of ['length','content_filter','tool_calls','function_call','refusal','unexpected'])
  await assert.rejects(translate(delta('partial',reason)+delta('','stop')+'data: [DONE]\n\n','text/event-stream'),
   /截断|未正常完成/);
 for(const reason of ['max_tokens','refusal','tool_use','pause_turn','unexpected']){
  const body=event({type:'content_block_delta',delta:{type:'text_delta',text:'partial'}})+
   event({type:'message_delta',delta:{stop_reason:reason}})+
   event({type:'message_delta',delta:{stop_reason:'end_turn'}})+event({type:'message_stop'});
  await assert.rejects(C.translate({...C.DEFAULT,protocol:'anthropic'},'fixture-key','Hello',{
   fetch:async()=>new Response(body,{headers:{'Content-Type':'text/event-stream'}})}),/截断|未正常完成/);
 }
});
test('JSON envelopes reject unsuccessful finish reasons without leaking response text',async()=>{
 for(const reason of ['content_filter','tool_calls','refusal','unexpected'])
  await assert.rejects(translate(JSON.stringify({choices:[{message:{content:'fixture-key private-body'},finish_reason:reason}]})),
   error=>/未正常完成/.test(error.message)&&!error.message.includes('fixture-key'));
 for(const reason of ['refusal','tool_use','pause_turn','unexpected'])
  await assert.rejects(C.translate({...C.DEFAULT,protocol:'anthropic'},'fixture-key','Hello',{
   fetch:async()=>new Response(JSON.stringify({content:[{type:'text',text:'partial'}],stop_reason:reason}))}),/未正常完成/);
});
test('SSE rejects malformed trailing events and unknown fields, including EOF tails',async()=>{
 for(const tail of ['data: {"error":','data: {"error":\n','<html>fixture-key</html>',
  '<html>fixture-key</html>\n\n','data: [DONE]\n\n'])
  await assert.rejects(translate(stream('partial')+tail,'text/event-stream'),
   error=>/SSE/.test(error.message)&&!error.message.includes('fixture-key'));
 await assert.rejects(translate('unknown: private-body\n\n'+stream('partial'),'text/event-stream'),/SSE/);
 await assert.rejects(translate(delta('partial')+delta('','stop')+'data: [DONE]','text/event-stream'),/未完整/);
});
test('SSE accepts id and retry prefixes and harmless comments after completion',async()=>{
 for(const prefix of ['id: fixture-event\n','retry: 1000\n'])
  for(const type of ['text/event-stream','application/json'])
   assert.equal((await translate(prefix+stream('你好')+': keep-alive',type)).translated,'你好');
});
test('successful finish forbids further text while allowing usage-only events',async()=>{
 await assert.rejects(translate(delta('hello','stop')+delta('oops')+'data: [DONE]\n\n','text/event-stream'),/结束.*正文/);
 const body=event({type:'content_block_delta',delta:{type:'text_delta',text:'hello'}})+
  event({type:'message_delta',delta:{stop_reason:'end_turn'}})+
  event({type:'content_block_delta',delta:{type:'text_delta',text:'oops'}})+event({type:'message_stop'});
 await assert.rejects(C.translate({...C.DEFAULT,protocol:'anthropic'},'fixture-key','Hello',{
  fetch:async()=>new Response(body,{headers:{'Content-Type':'text/event-stream'}})}),/结束.*正文/);
});
test('SSE supports CR-only complete frames but not missing final event boundaries',async()=>{
 assert.equal((await translate(stream('你好').replace(/\n/g,'\r'),'text/event-stream')).translated,'你好');
 await assert.rejects(translate((delta('partial')+delta('','stop')+'data: [DONE]\n').replace(/\n/g,'\r'),
  'text/event-stream'),/未完整/);
});
test('HTML, empty, malformed and stream errors provide safe distinct diagnostics',async()=>{
 for(const [body,type,pattern] of [['<html>fixture-key private-body</html>','text/html',/HTML/],
  ['','application/json',/空响应/],['{"secret":"fixture-key"','application/json',/JSON/],
  ['data: broken private-body\n\n','text/event-stream',/SSE/],
  [event({error:{message:'fixture-key private-body'}}),'text/event-stream',/供应商.*错误/]])
  await assert.rejects(translate(body,type),error=>pattern.test(error.message)&&
   !error.message.includes('fixture-key')&&!error.message.includes('private-body'));
});
test('response size is bounded and body transport errors are not JSON syntax errors',async()=>{
 await assert.rejects(translate('x'.repeat(2*1024*1024+1)),/过大/);
 const broken=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{'));c.error(new Error('fixture-key'));}});
 await assert.rejects(translate(broken),/响应.*读取失败/);
});
test('split UTF-8 chunks preserve Chinese and cancellation cancels reader',async()=>{
 const bytes=new TextEncoder().encode(stream('你好😀'));let index=0;
 const body=new ReadableStream({pull(c){if(index===bytes.length)c.close();else c.enqueue(bytes.slice(index,index+=1));}});
 assert.equal((await translate(body,'text/event-stream')).translated,'你好😀');
 let cancelled=false;const controller=new AbortController();
 const stalled=new ReadableStream({cancel(){cancelled=true;}});
 const pending=translate(stalled,'text/event-stream',{signal:controller.signal});
 setTimeout(()=>controller.abort(),15);await assert.rejects(pending,/取消/);
 await new Promise(r=>setTimeout(r,10));assert.equal(cancelled,true);
});
test('structured multi-batch SSE preserves every unit and reports progress',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:Array.from({length:90},(_,i)=>({id:'b'+i,type:'paragraph',text:'Hello '+i}))}]};
 const progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{onProgress:(n,total)=>progress.push([n,total]),
  fetch:async(url,options)=>{
   assert.match(options.headers.Accept,/application\/json/);
   const units=JSON.parse(JSON.parse(options.body).messages[1].content);
   return new Response(stream(JSON.stringify(units.map(x=>({...x,text:'你好 '+x.text})))),{headers:{'Content-Type':'text/event-stream'}});
  }});
 assert.equal(result.parts,3);assert.deepEqual(progress,[[1,3],[2,3],[3,3]]);
 assert.equal(result.document.translated_pages[0].blocks.length,90);
});
test('bounded transient retries recover an interrupted response without duplicate output',async()=>{
 let calls=0;const retries=[];
 const result=await C.translate(C.DEFAULT,'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
  onRetry:n=>retries.push(n),fetch:async()=>{
   calls++;
   if(calls<=5)return new Response(new ReadableStream({start(c){c.error(new Error('fixture-key'));}}));
   return new Response(JSON.stringify(envelope('你好')));
  }});
 assert.equal(result.translated,'你好');assert.equal(calls,6);assert.deepEqual(retries,[1,2,3,4,5]);
});
test('retries are bounded, reject invalid settings and do not retry authentication or malformed data',async()=>{
 let calls=0;
 await assert.rejects(C.translate(C.DEFAULT,'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
  fetch:async()=>{calls++;throw new Error('fixture-key');}}),/网络/);
 assert.equal(calls,6);
 for(const [body,status] of [['{}',401],['broken',200]]){
  calls=0;await assert.rejects(C.translate(C.DEFAULT,'fixture-key','Hello',{retryAttempts:5,retryDelayMs:1,
   fetch:async()=>{calls++;return new Response(body,{status});}}));assert.equal(calls,1);
 }
 for(const extra of [{retryAttempts:6},{retryAttempts:-1},{retryDelayMs:0}])
  await assert.rejects(translate(JSON.stringify(envelope('你好')),'application/json',extra),/重试设置/);
});
test('cancelling during retry backoff stops subsequent provider calls',async()=>{
 let calls=0;const controller=new AbortController();
 const pending=C.translate(C.DEFAULT,'fixture-key','Hello',{signal:controller.signal,retryAttempts:2,retryDelayMs:100,
  onRetry:()=>controller.abort(),fetch:async()=>{calls++;throw new Error('network');}});
 await assert.rejects(pending,/取消/);assert.equal(calls,1);
});
test('timeout retries remain bounded and cancellation interrupts an already scheduled backoff',async()=>{
 let calls=0;
 await assert.rejects(C.translate(C.DEFAULT,'fixture-key','Hello',{retryAttempts:1,retryDelayMs:1,timeoutMs:5,
  fetch:()=>{calls++;return new Promise(()=>{});}}),/超时/);
 assert.equal(calls,2);
 calls=0;const controller=new AbortController();
 const pending=C.translate(C.DEFAULT,'fixture-key','Hello',{retryAttempts:2,retryDelayMs:100,signal:controller.signal,
  onRetry:()=>setTimeout(()=>controller.abort(),10),fetch:async()=>{calls++;throw new Error('network');}});
 await assert.rejects(pending,/取消/);assert.equal(calls,1);
});
test('structured retries repeat only the interrupted batch and retain ordered progress',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:Array.from({length:90},(_,i)=>({id:'b'+i,type:'paragraph',text:'Hello '+i}))}]};
 const requests=[],progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:2,retryDelayMs:1,
  onProgress:(n,total)=>progress.push([n,total]),fetch:async(url,options)=>{
   const units=JSON.parse(JSON.parse(options.body).messages[1].content);requests.push(units.map(x=>x.id));
   if(requests.length===2)return new Response('{}',{status:503});
   return new Response(JSON.stringify(envelope(JSON.stringify(units.map(x=>({...x,text:'你好 '+x.text}))))));
  }});
 assert.equal(requests.length,4);assert.deepEqual(requests[1],requests[2]);
 assert.deepEqual(progress,[[1,3],[2,3],[3,3]]);assert.equal(result.document.translated_pages[0].blocks.length,90);
});
