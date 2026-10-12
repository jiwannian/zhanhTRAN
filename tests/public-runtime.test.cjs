'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..');

test('browser global SSE is connected to the response decoder',async()=>{
 const context={TextEncoder,TextDecoder};vm.createContext(context);
 for(const name of ['vendor/eventsource-parser.js','provider-response.js'])
  vm.runInContext(fs.readFileSync(path.join(root,'src',name),'utf8'),context);
 assert.equal(typeof context.ZhanhTRANSSE?.createParser,'function');
 const response={headers:{get:()=> 'text/event-stream'},text:async()=>
  'data: {"choices":[{"delta":{"content":"Translated"},"finish_reason":null}]}\n\n'+
  'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'};
 const result=await context.ZhanhTRANProviderResponse.read(response,'openai');
 assert.equal(result.choices[0].message.content,'Translated');
});

test('public defaults contain no machine-specific runtime or preselected commercial provider',()=>{
 const core=require('../src/custom-provider-core.js');
 assert.equal(core.DEFAULT.baseURL,'https://provider.invalid');
 assert.equal(core.DEFAULT.model,'your-model');
 for(const name of ['bootstrap.js','src/custom-provider-zotero.js'])
  assert.doesNotMatch(fs.readFileSync(path.join(root,name),'utf8'),/[A-Z]:\\\\(?:tool|Users)/);
});

test('output module only validates the current namespace, without migration side effects',()=>{
 const output=require('../src/output-settings.js');
 assert.deepEqual(output.outputOptions(),{translated:true,bilingual:true,order:'original-left'});
 assert.throws(()=>output.outputOptions({translated:false,bilingual:false,order:'original-left'}));
 assert.throws(()=>output.outputOptions('{'));
 assert.throws(()=>output.outputOptions({translated:true,bilingual:true,order:'invalid'}));
 for(const translated of [true,false])for(const bilingual of [true,false]){
  if(!translated&&!bilingual)continue;
  for(const order of ['original-left','translated-left']){
   const value={translated,bilingual,order};
   assert.deepEqual(output.outputOptions(JSON.stringify(value)),value);
   assert.equal(Object.isFrozen(output.outputOptions(value)),true);
  }
 }
 assert.deepEqual(Object.keys(output).sort(),['DEFAULT_OUTPUT','outputOptions']);
});
