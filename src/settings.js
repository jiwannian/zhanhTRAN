function mountZhanhTRANSettings(){
 const container=document.getElementById('zhanhtran-custom-provider-root');
 if(!container)return;
 Zotero.zhanhTRAN.mount(window,container);
 window.removeEventListener('load',mountZhanhTRANSettings,true);
}
// Zotero inserts the preference fragment after evaluating its scripts.
window.addEventListener('load',mountZhanhTRANSettings,true);
mountZhanhTRANSettings();
