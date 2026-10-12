let api,chromeHandle,sandbox;
function install(){}
function uninstall(){}
async function startup({id,resourceURI,rootURI}){
 await Zotero.initializationPromise;
 rootURI=(rootURI||resourceURI.spec).replace(/\/?$/,'/');
 const {AddonManager}=ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
 const addon=await AddonManager.getAddonByID(id);
 addon.applyBackgroundUpdates=AddonManager.AUTOUPDATE_DISABLE;
 chromeHandle=Cc['@mozilla.org/addons/addon-manager-startup;1'].getService(Ci.amIAddonManagerStartup)
  .registerChrome(Services.io.newURI(rootURI+'manifest.json'),[['content','zhanhtran',rootURI+'chrome/content/']]);
 sandbox=new Cu.Sandbox(Services.scriptSecurityManager.getSystemPrincipal(),{
  sandboxName:'zhanhTRAN',wantGlobalProperties:['fetch','AbortController','TextEncoder','TextDecoder','URL','IOUtils','Blob']});
 const ctx={};ctx._globalThis=ctx;
 for(const name of ['AbortController','TextEncoder','TextDecoder','URL','IOUtils','Blob'])ctx[name]=sandbox[name];
 ctx.fetch=sandbox.fetch.bind(sandbox);
 const timers=ChromeUtils.importESModule('resource://gre/modules/Timer.sys.mjs');
 ctx.setTimeout=timers.setTimeout;ctx.clearTimeout=timers.clearTimeout;
 const {Subprocess}=ChromeUtils.importESModule('resource://gre/modules/Subprocess.sys.mjs');
 const {NetUtil}=ChromeUtils.importESModule('resource://gre/modules/NetUtil.sys.mjs');
 const profileDir=Services.dirsvc.get('ProfD',Ci.nsIFile).path;
 const read=name=>new Promise((resolve,reject)=>NetUtil.asyncFetch({uri:rootURI+'chrome/content/scripts/'+name,loadUsingSystemPrincipal:true},(stream,status)=>{
  try{if(!Components.isSuccessCode(status))throw new Error('本地脚本读取失败');resolve(NetUtil.readInputStreamToString(stream,stream.available(),{charset:'UTF-8'}));}
  catch(error){reject(error);}finally{stream?.close();}
 }));
 try{
  for(const name of ['vendor/eventsource-parser.js','provider-response.js','custom-provider-core.js','output-settings.js','translation-progress.js','custom-provider-zotero.js','local-parser-zotero.js'])
   Services.scriptloader.loadSubScriptWithOptions(rootURI+'chrome/content/scripts/'+name,{target:ctx,ignoreCache:true});
  const bridge=ctx.ZhanhTRANLocalParser.install({IO:ctx.IOUtils,Subprocess,profileDir,
   join:(...p)=>PathUtils.join(...p),dirname:p=>PathUtils.parent(p),normalize:p=>PathUtils.normalize(p),
   uuid:()=>Services.uuid.generateUUID().toString(),...timers,logError:e=>Zotero.logError(e),
   environment:Object.fromEntries(Object.entries(Subprocess.getEnvironment()).filter(([key])=>/^(PATH|SYSTEMROOT|WINDIR|TEMP|TMP|USERPROFILE|HOME)$/i.test(key))),
   python:()=>Zotero.Prefs.get('extensions.zotero.zhanhtran.localPython',true)||'',
   scriptText:()=>read('local_parser.py'),pdfScriptText:()=>read('local_pdf.py'),imageScriptText:()=>read('local_image.py')});
  const Login=Components.Constructor('@mozilla.org/login-manager/loginInfo;1','nsILoginInfo','init');
  const createLogin=(...args)=>new Login(...args);
  api=ctx.ZhanhTRANCustom.install({Zotero,Services,IOUtils:ctx.IOUtils,PathUtils:{profileDir,join:(...p)=>PathUtils.join(...p)},
   core:ctx.ZhanhTRANCustomCore,output:ctx.ZhanhTRANOutput,bridge,fetch:ctx.fetch,createLogin,iconURL:rootURI+'icons/icon-48.png',createProgress:ctx.ZhanhTRANTranslationProgress.create});
  api.configState();api.outputSettings();
  Zotero.zhanhTRAN=api;
  await Zotero.PreferencePanes.register({pluginID:id,id:'zhanhtran-settings',label:'zhanhTRAN',
   src:'chrome://zhanhtran/content/settings.xhtml',image:rootURI+'icons/icon-32.png',scripts:['chrome://zhanhtran/content/settings.js']});
  for(const win of Zotero.getMainWindows())api.registerWindow(win);
 }catch(error){shutdown();throw error;}
}
function onMainWindowLoad({window}){api?.registerWindow(window);}
function onMainWindowUnload({window}){api?.unregisterWindow(window);}
function shutdown(){
 api?.shutdown();api=null;
 if(typeof Zotero!=='undefined')delete Zotero.zhanhTRAN;
 try{chromeHandle?.destruct();}finally{chromeHandle=null;if(sandbox){Cu.nukeSandbox(sandbox);sandbox=null;}}
}
