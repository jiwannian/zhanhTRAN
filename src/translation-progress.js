/* A job-owned message window, independent of Zotero's tiled progress windows. */
(function(root) {
  'use strict';
  root.ZhanhTRANTranslationProgress = {
    create(owner, controller, onClosed) {
      let dialog, elements, closed=false, finished=false;
      let text='本地解析 / OCR / 表格识别…', percent=null;
      function render() {
        if(closed || !elements)return;
        elements.status.textContent=text;
        if(percent===null)elements.progress.removeAttribute('value');
        else elements.progress.setAttribute('value',percent);
        elements.percent.textContent=percent===null?'':Math.round(percent)+'%';
        elements.action.textContent=finished?'关闭':controller.signal.aborted?'正在取消…':'取消';
        elements.action.disabled=!finished && controller.signal.aborted;
      }
      function cancelled() {
        if(!finished){text='正在取消…';render();}
      }
      function unloaded() {
        // Gecko navigates the initial about:blank document before loading chrome XHTML.
        if(!dialog?.closed && !elements)return;
        if(closed)return;
        closed=true;elements=null;
        owner.removeEventListener('unload',close);
        controller.signal.removeEventListener('abort',cancelled);
        if(!finished)controller.abort();
        onClosed?.();
      }
      function close() {
        if(closed)return;
        try{if(!dialog.closed)dialog.close();}finally{unloaded();}
      }
      const progress=Object.freeze({
        setText(value){if(!closed){text=String(value);render();}},
        setProgress(value){if(!closed){percent=Number.isFinite(value)?Math.max(0,Math.min(100,value)):null;render();}},
        bind(win) {
          if(closed || elements)return;
          const doc=win.document;
          elements={status:doc.getElementById('translation-status'),progress:doc.getElementById('translation-progress'),
            percent:doc.getElementById('translation-percent'),action:doc.getElementById('translation-action')};
          elements.action.addEventListener('click',()=>{if(finished)close();else controller.abort();});
          render();
        },
        finish(){if(!closed){finished=true;render();}},
        close
      });
      dialog=owner.openDialog('chrome://zhanhtran/content/translation-progress.xhtml','',
        'chrome,centerscreen,dialog=no,titlebar=yes,resizable=yes,width=520,height=210',{progress});
      if(!dialog || dialog.closed)throw new Error('翻译消息窗口无法打开');
      dialog.addEventListener('unload',unloaded);
      owner.addEventListener('unload',close,{once:true});
      controller.signal.addEventListener('abort',cancelled,{once:true});
      return progress;
    }
  };
})(typeof _globalThis!=='undefined'?_globalThis:globalThis);
