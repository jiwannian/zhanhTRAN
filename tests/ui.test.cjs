'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
fs.mkdirSync(path.join(__dirname,'../logs'),{recursive:true});

test('settings presentation: grouped fields, responsive geometry, dark mode and keyboard order',async()=>{
 const browser=await chromium.launch({...(process.env.ZHANHTRAN_CHROME?{executablePath:process.env.ZHANHTRAN_CHROME}:{}),headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1000,height:900}});
  await page.goto(pathToFileURL(path.join(__dirname,'ui-fixture.html')).href);
  await page.getByRole('heading',{name:'zhanhTRAN',exact:true}).waitFor();
  assert.equal(await page.locator('.zt-provider-grid').count(),1,'provider fields need a distinct responsive group');
  assert.equal(await page.getByRole('group',{name:'排版输出',exact:true}).count(),1);
  assert.equal(await page.locator('.zt-brand img').evaluate(img=>img.complete&&img.naturalWidth>0),true);
  const name=await page.locator('#zhanhtran-custom-name').boundingBox(),model=await page.locator('#zhanhtran-custom-model').boundingBox();
  assert.ok(Math.abs(name.y-model.y)<1&&model.x>name.x,'wide windows use two columns');
  assert.ok(name.height>=34&&name.height<=42,'compact but usable inputs');
  const reverse=page.getByRole('radio',{name:'译文左 / 原文右',exact:true});
  await reverse.check();
  assert.equal(await page.evaluate(()=>api.outputSettings().order),'translated-left');
  await reverse.focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await page.evaluate(()=>api.outputSettings().order),'original-left');
  await page.getByText('文本翻译测试',{exact:true}).click();
  await page.locator('#zhanhtran-custom-text').fill('Hello, world.');
  await page.locator('#zhanhtran-custom-key').fill('qa-fixture-only');
  await page.evaluate(()=>fixture.hold=true);await page.getByRole('button',{name:'翻译文本',exact:true}).click();
  await page.waitForFunction(()=>fixture.release);
  assert.equal(await page.getByRole('button',{name:'联通测试',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'翻译文本',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'取消',exact:true}).click();await page.evaluate(()=>{fixture.hold=false;fixture.release();});
  await page.getByText('翻译已取消',{exact:true}).waitFor();
  assert.equal(await page.locator('.zt-status[data-tone=error]').count(),2);
  assert.equal(await page.getByRole('button',{name:'翻译文本',exact:true}).isEnabled(),true);
  await page.getByRole('button',{name:'翻译文本',exact:true}).click();
  await page.getByText('成功 · your-model · 1 块',{exact:true}).waitFor();
  assert.equal(await page.locator('#zhanhtran-custom-result').inputValue(),'你好，世界。');
  await page.locator('#zhanhtran-custom-key').fill('');
  await page.screenshot({path:path.join(__dirname,'../logs/settings-desktop.png'),fullPage:true});
  for(const width of [760,420,360]){
   await page.setViewportSize({width,height:900});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`no horizontal overflow at ${width}`);
   assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#zhanhtran-custom-provider-root input,#zhanhtran-custom-provider-root button,#zhanhtran-custom-provider-root textarea')].every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.x>=0&&r.right<=innerWidth;})),true);
   if(width===420){
    const a=await page.locator('#zhanhtran-custom-name').boundingBox(),b=await page.locator('#zhanhtran-custom-model').boundingBox();
    assert.ok(b.y>a.y&&Math.abs(a.x-b.x)<1,'narrow windows use one column');
    await page.screenshot({path:path.join(__dirname,'../logs/settings-narrow.png'),fullPage:true});
   }
  }
  await page.setViewportSize({width:1000,height:900});
  await page.locator('#zhanhtran-custom-provider-root').evaluate(el=>el.style.width='420px');
  const narrowName=await page.locator('#zhanhtran-custom-name').boundingBox(),narrowModel=await page.locator('#zhanhtran-custom-model').boundingBox();
  assert.ok(narrowModel.y>narrowName.y,'a narrow Zotero pane also stacks within a wide host window');
  await page.locator('#zhanhtran-custom-provider-root').evaluate(el=>el.style.width='');
  await page.setViewportSize({width:760,height:900});await page.emulateMedia({colorScheme:'dark'});
  const colors=await page.locator('.zt-settings').evaluate(el=>({background:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color}));
  assert.notEqual(colors.background,'rgb(255, 255, 255)');assert.notEqual(colors.background,'rgba(0, 0, 0, 0)');
  await page.screenshot({path:path.join(__dirname,'../logs/settings-dark.png'),fullPage:true});
  await page.getByRole('button',{name:'新增',exact:true}).click();
  assert.equal(await page.locator('#zhanhtran-custom-name').inputValue(),'自定义供应商');
  await page.getByRole('button',{name:'保存并设为当前',exact:true}).click();
  assert.equal(await page.evaluate(()=>api.configState().providers.length),2);
  await page.getByRole('button',{name:'删除',exact:true}).click();
  assert.equal(await page.evaluate(()=>api.configState().providers.length),1);
  await page.getByRole('radio',{name:'Anthropic 原生',exact:true}).check();
  await page.locator('#zhanhtran-local-python').fill('D:/fixture/python.exe');await page.locator('#zhanhtran-local-python').blur();
  assert.equal(await page.evaluate(()=>fixture.prefs['extensions.zotero.zhanhtran.localPython']),'D:/fixture/python.exe');
 }finally{await browser.close();}
});

test('protocol selector: native radios, keyboard switching, save/readback and correct request route',async()=>{
 const browser=await chromium.launch({...(process.env.ZHANHTRAN_CHROME?{executablePath:process.env.ZHANHTRAN_CHROME}:{}),headless:true});
 try{
  const page=await browser.newPage({viewport:{width:760,height:900}});
  await page.goto(pathToFileURL(path.join(__dirname,'ui-fixture.html')).href);
  const group=page.getByRole('radiogroup',{name:'接口协议',exact:true});
  assert.equal(await group.count(),1,'protocol must not rely on an HTML select popup in the XUL host');
  const openai=group.getByRole('radio',{name:'OpenAI 兼容',exact:true});
  const anthropic=group.getByRole('radio',{name:'Anthropic 原生',exact:true});
  assert.equal(await openai.isChecked(),true);
  await anthropic.check();await anthropic.focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await openai.isChecked(),true);
  await page.keyboard.press('ArrowRight');assert.equal(await anthropic.isChecked(),true);
  await page.locator('#zhanhtran-custom-key').fill('qa-fixture-only');
  await page.getByRole('button',{name:'保存并设为当前',exact:true}).click();
  await page.getByText('已保存（密钥不会显示）',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>api.selected().protocol),'anthropic');
  assert.equal(await anthropic.isChecked(),true);
  await page.getByRole('button',{name:'联通测试',exact:true}).click();
  await page.getByText('成功 · your-model · 1 块',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixture.calls.at(-1).url.endsWith('/v1/messages')),true);
  assert.equal(await page.evaluate(()=>fixture.calls.at(-1).headers['x-api-key']),'qa-fixture-only');
  await page.evaluate(()=>{const root=document.getElementById('zhanhtran-custom-provider-root');root.replaceChildren();delete root.dataset.mounted;api.mount(window);});
  assert.equal(await anthropic.isChecked(),true);
  await openai.check();await page.locator('#zhanhtran-custom-key').fill('qa-fixture-only');
  await page.getByRole('button',{name:'保存并设为当前',exact:true}).click();
  await page.getByText('已保存（密钥不会显示）',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>api.selected().protocol),'openai');
  await page.getByRole('button',{name:'联通测试',exact:true}).click();
  await page.getByText('成功 · your-model · 1 块',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixture.calls.at(-1).url.endsWith('/v1/chat/completions')),true);
  assert.equal(await page.evaluate(()=>JSON.stringify(fixture.prefs).includes('qa-fixture-only')),false);
  for(const width of [1000,760,420,360]){
   await page.setViewportSize({width,height:900});
   assert.equal(await group.evaluate(el=>[...el.querySelectorAll('label')].every(label=>{
    const r=label.getBoundingClientRect();return r.width>0&&r.x>=0&&r.right<=innerWidth&&label.scrollWidth<=label.clientWidth;
   })),true,`protocol labels must fit at ${width}`);
  }
 }finally{await browser.close();}
});
test('settings DOM E2E: credentials, output controls and immediate translation',async()=>{
 const browser=await chromium.launch({...(process.env.ZHANHTRAN_CHROME?{executablePath:process.env.ZHANHTRAN_CHROME}:{}),headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1000,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.coverage.startJSCoverage({resetOnNavigation:false});
  await page.goto(pathToFileURL(path.join(__dirname,'ui-fixture.html')).href);
  await page.getByRole('heading',{name:'zhanhTRAN',exact:true}).waitFor();
  assert.equal(await page.locator('#zhanhtran-output-translated').isChecked(),true);
  await page.locator('#zhanhtran-custom-key').fill('qa-fixture-only');
  await page.getByRole('button',{name:'保存并设为当前',exact:true}).click();
  await page.getByText('已保存（密钥不会显示）',{exact:true}).waitFor();
  assert.equal(await page.locator('#zhanhtran-custom-key').inputValue(),'');
  assert.equal(await page.evaluate(()=>JSON.stringify(fixture.prefs).includes('qa-fixture-only')),false);
  await page.getByRole('button',{name:'联通测试',exact:true}).click();
  await page.getByText('成功 · your-model · 1 块',{exact:true}).waitFor();
  for(const [translated,bilingual,order] of [[true,false,'original-left'],[false,true,'translated-left'],[true,true,'original-left']]){
   if(translated)await page.locator('#zhanhtran-output-translated').check();
   if(bilingual)await page.locator('#zhanhtran-output-bilingual').check();
   if(!translated)await page.locator('#zhanhtran-output-translated').uncheck();
   if(!bilingual)await page.locator('#zhanhtran-output-bilingual').uncheck();
   await page.getByRole('radio',{name:order==='original-left'?'原文左 / 译文右':'译文左 / 原文右',exact:true}).check();
   const before=await page.evaluate(()=>fixture.pdfs.length);
   await page.evaluate(()=>{
    fixture.items=[{isNote:()=>false,isRegularItem:()=>false,isAttachment:()=>true,attachmentContentType:'application/pdf',getFilePathAsync:async()=>'/source.pdf',getField:()=> 'Fixture',libraryID:1,parentID:7}];
    return api.translateSelected(window);
   });
   assert.equal((await page.evaluate(()=>fixture.pdfs.length))-before,Number(translated)+Number(bilingual));
  }
  await page.locator('#zhanhtran-output-translated').uncheck();await page.locator('#zhanhtran-output-bilingual').click();
  assert.equal(await page.locator('#zhanhtran-output-bilingual').isChecked(),true);
  await page.getByText('请至少选择译文文件或双语文件',{exact:true}).waitFor();
  await page.setViewportSize({width:420,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  assert.deepEqual(await page.evaluate(()=>fixture.notes),[]);assert.deepEqual(await page.evaluate(()=>fixture.confirmations),[]);
  assert.deepEqual(errors,[]);
  const coverage=await page.coverage.stopJSCoverage();
  fs.writeFileSync(path.join(__dirname,'../logs/browser-coverage.json'),JSON.stringify(coverage));
  assert.ok(coverage.find(x=>x.url.endsWith('/src/custom-provider-zotero.js')));
 }finally{await browser.close();}
});
