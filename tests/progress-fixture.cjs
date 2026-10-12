'use strict';
module.exports=function progressFixture(state) {
 return (_owner,controller,onClosed)=>{
  if(state.progressError)throw new Error('fixture progress unavailable');
  const message={text:'',percent:null,finished:false,closed:false,
   setText(text){this.text=text;},setProgress(percent){this.percent=percent;},
   finish(){this.finished=true;},close(){if(this.closed)return;this.closed=true;if(!this.finished)controller.abort();onClosed?.();}};
  (state.messages ||= []).push(message);return message;
 };
};
