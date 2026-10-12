/* One-shot local parser; no HTTP listener, shell command string or model key. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object' && module.exports) module.exports=api;
  else root.ZhanhTRANLocalParser=api;
})(typeof _globalThis!=='undefined'?_globalThis:globalThis,function(){
  'use strict';
  function install(env){
    const {IO,join,normalize,Subprocess}=env;
    const issued=new Map();
    const check=signal=>{if(signal?.aborted)throw new Error('翻译已取消');};
    async function readPipe(pipe){
      let output='',part;
      while((part=await pipe.readString())!==''){
        if(output.length+part.length>262144)throw new Error('本地解析日志超过限制');
        output+=part;
      }
      return output;
    }
    function outputFor(doc){
      if(!issued.has(doc?.output_dir))throw new Error('本地解析结果目录无效');
      return doc.output_dir;
    }
    async function parseDocument(path,signal){
      check(signal);
      if(typeof path!=='string' || !/\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i.test(path) || /[\x00-\x1f]/.test(path))
        throw new Error('本地解析文件类型无效');
      if(!await IO.exists(path) || (await IO.stat(path)).size>268435456)throw new Error('本地解析文件不可用或超过 256 MB');
      const python=env.python();
      if(!python || !await IO.exists(python))throw new Error('本地解析器未安装，请配置 Python 路径');
      const root=join(env.profileDir,'zhanhtran-local-results');
      const run=join(root,'run-'+env.uuid().replace(/[^a-zA-Z0-9-]/g,''));
      await IO.makeDirectory(run,{createAncestors:true});
      const output=join(run,'document');const script=join(run,'local_parser.py');
      let process,timer,timedOut=false;
      const abort=()=>{if(process)void process.kill(0).catch(()=>{});};
      try{
        await IO.writeUTF8(script,await env.scriptText());
        check(signal);
        process=await Subprocess.call({command:python,arguments:['-I',script,'--input',path,'--output',output],
          environment:{...env.environment,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},environmentAppend:false,stderr:'pipe'});
        signal?.addEventListener('abort',abort,{once:true});
        if(signal?.aborted)abort();
        timer=env.setTimeout(()=>{timedOut=true;abort();},1200000);
        const [status,stdout]=await Promise.all([process.wait(),readPipe(process.stdout),readPipe(process.stderr)]);
        check(signal);
        if(timedOut)throw new Error('本地解析超时，请拆分文档');
        if(status.exitCode!==0){
          let code='';try{code=JSON.parse(stdout.trim()).error;}catch(_){}
          const labels={PDF_PASSWORD_REQUIRED:'PDF 需要密码，请先解密',PAGE_LIMIT_EXCEEDED:'文档超过 500 页，请拆分',
            INVALID_PDF:'PDF 文件损坏',ModuleNotFoundError:'本地解析依赖缺失，请重新安装解析器'};
          throw new Error('本地解析失败：'+(labels[code] || '请检查文件和 OCR 依赖'));
        }
        const file=join(output,'document.json');
        if((await IO.stat(file)).size>64*1024*1024)throw new Error('本地解析结果过大，请拆分文档');
        const doc=JSON.parse(await IO.readUTF8(file));
        if(doc.schema!==1 || !Array.isArray(doc.pages) || !doc.pages.length || normalize(doc.output_dir)!==normalize(output))
          throw new Error('本地解析结果无效');
        issued.set(doc.output_dir,path);
        return doc;
      }catch(error){
        if(process && process.exitCode == null){try{await process.kill(0);}catch(_){} try{await process.wait();}catch(_){} }
        await IO.remove(run,{recursive:true,ignoreAbsent:true});
        if(/^(本地解析|翻译已取消)/.test(error.message))throw error;
        throw new Error('本地解析启动失败，请检查 Python 路径与依赖',{cause:error});
      }finally{env.clearTimeout(timer);signal?.removeEventListener('abort',abort);}
    }
    async function assetPath(doc,asset){
      const folder=outputFor(doc);
      if(!/^figures\/[\w-]+\.png$/.test(asset) || !doc.assets?.some(x=>x.path===asset))throw new Error('图片资源路径无效');
      const file=join(folder,...asset.split('/'));
      if(!await IO.exists(file) || (await IO.stat(file)).size>100*1024*1024)throw new Error('图片资源不可用');
      return file;
    }
    async function saveResult(doc,result){
      const folder=outputFor(doc);
      await IO.writeUTF8(join(folder,'translated.json'),JSON.stringify(result.document,null,2));
      return folder;
    }
    async function publishFiles(source,paths,signal){
      const directory=env.dirname(source),copied=[];
      const label=/\.pdf$/i.test(source)?'PDF':'图片';
      const names=Object.fromEntries(Object.entries(paths).map(([kind,path])=>[kind,path.split(/[\\/]/).pop()]));
      let published;
      try{
        for(let n=1;n<=999;n++){
          check(signal);
          const candidate=Object.fromEntries(Object.entries(names).map(([kind,name])=>
            [kind,join(directory,name.replace(/(\.[^.]+)$/,(n===1?'':'-'+n)+'$1'))]));
          if((await Promise.all(Object.values(candidate).map(path=>IO.exists(path)))).some(Boolean))continue;
          published=candidate;break;
        }
        if(!published)throw new Error(label+' 同目录文件名已占满，请清理旧译文或重命名源文件');
        for(const [kind,path] of Object.entries(published)){
          check(signal);
          // An exclusive copy also closes the exists/copy race without overwriting user files.
          await IO.copy(paths[kind],path,{noOverwrite:true});copied.push(path);
        }
        check(signal);
        return Object.freeze(published);
      }catch(error){
        for(const path of copied){try{await IO.remove(path,{ignoreAbsent:true});}catch(cleanup){env.logError?.(cleanup);}}
        if(/^(PDF|图片|翻译已取消)/.test(error.message))throw error;
        throw new Error(label+' 无法保存到源文件目录，请检查目录写入权限或同名文件',{cause:error});
      }
    }
    async function exportFile(doc,signal,settings={translated:true,bilingual:true,order:'original-left'}){
      check(signal);
      if(typeof settings.translated!=='boolean'||typeof settings.bilingual!=='boolean'||(!settings.translated&&!settings.bilingual)||
        !['original-left','translated-left'].includes(settings.order))throw new Error('输出设置无效');
      const folder=outputFor(doc),source=issued.get(folder);
      const extension=source.match(/\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i)?.[0];
      if(!extension)throw new Error('本地解析文件类型无效');
      const isPDF=extension.toLowerCase()==='.pdf';
      const label=isPDF?'PDF':'图片';
      const python=env.python(),script=join(folder,isPDF?'local_pdf.py':'local_image.py');
      const stem=Array.from(source.split(/[\\/]/).pop().slice(0,-extension.length)).slice(0,64).join('').replace(/[ .]+$/,'');
      const suffix=isPDF?'.pdf':extension;
      const paths=Object.fromEntries(['translated','bilingual'].filter(kind=>settings[kind]).map(kind=>[kind,join(folder,stem+'-'+kind+suffix)]));
      for(const path of Object.values(paths))if(await IO.exists(path))throw new Error(label+' 输出文件已存在，请重新启动任务');
      let process,timer,timedOut=false;
      const abort=()=>{if(process)void process.kill(0).catch(()=>{});};
      try{
        await IO.writeUTF8(join(folder,'local_pdf.py'),await env.pdfScriptText());
        if(!isPDF)await IO.writeUTF8(script,await env.imageScriptText());
        check(signal);
        process=await Subprocess.call({command:python,arguments:['-I',script,'--input',source,
          '--document',join(folder,'translated.json'),'--output',folder,'--outputs',Object.keys(paths).join(','),'--order',settings.order],
          environment:{...env.environment,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},environmentAppend:false,stderr:'pipe'});
        signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
        timer=env.setTimeout(()=>{timedOut=true;abort();},1200000);
        const [status,stdout]=await Promise.all([process.wait(),readPipe(process.stdout),readPipe(process.stderr)]);
        check(signal);if(timedOut)throw new Error(label+' 排版超时，请拆分文档');
        let result;try{result=JSON.parse(stdout.trim());}catch(_){}
        if(status.exitCode!==0 || !result?.ok){
          const labels={PDF_TEXT_DOES_NOT_FIT:'译文无法在原文区域内完整排版，请使用更精简的译文',
            PDF_SOURCE_CHANGED:'原 PDF 已变化，请重新翻译',PDF_OCR_TRANSLATION_INCOMPLETE:'图片文字译文不完整，请重新翻译',
            PDF_OVERLAPPING_TRANSLATIONS:'原文文本区域重叠，请使用新版插件重新解析并翻译',
            PDF_PROTECTED_FORMULA_OVERLAP:'译文区域与公式重叠，请检查文档版式',
            PDF_TRANSLATION_INCOMPLETE:'译文结构不完整，请重新翻译',PDF_LAYOUT_INVALID:'文档版式坐标无效，请重新解析并翻译',
            PDF_SOURCE_INVALID:'源 PDF 无效或需要密码，请检查源文件',PDF_DOCUMENT_TOO_LARGE:'文档结构超过 64 MB，请拆分文档',
            PDF_OUTPUT_INVALID:'PDF 输出无效，请检查输出目录',ModuleNotFoundError:'本地 PDF 依赖缺失，请重新安装解析器',
            PDF_OUTPUT_EXISTS:'输出文件已存在，请重新启动任务',
            IMAGE_LAYOUT_INVALID:'图片版式坐标无效，请重新解析',IMAGE_FORMAT_INVALID:'图片实际格式与扩展名不符，请更正源文件',
            IMAGE_TRANSLATION_INCOMPLETE:'图片文字译文不完整，请重新翻译',IMAGE_SOURCE_INVALID:'图片帧数与解析结果不符，请重新翻译',
            IMAGE_PIXEL_LIMIT:'图片总像素超过限制，请拆分多帧图片',IMAGE_OUTPUT_EXISTS:'输出文件已存在，请重新启动任务',
            IMAGE_OUTPUT_INVALID:'图片输出无效，请检查目录',IMAGE_DOCUMENT_TOO_LARGE:'图片结构超过 64 MB，请拆分'};
          throw new Error(label+' 生成失败：'+(labels[result?.error] || '请检查文档结构和本地依赖'));
        }
        for(const [kind,path] of Object.entries(paths)){
          if(normalize(result[kind])!==normalize(path) || !await IO.exists(path) || (await IO.stat(path)).size<20)
            throw new Error(label+' 生成结果无效');
        }
        return await publishFiles(source,paths,signal);
      }catch(error){
        if(process){try{await process.kill(0);}catch(_){}try{await process.wait();}catch(_){}}
        for(const path of Object.values(paths)){
          await IO.remove(path,{ignoreAbsent:true});await IO.remove(path+'.tmp',{ignoreAbsent:true});
        }
        if(/^(PDF|图片|翻译已取消)/.test(error.message))throw error;
        throw new Error(label+' 生成启动失败，请检查本地依赖',{cause:error});
      }finally{env.clearTimeout(timer);signal?.removeEventListener('abort',abort);}
    }
    async function exportPDF(doc,signal,settings){
      check(signal);
      if(!/\.pdf$/i.test(issued.get(outputFor(doc))))throw new Error('PDF 输入无效');
      return exportFile(doc,signal,settings);
    }
    return Object.freeze({parseDocument,assetPath,saveResult,exportPDF,exportFile});
  }
  return Object.freeze({install});
});
