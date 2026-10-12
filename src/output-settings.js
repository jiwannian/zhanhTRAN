(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;else root.ZhanhTRANOutput=api;
})(typeof _globalThis!=='undefined'?_globalThis:globalThis,function(){
 'use strict';
 const DEFAULT_OUTPUT=Object.freeze({translated:true,bilingual:true,order:'original-left'});
 function outputOptions(raw){
  if(raw===undefined||raw===null||raw==='')return {...DEFAULT_OUTPUT};
  const data=typeof raw==='string'?JSON.parse(raw):raw;
  if(typeof data?.translated!=='boolean'||typeof data?.bilingual!=='boolean'||
    !['original-left','translated-left'].includes(data.order))throw new Error('输出设置无效');
  if(!data.translated&&!data.bilingual)throw new Error('请至少选择译文文件或双语文件');
  return Object.freeze({translated:data.translated,bilingual:data.bilingual,order:data.order});
 }
 return Object.freeze({outputOptions,DEFAULT_OUTPUT});
});
