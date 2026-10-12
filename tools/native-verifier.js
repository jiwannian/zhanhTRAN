function startup(){void verify();}
function shutdown(){}
function install(){}
function uninstall(){}
async function verify(){
 const {setTimeout}=ChromeUtils.importESModule('resource://gre/modules/Timer.sys.mjs');
 const {AddonManager}=ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
 const pause=ms=>new Promise(r=>setTimeout(r,ms));
 const profile=Services.dirsvc.get('ProfD',Ci.nsIFile).path;
 const reportPath=PathUtils.join(profile,'native-result.json');
 const report={result:'RUNNING',version:Zotero.version,checks:[],alerts:[],confirmations:[]};
 const write=()=>IOUtils.writeJSON(reportPath,report);
 const check=(value,label)=>{if(!value)throw new Error(label);};
 async function captureSettings(target,label,atBottom=false){
  if(atBottom)target.document.querySelector('.zt-test').scrollIntoView({block:'end'});
  await pause(350);
  const doc=target.document,root=doc.getElementById('zhanhtran-custom-provider-root');
  const bottom=doc.querySelector('.zt-test').getBoundingClientRect();
  if(atBottom)check(bottom.top>=0&&bottom.bottom<=target.innerHeight+1,'native settings bottom cannot scroll into view: '+label);
  const inputs=[...root.querySelectorAll('input:not([type=radio]):not([type=checkbox]),select')];
  const boxes=inputs.map(el=>{const r=el.getBoundingClientRect();return {id:el.id,x:r.x,y:r.y,width:r.width,height:r.height};});
  check(boxes.every(r=>r.width>0&&r.height>=34&&r.x>=0&&r.x+r.width<=target.innerWidth+1),'native settings controls overflow: '+label);
  const icon=root.querySelector('.zt-brand img');check(icon.complete&&icon.naturalWidth===48,'native settings icon not loaded');
  check(root.querySelectorAll('#zhanhtran-output-order input[type=radio]').length===2,'native order radios missing');
  const canvas=doc.createElementNS('http://www.w3.org/1999/xhtml','canvas');
  canvas.width=target.innerWidth;canvas.height=target.innerHeight;
  const rect=new target.DOMRect(target.scrollX,target.scrollY,canvas.width,canvas.height);
  const snapshot=await target.docShell.browsingContext.currentWindowGlobal.drawSnapshot(rect,1,'rgb(255,255,255)');
  canvas.getContext('2d').drawImage(snapshot,0,0);snapshot.close();
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
  const path=PathUtils.join(profile,label+'.png');await IOUtils.write(path,new Uint8Array(await blob.arrayBuffer()));
  report.settingsVisuals ||= [];report.settingsVisuals.push({label,path,viewport:[target.innerWidth,target.innerHeight],controls:boxes,
   background:target.getComputedStyle(root.querySelector('.zt-settings')).backgroundColor,iconLoaded:true,bottomVisible:bottom.top>=0&&bottom.bottom<=target.innerHeight+1});
 }
 async function installXPI(path){
  const file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);file.initWithPath(path);
  const install=await AddonManager.getInstallForFile(file);check(!install.error,'install rejected');await install.install();
 }
 async function verifyProtocol(target,api,host){
  const doc=target.document,root=doc.getElementById('zhanhtran-custom-provider-root');
  for(const protocol of ['anthropic','openai']){
   const group=doc.getElementById('zhanhtran-custom-protocol');
   check(group.getAttribute('role')==='radiogroup'&&!group.querySelector('option'),'protocol still uses a popup: '+host);
   const radio=group.querySelector('input[value="'+protocol+'"]');radio.click();
   check(radio.checked,'protocol click failed: '+host);
   root.querySelector('.zt-primary').click();
   for(let n=0;n<100&&api.selected().protocol!==protocol;n++)await pause(50);
   check(api.selected().protocol===protocol,'protocol save failed: '+host);
   root.replaceChildren();delete root.dataset.mounted;api.mount(target,root);
   check(root.querySelector('#zhanhtran-custom-protocol input[value="'+protocol+'"]').checked,'protocol readback failed: '+host);
   const buttons=[...root.querySelectorAll('button')];buttons.find(b=>b.textContent==='联通测试').click();
   for(let n=0;n<100&&!root.querySelector('.zt-status').textContent.startsWith('成功');n++)await pause(50);
   check(root.querySelector('.zt-status').textContent.startsWith('成功'),'protocol request failed: '+host);
   const labels=[...root.querySelectorAll('.zt-protocol label')];
   check(labels.every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.x>=0&&r.right<=target.innerWidth+1&&el.scrollWidth<=el.clientWidth;}),'protocol labels overflow: '+host);
   report.protocolSelections ||= [];report.protocolSelections.push({host,protocol,clicked:true,saved:true,readback:true,requestPassed:true,labelsFit:true});
  }
 }
 let win,originalAlert,originalConfirm,originalDialog,promptObserver;
 try{
  await Zotero.initializationPromise;const cfg=await IOUtils.readJSON(PathUtils.join(profile,'native-config.json'));
  const provider={id:'qa-local',name:'QA local',baseURL:cfg.provider,model:'fixture-model',protocol:'openai',language:'简体中文'};
  Zotero.Prefs.set('extensions.zotero.zhanhtran.customProvider',JSON.stringify({selected:provider.id,providers:[provider]}),true);
  Zotero.Prefs.set('extensions.zotero.zhanhtran.localPython',cfg.python,true);
  const Login=Components.Constructor('@mozilla.org/login-manager/loginInfo;1','nsILoginInfo','init');
  await Services.logins.addLoginAsync(new Login('chrome://zhanhtran',null,provider.id+'|'+provider.baseURL,provider.id,'qa-fixture-only','',''));
  report.stage='independent-install';await write();await installXPI(cfg.xpi);
  for(let n=0;n<120&&!Zotero.zhanhTRAN;n++)await pause(250);
  const api=Zotero.zhanhTRAN;check(api,'independent entry missing');
  for(let n=0;n<120&&!Zotero.getMainWindow();n++)await pause(250);
  win=Zotero.getMainWindow();check(win,'main window missing');
  for(let n=0;n<120&&(!win.ZoteroPane?.itemsView||!win.ZoteroPane?.collectionsView?.itemTreeView);n++)await pause(250);
  check(win.ZoteroPane?.itemsView&&win.ZoteroPane?.collectionsView?.itemTreeView,'main library trees not ready');
  await win.ZoteroPane.itemsView.waitForLoad();
  const addon=await AddonManager.getAddonByID('zhanhtran@zhanh.local');
  report.configuration={model:api.selected().model,output:api.outputSettings(),keyReadback:(await api.keyFor(provider))==='qa-fixture-only'};
  check(report.configuration.keyReadback&&report.configuration.output.order==='original-left','independent configuration failed');
  report.backgroundUpdatesDisabled=addon.applyBackgroundUpdates===AddonManager.AUTOUPDATE_DISABLE;
  check(report.backgroundUpdatesDisabled,'background updates enabled');
  originalAlert=Services.prompt.alert;originalConfirm=Services.prompt.confirm;originalDialog=win.openDialog;
  Services.prompt.alert=(_w,title,message)=>report.alerts.push({title,message});
  Services.prompt.confirm=(_w,title,message)=>{report.confirmations.push({title,message});return false;};
  // Prompt service wrappers can differ between plugin globals; observe the actual host dialog.
  promptObserver={observe(dialog,topic){
   if(topic!=='domwindowopened')return;
   dialog.addEventListener('load',()=>{void (async()=>{
    if(!dialog.document.documentURI.includes('commonDialog.xhtml'))return;
    await pause(100);
    const prompt=dialog.document.querySelector('dialog');
    report.alerts.push({title:dialog.document.title,message:dialog.document.getElementById('infoBody')?.textContent||'',native:true});
    prompt?.acceptDialog();
   })();},{once:true});
  }};
  Services.ww.registerNotification(promptObserver);
  const messages=[];
  win.openDialog=function(...args){const dialog=originalDialog.apply(this,args);if(args[0].includes('translation-progress.xhtml'))messages.push({dialog,progress:args[3].progress,features:args[2]});return dialog;};
  const beforeNotes=await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemNotes');
  const cases=[['digital.pdf',true,true,'original-left'],['scan.pdf',true,false,'original-left'],
   ['searchable.pdf',false,true,'translated-left'],['rotated.pdf',true,true,'translated-left'],
   ['figure.png',true,true,'original-left'],['figure.png',false,true,'translated-left'],['figure.png',true,false,'original-left'],
   ['fit.pdf',true,true,'original-left'],['fit.pdf',true,false,'original-left'],
   ['fit.pdf',false,true,'translated-left'],['fit-rotated.pdf',true,true,'translated-left']];
  for(const [name,translated,bilingual,order] of cases){
   report.stage=name+':'+translated+':'+bilingual+':'+order;await write();
   api.saveOutput({translated,bilingual,order});
   const parent=new Zotero.Item('journalArticle');parent.libraryID=Zotero.Libraries.userLibraryID;parent.setField('title','zhanhTRAN synthetic '+report.stage);await parent.saveTx();
   const input=await Zotero.Attachments.importFromFile({file:PathUtils.join(cfg.fixtures,name),parentItemID:parent.id});
   const sourcePath=await input.getFilePathAsync(),beforeBytes=await IOUtils.read(sourcePath);
   await win.ZoteroPane.selectItem(input.id);
   const before=await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemAttachments');
   const index=messages.length,task=api.translateSelected(win);
   for(let n=0;n<120&&messages.length===index;n++)await pause(50);
   check(messages.length>index,'progress message missing');const message=messages[index];
   for(let n=0;n<120&&!message.dialog.document?.getElementById('translation-action');n++)await pause(50);
   message.dialog.moveTo(70,90);message.dialog.resizeTo(550,260);await pause(100);
   const position=[message.dialog.screenX,message.dialog.screenY,message.dialog.outerWidth,message.dialog.outerHeight];
   await task;await pause(100);
   const after=await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemAttachments');
   const outputIDs=after.filter(id=>!before.includes(id));
   check(outputIDs.length===Number(translated)+Number(bilingual),'output count mismatch');
   const outputs=[];
   for(const id of outputIDs){
    const item=Zotero.Items.get(id),path=await item.getFilePathAsync();
    const observation={path,parentID:item.parentID,libraryID:item.libraryID,linkMode:item.attachmentLinkMode,title:item.getField('title'),bytes:(await IOUtils.stat(path)).size};
    check(item.parentID===parent.id&&item.libraryID===parent.libraryID,'parent/library mismatch');
    check(PathUtils.parent(path)===PathUtils.parent(sourcePath),'output directory mismatch');
    check(path.split('.').pop()===sourcePath.split('.').pop(),'output type mismatch');outputs.push(observation);
   }
   const afterBytes=await IOUtils.read(sourcePath);
   check(beforeBytes.length===afterBytes.length&&beforeBytes.every((b,i)=>b===afterBytes[i]),'source changed');
   const moved=[message.dialog.screenX,message.dialog.screenY,message.dialog.outerWidth,message.dialog.outerHeight];
   check(position.every((v,i)=>v===moved[i]),'message position not retained');
   const status=message.dialog.document.getElementById('translation-status').textContent;
   check(message.dialog.document.getElementById('translation-percent').textContent==='100%','incomplete progress');
   report.checks.push({name,translated,bilingual,order,sourcePath,sourceUnchanged:true,outputs,status,movable:true});
   message.dialog.document.getElementById('translation-action').click();await write();
  }
  report.notesAdded=(await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemNotes')).filter(id=>!beforeNotes.includes(id));
  check(report.notesAdded.length===0,'notes created');check(report.alerts.length===0&&report.confirmations.length===0,'unexpected prompt');
  for(const model of ['fixture-retry','fixture-cancel','fixture-timeout-retry','fixture-timeout-shrink',
    'fixture-timeout-exhaust','fixture-timeout-cancel']){
   report.stage=model;await write();
   await api.save({...provider,model});api.saveOutput({translated:true,bilingual:model==='fixture-timeout-shrink',order:'original-left'});
   const file=model==='fixture-timeout-retry'?'timeout-single.pdf':model.startsWith('fixture-timeout-')?'timeout.pdf':'digital.pdf';
   const input=await Zotero.Attachments.importFromFile({file:PathUtils.join(cfg.fixtures,file),libraryID:Zotero.Libraries.userLibraryID});
   const sourcePath=await input.getFilePathAsync(),sourceBytes=await IOUtils.read(sourcePath);
   await win.ZoteroPane.selectItem(input.id);
   const before=await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemAttachments'),index=messages.length,alertIndex=report.alerts.length;
   const task=api.translateSelected(win);
   for(let n=0;n<120&&messages.length===index;n++)await pause(50);
   check(messages.length>index,'reliability message missing');const message=messages[index];
   for(let n=0;n<120&&!message.dialog.document?.getElementById('translation-action');n++)await pause(50);
   if(model.endsWith('-cancel')){
    for(let n=0;n<240;n++){
     const counts=await (await win.fetch(cfg.provider+'/qa-counts')).json();
     if(counts[model]===1)break;
     await pause(100);
    }
    const counts=await (await win.fetch(cfg.provider+'/qa-counts')).json();
    check(counts[model]===1,'cancellation did not reach the pending request');
    message.dialog.document.getElementById('translation-action').click();
   }
   await task;await pause(250);
   const counts=await (await win.fetch(cfg.provider+'/qa-counts')).json();
   const after=await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemAttachments');
   const outputIDs=after.filter(id=>!before.includes(id));
   const status=message.dialog.document.getElementById('translation-status').textContent;
   const restored=await IOUtils.read(sourcePath);
   check(sourceBytes.length===restored.length&&sourceBytes.every((b,i)=>b===restored[i]),'reliability source changed');
   let expectedAlert;
   if(model==='fixture-retry'||model==='fixture-timeout-retry'){
    check(counts[model]===6&&outputIDs.length===1,'fifth retry did not recover exactly once');
    check(message.dialog.document.getElementById('translation-percent').textContent==='100%','retry job incomplete');
   }else if(model==='fixture-timeout-shrink'){
    check(counts[model]===3&&outputIDs.length===2,'timeout shrink did not export both files');
    check(message.dialog.document.getElementById('translation-percent').textContent==='100%','shrunk job incomplete');
   }else if(model==='fixture-timeout-exhaust'){
    check(counts[model]===6&&outputIDs.length===0&&/524.*超时/.test(status),'persistent timeout exceeded budget or exported');
    check(report.alerts.length===alertIndex+1&&/524.*超时/.test(report.alerts[alertIndex].message)&&
      !/密钥|配额|qa-fixture-only/.test(report.alerts[alertIndex].message),'unsafe or incorrect timeout diagnostic');
    expectedAlert=report.alerts.splice(alertIndex,1)[0];
   }else check(counts[model]===1&&outputIDs.length===0&&status==='翻译已取消','cancelled job published output or retried');
   check(report.alerts.length===alertIndex,'unexpected reliability alert');
   const outputs=[];
   for(const id of outputIDs){
    const item=Zotero.Items.get(id),path=await item.getFilePathAsync();
    check(item.libraryID===input.libraryID&&item.parentID===input.parentID,'reliability output parent/library mismatch');
    check(PathUtils.parent(path)===PathUtils.parent(sourcePath)&&path.endsWith('.pdf'),'reliability output location/type mismatch');
    outputs.push({path,libraryID:item.libraryID,parentID:item.parentID});
   }
   report.reliability ||= [];
   report.reliability.push({model,requests:counts[model],outputCount:outputIDs.length,status,sourcePath,outputs,
    sourceUnchanged:true,...(expectedAlert?{expectedAlert}:{})});
   message.dialog.document.getElementById('translation-action').click();await write();
  }
  await api.save(provider);
  report.notesAdded=(await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemNotes')).filter(id=>!beforeNotes.includes(id));
  check(report.notesAdded.length===0&&report.alerts.length===0&&report.confirmations.length===0,'reliability created a note or prompt');
  const settings=api.openSettings(win);for(let n=0;n<100&&!settings.document?.getElementById('zhanhtran-output-order');n++)await pause(100);
  check(settings.document.getElementById('zhanhtran-output-order'),'settings failed to mount');
  check(settings.document.title==='zhanhTRAN','settings brand mismatch');report.settingsMounted=true;
  await verifyProtocol(settings,api,'standalone');
  await captureSettings(settings,'settings-native-desktop');await captureSettings(settings,'settings-native-desktop-bottom',true);
  settings.resizeTo(420,900);await captureSettings(settings,'settings-native-narrow');await captureSettings(settings,'settings-native-narrow-bottom',true);settings.close();
  const pane=Zotero.PreferencePanes.pluginPanes?.find?.(p=>p.id==='zhanhtran-settings');
  report.preferencePane=pane?{label:pane.rawLabel,src:pane.src,image:pane.image}:null;
  check(pane?.rawLabel==='zhanhTRAN','preference sidebar brand mismatch');
  const preferences=win.openDialog('chrome://zotero/content/preferences/preferences.xhtml','zhanhtran-qa-preferences',
   'chrome,titlebar,toolbar,centerscreen,resizable',{pane:'zhanhtran-settings'});
  for(let n=0;n<150&&!preferences.document?.getElementById('zhanhtran-output-order');n++)await pause(100);
  report.preferenceDiagnostics={selected:preferences.Zotero_Preferences?.navigation?.value,
   rootFound:!!preferences.document?.getElementById('zhanhtran-custom-provider-root'),
   paneLoaded:preferences.Zotero_Preferences?.panes.get('zhanhtran-settings')?.loaded};
  check(preferences.document.getElementById('zhanhtran-output-order'),'native preference fragment failed to mount');
  const navigation=[...preferences.document.getElementById('prefs-navigation').children].find(n=>n.value==='zhanhtran-settings');
  check(navigation?.querySelector('label')?.value==='zhanhTRAN','native sidebar label mismatch');
  report.preferencePane.mounted=true;report.preferencePane.sidebarLabel=navigation.querySelector('label').value;
  await verifyProtocol(preferences,api,'preferences');
  await captureSettings(preferences,'settings-native-preferences');
  await captureSettings(preferences,'settings-native-preferences-bottom',true);
  preferences.close();
  report.stage='restart-idempotency';await write();
  await addon.disable();await addon.enable();for(let n=0;n<120&&!Zotero.zhanhTRAN;n++)await pause(250);
  check(Zotero.zhanhTRAN,'restart failed');
  report.idempotentKeyCount=(await Services.logins.findLogins('chrome://zhanhtran',null,provider.id+'|'+provider.baseURL)).length;
  check(report.idempotentKeyCount===1,'restart duplicated credentials');
  report.result='PASS';
 }catch(error){report.result='FAIL';report.error=String(error);report.stack=error.stack;}
 finally{
  if(promptObserver)Services.ww.unregisterNotification(promptObserver);
  if(originalAlert)Services.prompt.alert=originalAlert;if(originalConfirm)Services.prompt.confirm=originalConfirm;if(originalDialog)win.openDialog=originalDialog;
  await write();
 }
}
