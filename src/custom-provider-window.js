window.addEventListener('DOMContentLoaded',()=>{
  const api=window.arguments?.[0]?.api;
  if(api) api.mount(window);
  else document.getElementById('zhanhtran-custom-provider-root').textContent='插件尚未启动，请重新打开设置';
},{once:true});
