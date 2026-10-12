/* Decode bounded provider responses without exposing upstream bodies or secrets. */
(function(root,factory){
  if(typeof module==='object' && module.exports) module.exports=factory(require('./vendor/eventsource-parser.js'));
  else root.ZhanhTRANProviderResponse=factory(root.ZhanhTRANSSE);
})(typeof _globalThis!=='undefined'?_globalThis:globalThis,function(SSE){
  'use strict';
  const LIMIT=2*1024*1024;
  function diagnostic(res,bytes){
    const raw=(res.headers?.get('content-type') || '').split(';')[0].toLowerCase();
    const type=['application/json','text/event-stream','text/html'].includes(raw)?raw:'other';
    const status=Number.isInteger(res.status)?res.status:0;
    return '（HTTP '+status+'；'+type+'；'+bytes+' bytes）';
  }
  async function bodyText(res,signal){
    const reader=res.body?.getReader?.();
    if(!reader){
      const text=await res.text();
      if(new TextEncoder().encode(text).length>LIMIT)throw new Error('供应商响应过大');
      return text;
    }
    let bytes=0;let complete=false;const parts=[];const decoder=new TextDecoder('utf-8',{fatal:true});
    const cancel=()=>{void reader.cancel().catch(()=>{});};
    signal?.addEventListener('abort',cancel,{once:true});
    try{
      if(signal?.aborted)throw new Error('翻译已取消');
      while(true){
        const {done,value}=await reader.read();
        if(signal?.aborted)throw new Error('翻译已取消');
        if(done){complete=true;break;}
        bytes+=value.byteLength;
        if(bytes>LIMIT)throw new Error('供应商响应过大');
        parts.push(decoder.decode(value,{stream:true}));
      }
      parts.push(decoder.decode());return parts.join('');
    }catch(error){
      if(signal?.aborted)throw new Error('翻译已取消');
      if(error.message==='供应商响应过大')throw error;
      throw Object.assign(new Error('供应商响应读取失败，请检查网络后重试'+diagnostic(res,bytes)),{retryable:true});
    }finally{
      signal?.removeEventListener('abort',cancel);
      if(!complete)cancel();reader.releaseLock();
    }
  }
  function fragments(value){
    if(typeof value==='string')return value;
    if(Array.isArray(value))return value.filter(x=>x.type==='text').map(x=>x.text || '').join('');
    return '';
  }
  function checkFinish(protocol,reason){
    if(reason==null)return;
    if(reason==='length' || reason==='max_tokens')throw new Error('模型输出被截断，请减少分块长度');
    const allowed=protocol==='anthropic'?['end_turn','stop_sequence']:['stop'];
    if(!allowed.includes(reason))throw new Error('模型输出未正常完成，请检查模型与内容限制');
  }
  function streamData(text,protocol,detail){
    const pieces=[];let terminal=false;let finish=null;
    function append(content){
      if(finish && content)throw new Error('供应商 SSE 结束后仍返回正文'+detail);
      pieces.push(content);
    }
    const parser=SSE.createParser({maxBufferSize:LIMIT,onEvent(event){
      if(terminal)throw new Error('供应商 SSE 结束后仍返回数据'+detail);
      if(event.data==='[DONE]'){terminal=true;return;}
      let data;
      try{data=JSON.parse(event.data);}catch(_){throw new Error('供应商 SSE 数据不是有效 JSON'+detail);}
      if(data?.error || data?.type==='error')throw new Error('供应商返回流式错误，请检查模型与配额'+detail);
      if(protocol==='anthropic'){
        if(data?.type==='content_block_start' && data.content_block?.type==='text')append(data.content_block.text || '');
        if(data?.type==='content_block_delta' && data.delta?.type==='text_delta')append(data.delta.text || '');
        if(data?.type==='message_delta' && data.delta?.stop_reason){
          checkFinish(protocol,data.delta.stop_reason);finish=data.delta.stop_reason;
        }
        if(data?.type==='message_stop')terminal=true;
      }else{
        const choice=data?.choices?.find(x=>(x.index ?? 0)===0);
        if(choice){
          append(fragments(choice.delta?.content ?? choice.message?.content));
          if(choice.finish_reason){checkFinish(protocol,choice.finish_reason);finish=choice.finish_reason;}
        }
      }
    },onError(error){
      if(error.type==='max-buffer-size-exceeded')throw new Error('供应商响应过大');
      throw new Error('供应商 SSE 含无效字段'+detail);
    }});
    parser.feed(text.replace(/\r\n?/g,'\n'));
    const ended=terminal;
    // Flush EOF solely to reject unconsumed data; it cannot complete the response.
    parser.feed('\n\n');
    if(!ended || !finish)throw new Error('供应商 SSE 响应未完整结束，请重试'+detail);
    const content=pieces.join('');
    return protocol==='anthropic'?{content:[{type:'text',text:content}],stop_reason:finish}:
      {choices:[{message:{content},finish_reason:finish}]};
  }
  async function read(res,protocol,signal){
    // Plain JSON-only response doubles are retained for existing host-independent tests.
    if(!res.text && !res.body){
      try{return await res.json();}catch(_){throw new Error('供应商响应不是有效 JSON'+diagnostic(res,0));}
    }
    let text;
    try{text=(await bodyText(res,signal)).trimStart();}
    catch(error){if(/^(供应商|翻译已取消)/.test(error.message))throw error;
      throw new Error('供应商响应读取失败，请检查网络后重试'+diagnostic(res,0));}
    const detail=diagnostic(res,new TextEncoder().encode(text).length);
    if(/^</.test(text))throw new Error('供应商返回 HTML/XML 页面，请检查 Base URL 或网关状态'+detail);
    // Some nonstream gateways prepend SSE comments to a final JSON envelope.
    const jsonText=text.replace(/^(?:(?::[^\r\n]*(?:\r\n|\r|\n))|(?:\r\n|\r|\n))*/,'').trimStart();
    if(!jsonText.trim())throw new Error('供应商返回空响应，请重试'+detail);
    let data;
    try{data=JSON.parse(jsonText);}catch(_){
      if(/^(?:data:|event:|id:|retry:|:)/.test(text) ||
        /^text\/event-stream(?:;|$)/i.test(res.headers?.get('content-type') || ''))return streamData(text,protocol,detail);
      throw new Error('供应商响应不是有效 JSON（可能被网关截断）'+detail);
    }
    if(data?.error)throw new Error('供应商返回错误，请检查密钥、模型与配额'+detail);
    return data;
  }
  return Object.freeze({read,checkFinish});
});
