const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../src/custom-provider-core.js');
const fixture={schema:1,pages:[{number:1,blocks:[
 {id:'p1-b1',type:'heading',level:1,text:'Research'},
 {id:'p1-b2',type:'paragraph',text:'Hello, world.'},
 {id:'p1-b3',type:'table',rows:[['Model','Score'],['Alpha','95']]},
 {id:'p1-b4',type:'figure',asset:'figures/p1.png',text:'Accuracy'},
 {id:'p1-b5',type:'formula',text:'$E=mc^2$'}]}],assets:[]};
const respond=async(url,options)=>{
 const body=JSON.parse(options.body);
 const units=JSON.parse(body.messages.at(-1).content);
 const map={'Research':'研究','Hello, world.':'你好，世界。','Model':'模型','Score':'分数','Alpha':'阿尔法','Accuracy':'准确率'};
 return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:JSON.stringify(units.map(x=>({...x,text:map[x.text]||x.text})))}}]})};
};
test('image OCR lines are translated independently at unchanged source positions',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:[{id:'image',type:'figure',text:'First\nSecond',
   layout_lines:[{text:'First',bbox:[0,0,100,12]},{text:'Second',bbox:[0,20,100,32]}]}]}]};
 const before=JSON.stringify(doc);
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{fetch:async(_url,options)=>{
   const units=JSON.parse(JSON.parse(options.body).messages.at(-1).content);
   assert.deepEqual(units.map(x=>x.text),['First','Second']);
   return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:JSON.stringify(units.map(x=>({...x,text:'译文 '+x.text})))}}]})};
 }});
 assert.equal(JSON.stringify(doc),before);
 const block=result.document.translated_pages[0].blocks[0];
 assert.equal(block.text,'译文 First\n译文 Second');
 assert.deepEqual(block.layout_lines.map(x=>x.bbox),doc.pages[0].blocks[0].layout_lines.map(x=>x.bbox));
});
test('equation and algorithm prose runs retain all original mathematical content and positions',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:[{id:'algo',type:'table',rows:[['E = mc2; if searching']],
  layout_runs:[{text:'if searching',bbox:[100,20,160,30]}]}]}]};
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{fetch:async(_url,options)=>{
  const units=JSON.parse(JSON.parse(options.body).messages.at(-1).content);
  assert.deepEqual(units.map(x=>x.text),['if searching']);
  return envelope(units.map(x=>({...x,text:'如果正在搜索'})));
 }});
 const block=result.document.translated_pages[0].blocks[0];
 assert.deepEqual(block.rows,doc.pages[0].blocks[0].rows);
 assert.equal(block.layout_runs[0].text,'如果正在搜索');
 assert.deepEqual(block.layout_runs[0].bbox,[100,20,160,30]);
});
test('structured translation retains cell count, figure reference, formulas and immutable source',async()=>{
 const before=JSON.stringify(fixture);
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',fixture,{fetch:respond});
 assert.equal(JSON.stringify(fixture),before);
 assert.match(result.translated,/\| 模型 \| 分数 \|/);
 assert.match(result.translated,/\| 阿尔法 \| 95 \|/);
 assert.match(result.translated,/figures\/p1.png/);
 assert.match(result.translated,/准确率/);
 assert.match(result.translated,/\$E=mc\^2\$/);
 assert.match(result.bilingual,/Hello, world/);
 assert.match(result.bilingual,/你好/);
 assert.equal(result.document.translated_pages[0].blocks[2].rows.length,2);
});

function envelope(units) {
 return {ok:true,status:200,json:async()=>({choices:[{finish_reason:'stop',message:{content:JSON.stringify(units)}}]})};
}
function unitsFrom(options) {return JSON.parse(JSON.parse(options.body).messages.at(-1).content);}
test('paragraph translation is coherent and retries only broken inline formula markers',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:[{id:'prose',type:'paragraph',text:'The wind velocity V remains constant.',
  translation_text:'The wind velocity [[M0]] remains constant.',inline_math:[{id:'M0',bbox:[30,30,38,42]}],
  layout_runs:[{text:'The wind velocity',bbox:[0,0,30,10]}]}]}]};
 const original=JSON.stringify(doc);let calls=0;
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,fetch:async(_url,options)=>{
  const units=unitsFrom(options);assert.equal(units[0].text,doc.pages[0].blocks[0].translation_text);
  return envelope(units.map(x=>({...x,text:++calls===1?'风速保持不变。':'风速 [[M0]] 保持不变。'})));
 }});
 assert.equal(calls,2);assert.equal(JSON.stringify(doc),original);
 assert.equal(result.document.translated_pages[0].blocks[0].translation_text,'风速 [[M0]] 保持不变。');
 assert.deepEqual(result.document.translated_pages[0].blocks[0].inline_math,doc.pages[0].blocks[0].inline_math);
});
const small={schema:1,pages:[{number:1,blocks:[{id:'a',type:'paragraph',text:'First'},{id:'b',type:'paragraph',text:'Second'}]}]};

test('reordered structured IDs are mapped to their own source cells without dropping content',async()=>{
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',fixture,{fetch:async(_url,options)=>
  envelope(unitsFrom(options).map(unit=>({...unit,text:'译文 '+unit.text})).reverse())});
 const blocks=result.document.translated_pages[0].blocks;
 assert.equal(blocks[0].text,'译文 Research');assert.equal(blocks[1].text,'译文 Hello, world.');
 assert.equal(blocks[2].rows[0][0],'译文 Model');assert.equal(blocks[2].rows[1][0],'译文 Alpha');
 assert.equal(blocks[2].rows[1][1],'95');assert.equal(blocks[4].text,'$E=mc^2$');
});

test('partial and blank structured outputs retry only missing IDs and publish ordered progress once',async()=>{
 const requests=[],progress=[],retries=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  onRetry:(n,total,reason)=>retries.push([n,total,reason]),onProgress:(n,total)=>progress.push([n,total]),
  fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units);
   if(requests.length===1)return envelope([{id:units[0].id,text:'第一段'},{id:units[1].id,text:' '}]);
   return envelope(units.map(unit=>({...unit,text:'第二段'})));
  }});
 assert.deepEqual(requests.map(units=>units.map(unit=>unit.id)),[['u0','u1'],['u1']]);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(b=>b.text),['第一段','第二段']);
 assert.deepEqual(progress,[[1,1]]);assert.equal(retries.length,1);assert.deepEqual(retries[0],[1,5,'结构化返回不完整']);
});

test('network interruptions and missing cells share the same five-retry budget',async()=>{
 const requests=[],retries=[];
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  onRetry:n=>retries.push(n),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units);
   if(requests.length===1)return envelope([{id:'u0',text:'第一段'}]);
   if(requests.length%2===0)throw new Error('network fixture');
   return envelope([]);
  }}),/网络|结构化/);
 assert.equal(requests.length,6);assert.deepEqual(retries,[1,2,3,4,5]);
 assert.ok(requests.slice(1).every(units=>units.length===1&&units[0].id==='u1'));
});

test('unknown and duplicate IDs do not contaminate accepted translations',async()=>{
 for(const invalid of [[{id:'u0',text:'不可信'},{id:'wrong',text:'不可信'}],
  [{id:'u0',text:'不可信'},{id:'u0',text:'重复'}]]){
  const requests=[];
  const result=await C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
   fetch:async(_url,options)=>{
    const units=unitsFrom(options);requests.push(units);
    return envelope(requests.length===1?invalid:units.map(unit=>({...unit,text:'正确 '+unit.text})));
   }});
  assert.equal(requests.length,2);assert.equal(requests[1].length,2);
  assert.deepEqual(result.document.translated_pages[0].blocks.map(b=>b.text),['正确 First','正确 Second']);
 }
});

test('fifth missing-cell retry can complete without retranslating already accepted paragraphs',async()=>{
 let calls=0;const progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  onProgress:(n,total)=>progress.push([n,total]),fetch:async(_url,options)=>{
   const units=unitsFrom(options);calls++;
   if(calls===1)return envelope([{id:'u0',text:'第一段'}]);
   return envelope(calls===6?units.map(unit=>({...unit,text:'第二段'})):[]);
  }});
 assert.equal(calls,6);assert.deepEqual(progress,[[1,1]]);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(b=>b.text),['第一段','第二段']);
});

test('cancelling missing-cell recovery stops subsequent requests and progress',async()=>{
 let calls=0;const progress=[],controller=new AbortController();
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  signal:controller.signal,onRetry:()=>controller.abort(),onProgress:n=>progress.push(n),
  fetch:async()=>{calls++;return envelope([{id:'u0',text:'第一段'}]);}}),/取消/);
 assert.equal(calls,1);assert.deepEqual(progress,[]);
});

test('invalid JSON structured content retries within the five-attempt budget and never fabricates cells',async()=>{
 let calls=0;
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  fetch:async(_url,options)=>{
   calls++;
   if(calls===1)return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'not-json'}}]})};
   return envelope(unitsFrom(options).map(unit=>({...unit,text:'译文 '+unit.text})));
  }});
 assert.equal(calls,2);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(block=>block.text),['译文 First','译文 Second']);
});

test('invalid JSON remains bounded after all five retries',async()=>{
 let calls=0;
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  fetch:async()=>{calls++;return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'not-json'}}]})};}}),/不是有效 JSON/);
 assert.equal(calls,6);
});

test('structured JSON preserves code fences inside translated text and explanatory wrappers',async()=>{
 const text='示例：\n```python\nprint("你好 [nested]")\n```\n代码不变。';
 const before=JSON.stringify(small);
 for(const wrap of [raw=>raw,raw=>'```json\n'+raw+'\n```',
  raw=>'说明：\n```text\n[not JSON]\n```\n翻译结果：\n'+raw+'\n完成。']){
  let calls=0;const retries=[];
  const result=await C.translateDocument(C.DEFAULT,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
   onRetry:n=>retries.push(n),fetch:async(_url,options)=>{
    calls++;const content=wrap(JSON.stringify(unitsFrom(options).map(unit=>({...unit,text}))));
    return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content}}]})};
   }});
  assert.equal(calls,1);assert.deepEqual(retries,[]);
  assert.deepEqual(result.document.translated_pages[0].blocks.map(block=>block.text),[text,text]);
  assert.equal(JSON.stringify(small),before);
 }
});

test('structured output accepts a fenced or explanatory JSON array without losing IDs',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:[{id:'a',type:'paragraph',text:'First'},{id:'b',type:'paragraph',text:'Second [bracket]'}]}]};
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{fetch:async(_url,options)=>{
   const units=unitsFrom(options);
   const payload=JSON.stringify(units.map(x=>({...x,text:'译文 '+x.text})));
   return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'我已整理结果：\n```json\n'+payload+'\n```\n以上。'}}]})};
 }});
 assert.deepEqual(result.document.translated_pages[0].blocks.map(x=>x.text),['译文 First','译文 Second [bracket]']);
});

test('structured output ignores a UTF-8 BOM and recovers an array after model commentary',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:[{id:'a',type:'paragraph',text:'First'}]}]};
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{fetch:async(_url,options)=>{
   const units=unitsFrom(options);
   const payload=JSON.stringify(units.map(x=>({...x,text:'第一段'})));
   return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'\uFEFF<think>不要输出思考</think>\n'+payload+'\n'}}]})};
 }});
 assert.equal(result.document.translated_pages[0].blocks[0].text,'第一段');
});

test('Anthropic partial recovery uses messages protocol and only the missing unit',async()=>{
 const requests=[];const config={...C.DEFAULT,protocol:'anthropic'};
 const result=await C.translateDocument(config,'fixture-key',small,{retryAttempts:5,retryDelayMs:1,
  fetch:async(url,options)=>{
   const body=JSON.parse(options.body),units=unitsFrom(options);requests.push(units);
   assert.match(url,/\/v1\/messages$/);assert.match(body.system,/unchanged input id/);
   assert.equal(body.messages[0].role,'user');assert.equal(options.headers['x-api-key'],'fixture-key');
   const content=requests.length===1?[{id:'u0',text:'第一段'}]:units.map(x=>({...x,text:'第二段'}));
   return {ok:true,json:async()=>({stop_reason:'end_turn',content:[{type:'text',text:JSON.stringify(content)}]})};
  }});
 assert.deepEqual(requests.map(units=>units.map(x=>x.id)),[['u0','u1'],['u1']]);
 assert.deepEqual(result.document.translated_pages[0].blocks.map(b=>b.text),['第一段','第二段']);
});

test('a missing item in the final batch does not restart previous batches or duplicate progress',async()=>{
 const doc={schema:1,pages:[{number:1,blocks:Array.from({length:90},(_,i)=>({id:'b'+i,type:'paragraph',text:'Paragraph '+i}))}]};
 const requests=[],progress=[];
 const result=await C.translateDocument(C.DEFAULT,'fixture-key',doc,{retryAttempts:5,retryDelayMs:1,
  onProgress:(n,total)=>progress.push([n,total]),fetch:async(_url,options)=>{
   const units=unitsFrom(options);requests.push(units);
   return envelope((requests.length===3?units.slice(0,-1):units).map(x=>({...x,text:'译文 '+x.text})));
  }});
 assert.deepEqual(requests.map(units=>units.length),[40,40,10,1]);assert.equal(requests[3][0].id,'u89');
 assert.deepEqual(progress,[[1,3],[2,3],[3,3]]);
 assert.ok(result.document.translated_pages[0].blocks.every(b=>b.text==='译文 Paragraph '+b.id.slice(1)));
});
test('malformed or reordered structured results cannot drop source cells',async()=>{
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',fixture,{fetch:async()=>({ok:true,json:async()=>({choices:[{message:{content:'[]'}}]})})}),/结构化/);
 const controller=new AbortController();controller.abort();
 await assert.rejects(C.translateDocument(C.DEFAULT,'fixture-key',fixture,{signal:controller.signal,fetch:respond}),/取消/);
});
