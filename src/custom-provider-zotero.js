/* Privileged Zotero adapter. Only this module touches preferences, login storage or UI. */
(function(root) {
  'use strict';
  // Scoped styles also work when Zotero inserts this UI into its XUL preferences host.
  const SETTINGS_CSS = `
    #zhanhtran-custom-provider-root { color-scheme:light dark; container:zhanhtran-settings / inline-size; width:100%; min-width:0; }
    #zhanhtran-custom-provider-root .zt-settings {
      --zt-bg:#ffffff; --zt-field:#ffffff; --zt-soft:#f5f7f8; --zt-text:#252b32;
      --zt-muted:#69727d; --zt-line:#dce1e5; --zt-border:#bbc3cb; --zt-accent:#087f76;
      --zt-hover:#edf6f4; --zt-error:#ad343e; --zt-success:#23734f;
      box-sizing:border-box; width:100%; max-width:780px; margin:0 auto; padding:22px 26px;
      background:var(--zt-bg); color:var(--zt-text); font:14px/1.5 system-ui,sans-serif;
    }
    #zhanhtran-custom-provider-root .zt-settings * { box-sizing:border-box; letter-spacing:0; }
    #zhanhtran-custom-provider-root .zt-brand { display:flex; align-items:center; gap:12px; padding-bottom:20px; }
    #zhanhtran-custom-provider-root .zt-brand img { flex:none; width:40px; height:40px; }
    #zhanhtran-custom-provider-root h2 { margin:0; font-size:22px; line-height:1.3; font-weight:650; }
    #zhanhtran-custom-provider-root h3 { margin:0; font-size:15px; font-weight:650; line-height:1.5; }
    #zhanhtran-custom-provider-root .zt-section { padding:18px 0; border-top:1px solid var(--zt-line); }
    #zhanhtran-custom-provider-root .zt-section-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
    #zhanhtran-custom-provider-root .zt-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
    #zhanhtran-custom-provider-root .zt-provider-picker { margin-bottom:14px; }
    #zhanhtran-custom-provider-root .zt-provider-grid,
    #zhanhtran-custom-provider-root .zt-output-grid,
    #zhanhtran-custom-provider-root .zt-text-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 18px; }
    #zhanhtran-custom-provider-root .zt-field { display:flex; flex-direction:column; gap:5px; min-width:0; }
    #zhanhtran-custom-provider-root .zt-field > span { font-size:13px; color:var(--zt-muted); overflow-wrap:anywhere; }
    #zhanhtran-custom-provider-root .zt-wide { grid-column:1/-1; }
    #zhanhtran-custom-provider-root input,
    #zhanhtran-custom-provider-root select,
    #zhanhtran-custom-provider-root textarea,
    #zhanhtran-custom-provider-root button { font:inherit; color:var(--zt-text); margin:0; }
    #zhanhtran-custom-provider-root input:not([type=checkbox]):not([type=radio]),
    #zhanhtran-custom-provider-root select,
    #zhanhtran-custom-provider-root textarea {
      width:100%; min-width:0; border:1px solid var(--zt-border); border-radius:5px;
      background:var(--zt-field); padding:7px 10px; box-shadow:none;
    }
    #zhanhtran-custom-provider-root input:not([type=checkbox]):not([type=radio]),
    #zhanhtran-custom-provider-root select { height:36px; }
    #zhanhtran-custom-provider-root select { appearance:auto; padding-right:24px; }
    #zhanhtran-custom-provider-root input[type=checkbox],
    #zhanhtran-custom-provider-root input[type=radio] { flex:none; width:16px; height:16px; accent-color:var(--zt-accent); }
    #zhanhtran-custom-provider-root button {
      min-height:34px; padding:5px 12px; border:1px solid var(--zt-border); border-radius:5px;
      background:var(--zt-field); cursor:pointer; white-space:normal;
    }
    #zhanhtran-custom-provider-root button:hover:not(:disabled) { background:var(--zt-hover); border-color:var(--zt-accent); }
    #zhanhtran-custom-provider-root button.zt-primary { background:var(--zt-accent); border-color:var(--zt-accent); color:#ffffff; }
    #zhanhtran-custom-provider-root button.zt-primary:hover:not(:disabled) { background:#066b64; }
    #zhanhtran-custom-provider-root button.zt-danger { color:var(--zt-error); }
    #zhanhtran-custom-provider-root button:disabled { opacity:.5; cursor:default; }
    #zhanhtran-custom-provider-root :is(input,select,textarea,button,summary):focus-visible {
      outline:2px solid var(--zt-accent); outline-offset:2px;
    }
    #zhanhtran-custom-provider-root .zt-save-actions { margin-top:16px; justify-content:flex-end; }
    #zhanhtran-custom-provider-root fieldset { margin:0; padding:0; min-width:0; border:0; }
    #zhanhtran-custom-provider-root legend { padding:0; margin-bottom:10px; font-size:15px; font-weight:650; }
    #zhanhtran-custom-provider-root .zt-checks { display:flex; flex-wrap:wrap; gap:10px 20px; padding-top:8px; }
    #zhanhtran-custom-provider-root .zt-choice { display:flex; align-items:center; gap:8px; min-height:30px; cursor:pointer; }
    #zhanhtran-custom-provider-root .zt-order { display:grid; grid-template-columns:1fr; gap:4px; }
    #zhanhtran-custom-provider-root .zt-protocol { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; min-height:36px; }
    #zhanhtran-custom-provider-root :is(.zt-order,.zt-protocol) .zt-choice { min-width:0; padding:5px 9px; border:1px solid transparent; border-radius:4px; font-size:13px; }
    #zhanhtran-custom-provider-root :is(.zt-order,.zt-protocol) .zt-choice:has(input:checked) { background:var(--zt-hover); border-color:var(--zt-line); }
    #zhanhtran-custom-provider-root .zt-layout-label { font-size:13px; color:var(--zt-muted); margin:0 0 7px; }
    #zhanhtran-custom-provider-root .zt-status { padding:8px 10px; margin:12px 0 0; border-left:3px solid var(--zt-border); background:var(--zt-soft); font-size:13px; overflow-wrap:anywhere; }
    #zhanhtran-custom-provider-root .zt-status:empty { display:none; }
    #zhanhtran-custom-provider-root .zt-status[data-tone=success] { color:var(--zt-success); border-color:var(--zt-success); }
    #zhanhtran-custom-provider-root .zt-status[data-tone=error] { color:var(--zt-error); border-color:var(--zt-error); }
    #zhanhtran-custom-provider-root .zt-status[data-tone=busy] { border-color:var(--zt-accent); }
    #zhanhtran-custom-provider-root .zt-test summary { cursor:pointer; font-size:15px; font-weight:650; }
    #zhanhtran-custom-provider-root .zt-test[open] summary { margin-bottom:14px; }
    #zhanhtran-custom-provider-root textarea { min-height:112px; resize:vertical; }
    #zhanhtran-custom-provider-root textarea[readonly] { background:var(--zt-soft); }
    #zhanhtran-custom-provider-root .zt-test .zt-actions { margin-top:12px; justify-content:flex-end; }
    @container zhanhtran-settings (max-width:600px) {
      #zhanhtran-custom-provider-root .zt-settings { padding:18px 16px; }
      #zhanhtran-custom-provider-root .zt-provider-grid,
      #zhanhtran-custom-provider-root .zt-output-grid,
      #zhanhtran-custom-provider-root .zt-text-grid { grid-template-columns:minmax(0,1fr); }
      #zhanhtran-custom-provider-root .zt-section-head { flex-wrap:wrap; }
      #zhanhtran-custom-provider-root .zt-order { grid-template-columns:repeat(2,minmax(0,1fr)); }
      #zhanhtran-custom-provider-root .zt-order .zt-choice { padding:6px; font-size:12px; gap:5px; }
    }
    @media (prefers-color-scheme:dark) {
      #zhanhtran-custom-provider-root .zt-settings {
        --zt-bg:#242628; --zt-field:#2c2e31; --zt-soft:#2b2e30; --zt-text:#eef0f2;
        --zt-muted:#b3bac2; --zt-line:#40454a; --zt-border:#646c75; --zt-accent:#12867d;
        --zt-hover:#293e3b; --zt-error:#ff949b; --zt-success:#88d5a9;
      }
    }
  `;
  root.ZhanhTRANCustom = {
    install(env) {
      const {Zotero:Z,Services:S,IOUtils:IO,PathUtils:P,bridge,core:C,createProgress} = env;
      const PREF = 'extensions.zotero.zhanhtran.customProvider';
      const ORIGIN = 'chrome://zhanhtran';
      const windows = new Map(); const jobs = new Map(); const messages = new Map();
      let alive = true;
      function configState() {
        const raw = Z.Prefs.get(PREF,true);
        if (!raw) return {selected:C.DEFAULT.id,providers:[C.DEFAULT]};
        try {
          const data = JSON.parse(raw);
          const providers = data.providers.map(C.validate);
          if (!providers.length || new Set(providers.map(p=>p.id)).size!==providers.length) throw new Error();
          return {selected:providers.some(p=>p.id===data.selected)?data.selected:providers[0].id,providers};
        } catch (_) { throw new Error('自定义供应商配置损坏，请在设置中重新保存'); }
      }
      function store(state) { Z.Prefs.set(PREF,JSON.stringify(state),true); }
      function selected() { const state=configState(); return state.providers.find(p=>p.id===state.selected); }
      function outputSettings() {
        return (env.output || root.ZhanhTRANOutput).outputOptions(Z.Prefs.get('extensions.zotero.zhanhtran.output',true));
      }
      function saveOutput(input) {
        const settings=(env.output || root.ZhanhTRANOutput).outputOptions(input);
        Z.Prefs.set('extensions.zotero.zhanhtran.output',JSON.stringify(settings),true);return settings;
      }
      function realm(p) { return p.id+'|'+p.baseURL; }
      async function logins(p) { return await S.logins.findLogins(ORIGIN,null,realm(p)); }
      async function keyFor(p) { return (await logins(p))[0]?.password || ''; }
      async function setKey(p,key) {
        C.request(p,key,'credential-validation');
        const entry = env.createLogin(ORIGIN,null,realm(p),p.id,key,'','');
        const existing = await logins(p);
        if (existing.length) S.logins.modifyLogin(existing[0],entry); else await (S.logins.addLoginAsync ? S.logins.addLoginAsync(entry) : S.logins.addLogin(entry));
      }
      async function save(input,key='') {
        const p=C.validate(input); const state=configState();
        const old=state.providers.find(x=>x.id===p.id);
        if (key.trim()) await setKey(p,key);
        const providers = old ? state.providers.map(x=>x.id===p.id?p:x) : [...state.providers,p];
        store({selected:p.id,providers});
        if (old && old.baseURL!==p.baseURL) for(const login of await logins(old)) S.logins.removeLogin(login);
        return p;
      }
      async function remove(id) {
        const state=configState(); if(state.providers.length===1) throw new Error('至少保留一个供应商');
        const p=state.providers.find(x=>x.id===id); if(!p) throw new Error('供应商不存在');
        for(const login of await logins(p)) S.logins.removeLogin(login);
        const providers=state.providers.filter(x=>x.id!==id);
        store({providers,selected:state.selected===id?providers[0].id:state.selected});
      }
      function checkAlive(signal) {
        if(!alive || signal?.aborted) throw new Error('翻译已取消');
      }
      async function sourceFor(win,item,signal) {
        if(item.isNote()) throw new Error('请选择 PDF 或图片附件；笔记没有源文件类型，无法生成同类型文件');
        let attachment=item;
        if(item.isRegularItem()) {
          const ids=item.getAttachments();
          attachment=ids.map(id=>Z.Items.get(id)).find(x=>x?.attachmentContentType==='application/pdf') ||
            ids.map(id=>Z.Items.get(id)).find(x=>/^image\//.test(x?.attachmentContentType));
        }
        if(!attachment?.isAttachment() || !(attachment.attachmentContentType==='application/pdf' || /^image\//.test(attachment.attachmentContentType)))
          throw new Error('请选择 PDF、图片附件或含这些附件的文献条目');
        const path=await attachment.getFilePathAsync(); if(!path) throw new Error('附件文件不可用，请先下载');
        checkAlive(signal);
        const document=await bridge.parseDocument(path,signal);
        checkAlive(signal);
        const extension=path.match(/\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i)?.[1].toLowerCase();
        const types={pdf:'application/pdf',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',tif:'image/tiff',tiff:'image/tiff',bmp:'image/bmp',webp:'image/webp'};
        if(!types[extension])throw new Error('本地解析文件类型无效');
        return {document,contentType:types[extension],sourcePath:path,isPDF:extension==='pdf',title:attachment.getField('title') || '文档',parentID:attachment.parentID || null,
          libraryID:attachment.libraryID,collections:attachment.parentID?[]:(attachment.getCollections?.() || [])};
      }
      async function createFileAttachments(source,result,paths,signal) {
        if(!/^(application\/pdf|image\/(png|jpeg|tiff|bmp|webp))$/.test(source.contentType))throw new Error('本地解析文件类型无效');
        const ids=[];
        try {
          for(const [kind,label] of [['translated','译文'],['bilingual','双语']]) {
            if(!paths[kind])continue;
            checkAlive(signal);
            const options={file:paths[kind],libraryID:source.libraryID,contentType:source.contentType,
              ...(source.parentID?{parentItemID:source.parentID}:{collections:source.collections || []})};
            // Personal-library links retain the sidecar path; groups cannot link local files.
            const attachment=source.libraryID===Z.Libraries.userLibraryID
              ? await Z.Attachments.linkFromFile(options) : await Z.Attachments.importFromFile(options);
            ids.push(attachment.id);checkAlive(signal);
            attachment.setField('title',source.title+' — '+label+' · '+result.provider+' · '+result.model);
            await attachment.saveTx();
          }
          checkAlive(signal);return ids;
        } catch(error) {
          for(const id of ids){try{await Z.Items.get(id).eraseTx();}catch(cleanupError){Z.logError?.(cleanupError);}}
          throw error;
        }
      }
      async function translateSelected(win) {
        if(jobs.has(win)) { jobs.get(win).abort(); return; }
        const items=win.ZoteroPane.getSelectedItems();
        if(items.length!==1) { S.prompt.alert(win,'zhanhTRAN','请仅选择一个 PDF、图片附件或含附件的文献条目'); return; }
        const controller=new AbortController();jobs.set(win,controller);
        let progress;
        try {
          checkAlive(controller.signal);
          const output=outputSettings();const p=selected(); const key=await keyFor(p);
          checkAlive(controller.signal);
          if(!key) { S.prompt.alert(win,'zhanhTRAN','请先保存自定义供应商 API 密钥');openSettings(win);return; }
          const owned=messages.get(win) || new Set();messages.set(win,owned);
          progress=createProgress(win,controller,()=>{owned.delete(progress);if(!owned.size)messages.delete(win);});
          owned.add(progress);checkAlive(controller.signal);
          const source=await sourceFor(win,items[0],controller.signal);
          const options={fetch:env.fetch,signal:controller.signal,retryAttempts:5,
            onRetry:(attempt,total,reason)=>progress.setText(reason+'，重试 '+attempt+'/'+total+'…'),
            onProgress:(done,total)=>{progress.setText('模型翻译 '+done+'/'+total);progress.setProgress(done/total*100);}};
          const result=await C.translateDocument(p,key,source.document,options);
          checkAlive(controller.signal);
          await bridge.saveResult(source.document,result);
          progress.setText('正在生成同类型译文 / 双语文件…');
          const paths=await bridge.exportFile(source.document,controller.signal,output);
          const selectedPaths=Object.fromEntries(Object.entries(paths).filter(([kind])=>output[kind]===true));
          checkAlive(controller.signal);
          await createFileAttachments(source,result,selectedPaths,controller.signal);
          progress.setText('已创建'+(output.translated&&output.bilingual?'译文与双语':output.translated?'译文':'双语')+'文件附件');
          progress.setProgress(100);
        } catch(error) {
          if(!progress && controller.signal.aborted)return;
          const message=/^(翻译已取消|没有可翻译|供应商|请求超时|网络请求失败|模型输出|本地解析|PDF|结构化|图片|请选择|请至少选择|输出设置|附件文件)/.test(error.message)?error.message:'翻译失败：请检查本地解析器、文件与模型配置';
          progress?.setText(message);if(!controller.signal.aborted)S.prompt.alert(win,'zhanhTRAN',message);
        } finally {
          if(jobs.get(win)===controller)jobs.delete(win);
          progress?.finish();
        }
      }
      function openSettings(win) {
        return win.openDialog('chrome://zhanhtran/content/custom-provider.xhtml','zhanhtran-custom-provider',
          'chrome,centerscreen,resizable,width=760,height=820',{api});
      }
      function registerWindow(win) {
        if(windows.has(win)) return;
        const nodes=[];
        for(const id of ['menu_ToolsPopup','zotero-itemmenu']) {
          const popup=win.document.getElementById(id);if(!popup) continue;
          const item=win.document.createXULElement('menuitem');
          item.id='zhanhtran-custom-translate-'+id;item.setAttribute('label','zhanhTRAN：翻译');
          item.addEventListener('command',()=>translateSelected(win).catch(()=>S.prompt.alert(win,'zhanhTRAN','自定义翻译启动失败，请检查配置及凭据存储')));
          popup.appendChild(item);nodes.push(item);
        }
        const tools=win.document.getElementById('menu_ToolsPopup');
        if(tools) { const item=win.document.createXULElement('menuitem');item.id='zhanhtran-custom-provider-settings';
          item.setAttribute('label','zhanhTRAN：设置');item.addEventListener('command',()=>openSettings(win));tools.appendChild(item);nodes.push(item); }
        windows.set(win,nodes);
      }
      function unregisterWindow(win) {
        jobs.get(win)?.abort(); jobs.delete(win);
        for(const progress of [...messages.get(win)||[]])progress.close();messages.delete(win);
        for(const node of windows.get(win)||[]) node.remove();windows.delete(win);
      }
      function mount(win,container=win.document.getElementById('zhanhtran-custom-provider-root')) {
        if(!container || container.dataset.mounted) return;
        container.dataset.mounted='true';
        const doc=win.document;const H='http://www.w3.org/1999/xhtml';
        function el(tag,text) { const node=doc.createElementNS(H,tag);if(text)node.textContent=text;return node; }
        const style=el('style',SETTINGS_CSS);container.appendChild(style);
        const panel=el('section');panel.className='zt-settings';container.appendChild(panel);
        const brand=el('header');brand.className='zt-brand';
        const icon=el('img');icon.src=env.iconURL;icon.alt='';icon.width=40;icon.height=40;
        brand.appendChild(icon);brand.appendChild(el('h2','zhanhTRAN'));panel.appendChild(brand);
        function section(title) {
          const node=el('section');node.className='zt-section';const head=el('div');head.className='zt-section-head';
          head.appendChild(el('h3',title));node.appendChild(head);panel.appendChild(node);return {node,head};
        }
        function field(parent,label,node) {
          const grouped=node.getAttribute('role')==='radiogroup';
          const wrap=el(grouped?'div':'label'),title=el('span',label);wrap.className='zt-field';
          if(grouped){title.id=node.id+'-label';node.setAttribute('aria-labelledby',title.id);}
          wrap.appendChild(title);
          wrap.appendChild(node);parent.appendChild(wrap);return wrap;
        }
        const provider=section('供应商');
        const manage=el('div');manage.className='zt-actions';provider.head.appendChild(manage);
        const selector=el('select');selector.id='zhanhtran-provider-select';
        const picker=field(provider.node,'当前供应商',selector);picker.classList.add('zt-provider-picker');
        const grid=el('div');grid.className='zt-provider-grid';provider.node.appendChild(grid);
        const fields={},protocolFields={};
        for(const [name,label,type] of [['name','供应商名称','text'],['model','自定义模型','text'],['baseURL','Base URL（根地址或 /v1）','url'],['protocol','接口协议','radio'],['language','目标语言','text'],['key','API 密钥（留空保留原密钥）','password']]) {
          const node=el(type==='radio'?'div':'input');node.id='zhanhtran-custom-'+name;
          if(type!=='radio')node.setAttribute('type',type);
          else {
            node.className='zt-protocol';node.setAttribute('role','radiogroup');
            for(const [value,text] of [['openai','OpenAI 兼容'],['anthropic','Anthropic 原生']]) {
              const wrap=el('label'),radio=el('input');wrap.className='zt-choice';radio.type='radio';
              radio.name='zhanhtran-protocol';radio.value=value;wrap.appendChild(radio);wrap.appendChild(el('span',text));
              node.appendChild(wrap);protocolFields[value]=radio;
            }
          }
          if(name==='key')node.autocomplete='off';
          if(['baseURL','model','key'].includes(name))node.spellcheck=false;
          const wrap=field(grid,label,node);if(['baseURL','key'].includes(name))wrap.classList.add('zt-wide');fields[name]=node;
        }
        const buttons=el('div');buttons.className='zt-actions zt-save-actions';provider.node.appendChild(buttons);
        const status=el('p');status.className='zt-status';status.setAttribute('role','status');provider.node.appendChild(status);
        const setStatus=(text,tone='neutral')=>{status.textContent=text;status.dataset.tone=tone;};
        const outputSection=el('section');outputSection.className='zt-section';panel.appendChild(outputSection);
        const outputGrid=el('div');outputGrid.className='zt-output-grid';outputSection.appendChild(outputGrid);
        const outputs=el('fieldset');outputs.appendChild(el('legend','双语输出'));outputGrid.appendChild(outputs);
        const checks=el('div');checks.className='zt-checks';outputs.appendChild(checks);
        const initial=outputSettings(),outputFields={};
        for(const [kind,label] of [['translated','译文文件'],['bilingual','双语文件']]){
          const wrap=el('label'),box=el('input');box.type='checkbox';box.id='zhanhtran-output-'+kind;box.checked=initial[kind];
          wrap.className='zt-choice';wrap.appendChild(box);wrap.appendChild(el('span',label));checks.appendChild(wrap);outputFields[kind]=box;
        }
        const layout=el('fieldset');layout.appendChild(el('legend','排版输出'));outputGrid.appendChild(layout);
        const layoutLabel=el('p','保留原布局');layoutLabel.className='zt-layout-label';layout.appendChild(layoutLabel);
        const order=el('div');order.id='zhanhtran-output-order';order.className='zt-order';layout.appendChild(order);
        const orderFields={};
        for(const [value,label] of [['original-left','原文左 / 译文右'],['translated-left','译文左 / 原文右']]){
          const wrap=el('label'),radio=el('input');wrap.className='zt-choice';radio.type='radio';radio.name='zhanhtran-panel-order';
          radio.value=value;radio.checked=initial.order===value;wrap.appendChild(radio);wrap.appendChild(el('span',label));order.appendChild(wrap);orderFields[value]=radio;
        }
        const outputStatus=el('p');outputStatus.className='zt-status';outputStatus.dataset.tone='error';outputStatus.setAttribute('role','status');outputSection.appendChild(outputStatus);
        const updateOutput=()=>{try{saveOutput({translated:outputFields.translated.checked,bilingual:outputFields.bilingual.checked,order:Object.values(orderFields).find(r=>r.checked).value});outputStatus.textContent='';}
          catch(error){outputStatus.textContent=error.message;const saved=outputSettings();for(const kind of ['translated','bilingual'])outputFields[kind].checked=saved[kind];for(const radio of Object.values(orderFields))radio.checked=radio.value===saved.order;}};
        for(const box of [...Object.values(outputFields),...Object.values(orderFields)])box.addEventListener('change',updateOutput);
        const local=section('本地解析器');const python=el('input');python.id='zhanhtran-local-python';python.spellcheck=false;
        python.value=Z.Prefs.get('extensions.zotero.zhanhtran.localPython',true) || '';
        python.addEventListener('change',()=>{Z.Prefs.set('extensions.zotero.zhanhtran.localPython',python.value.trim(),true);});
        field(local.node,'Python 路径',python);
        let editingID;let controller;
        function load(p) { editingID=p.id;for(const name of ['name','baseURL','model','language'])fields[name].value=p[name];for(const radio of Object.values(protocolFields))radio.checked=radio.value===p.protocol;fields.key.value=''; }
        function refresh() { selector.replaceChildren();const state=configState();for(const p of state.providers){const op=el('option',p.name+' · '+p.model);op.value=p.id;selector.appendChild(op);}selector.value=state.selected;load(selected()); }
        function read() { return C.validate({id:editingID,...Object.fromEntries(['name','baseURL','model','language'].map(n=>[n,fields[n].value])),protocol:Object.values(protocolFields).find(r=>r.checked).value}); }
        selector.addEventListener('change',()=>{const state=configState();store({...state,selected:selector.value});load(selected());});
        function button(parent,label,action,className='') {const b=el('button',label);b.className=className;b.setAttribute('type','button');b.addEventListener('click',()=>Promise.resolve().then(action).catch(e=>setStatus(e.message,'error')));parent.appendChild(b);return b;}
        button(manage,'新增',()=>{load({...C.DEFAULT,id:'provider-'+Date.now().toString(36),name:'自定义供应商'});setStatus('填写后点击保存');});
        button(manage,'删除',async()=>{if(S.prompt.confirm(win,'zhanhTRAN','删除此供应商及其已保存密钥？')){await remove(editingID);refresh();setStatus('已删除');}},'zt-danger');
        button(buttons,'保存并设为当前',async()=>{await save(read(),fields.key.value);refresh();setStatus('已保存（密钥不会显示）','success');},'zt-primary');
        const test=el('details');test.className='zt-section zt-test';test.appendChild(el('summary','文本翻译测试'));panel.appendChild(test);
        const textGrid=el('div');textGrid.className='zt-text-grid';test.appendChild(textGrid);
        const input=el('textarea');input.id='zhanhtran-custom-text';input.rows=4;input.placeholder='待译文本';field(textGrid,'原文',input);
        const output=el('textarea');output.id='zhanhtran-custom-result';output.rows=4;output.readOnly=true;output.placeholder='译文';field(textGrid,'译文',output);
        const testButtons=el('div');testButtons.className='zt-actions';test.appendChild(testButtons);
        async function run(text) {
          if(controller) throw new Error('请求正在执行，请先取消');
          const current=new AbortController();controller=current;
          connect.disabled=true;translate.disabled=true;cancel.disabled=false;
          try {
            checkAlive(current.signal);
            const p=read();const key=fields.key.value.trim() || await keyFor(p);
            checkAlive(current.signal);setStatus('正在直连 '+p.baseURL+'…','busy');
            const result=await C.translate(p,key,text,{fetch:env.fetch,signal:current.signal,retryAttempts:5,
            onRetry:(n,total,reason)=>setStatus(reason+'，重试 '+n+'/'+total+'…','busy'),
            onProgress:(n,total)=>setStatus('翻译 '+n+'/'+total,'busy')});
            checkAlive(current.signal);output.value=result.translated;setStatus('成功 · '+result.model+' · '+result.parts+' 块','success');return result;
          } finally {if(controller===current)controller=null;connect.disabled=false;translate.disabled=false;cancel.disabled=true;}
        }
        const connect=button(buttons,'联通测试',()=>run('Hello, world.'));
        const translate=button(testButtons,'翻译文本',()=>run(input.value));
        const cancel=button(testButtons,'取消',()=>controller?.abort());cancel.disabled=true;
        win.addEventListener('unload',()=>{controller?.abort();fields.key.value='';},{once:true});refresh();
      }
      const api=Object.freeze({configState,selected,save,remove,keyFor,mount,openSettings,outputSettings,saveOutput,
        translateSelected,registerWindow,unregisterWindow,createFileAttachments,sourceFor,
        shutdown(){alive=false;for(const win of new Set([...windows.keys(),...jobs.keys(),...messages.keys()]))unregisterWindow(win);}});
      return api;
    }
  };
})(typeof _globalThis!=='undefined'?_globalThis:globalThis);
