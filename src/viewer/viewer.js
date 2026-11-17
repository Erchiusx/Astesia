'use strict';
const nonce=document.querySelector('meta[name="agent-nonce"]').content;
const el=id=>document.getElementById(id);
let current=null,selected=null,before=null,refreshing=false,sending=false;
const retained=new Map();
const voiceDrafts=new Map(),audioUrls=new Map();
async function api(path,data){
  const response=await fetch('/api'+path,{method:data===undefined?'GET':'POST',headers:{'X-Personal-Agent-Nonce':nonce,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store'});
  const value=await response.json();if(!response.ok)throw Error(value.error||'暂时无法连接');return value;
}
function error(message){el('error').hidden=!message;el('error').textContent=message||'';}
function node(tag,text,className){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
function time(value){return value?new Date(value*1000).toLocaleString('zh-CN'):'新会话';}
function composeState(){const active=selected===current;el('continue').hidden=active;el('send').disabled=!active||sending;el('question').disabled=!active;el('title').textContent=active?'和助手的当前对话':'以前的对话';}
function render(){
  const box=el('turns');box.replaceChildren();
  const turns=[...retained.values()].sort((a,b)=>a.created-b.created||a.id.localeCompare(b.id));
  if(!turns.length)box.append(node('p','还没有消息。可以从下面开始聊天。','meta'));
  for(const turn of turns){
    const article=node('article',undefined,'turn');
    article.append(node('div',time(turn.created)+' · '+({zello_audio:'Zello 原生语音',zello:'Zello 语音',zello_review:'校对后的语音',web:'文字',shortcut:'快捷指令',text:'文字'}[turn.input_source]||'输入'),'meta'),node('div',turn.question,'question'));
    if(turn.done)article.append(node('div',turn.reply||turn.spoken_text||'没有可显示的回答','reply'));
    else article.append(node('div',turn.progress||'助手正在处理…','reply progress'));
    for(const source of turn.sources||[]){try{const url=new URL(source.url);if(!['https:','http:'].includes(url.protocol))continue;const link=node('a',source.title||'来源','source');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';article.append(link);}catch{}}
    box.append(article);
  }
  composeState();
}
async function history(older=false){
  const value=await api('/conversations/'+encodeURIComponent(selected)+(older&&before?'?before='+encodeURIComponent(before):''));
  if(!older&&retained.size===0)retained.clear();for(const turn of value.turns)retained.set(turn.id,turn);
  if(older||!before)before=value.next_before;current=value.current;el('older').hidden=!before;render();
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
    const [sessions,health,memory,voices]=await Promise.all([api('/conversations'),api('/health'),api('/memories'),api('/voice-inbox')]);
    current=sessions.current;if(!selected)selected=current;
    el('status').textContent=(health.worker_online?'助手在线':'Mac 助手暂时离线')+' · 部署时区';
    el('sessions').replaceChildren();
    for(const session of sessions.conversations){const button=node('button',time(session.updated)+' · '+session.turns+' 轮'+(session.conversation===current?' · 当前':''),session.conversation===selected?'current':'');button.onclick=async()=>{selected=session.conversation;before=null;retained.clear();try{await history();error('');}catch(e){error(e.message);}};el('sessions').append(button);}
    el('memories').replaceChildren();for(const item of memory.memories)el('memories').append(node('li',item.text));
    renderVoiceInbox(voices.messages);
    await history();error('');
  }catch(e){error(e.message);}finally{refreshing=false;}
}
el('older').onclick=()=>history(true).catch(e=>error(e.message));
el('continue').onclick=async()=>{try{await api('/conversation/select',{conversation:selected});current=selected;await refresh();}catch(e){error(e.message);}};
el('new').onclick=async()=>{try{await api('/conversation/new',{});selected=null;before=null;retained.clear();await refresh();}catch(e){error(e.message);}};
el('compose').onsubmit=async event=>{
  event.preventDefault();const text=el('question').value.trim();if(!text||sending||selected!==current)return;
  sending=true;composeState();
  try{await api('/ask',{text,conversation:selected});el('question').value='';await history();error('');}
  catch(e){error(e.message);}finally{sending=false;composeState();}
};
function renderVoiceInbox(messages){
  el('review-summary').textContent='待确认语音'+(messages.length?' · '+messages.length:'');
  if(document.activeElement?.closest('.voice-card'))return;
  const active=new Set(messages.map(item=>item.id));
  for(const [key,url] of audioUrls)if(!active.has(key)){URL.revokeObjectURL(url);audioUrls.delete(key);voiceDrafts.delete(key);}
  const box=el('voice-inbox');box.replaceChildren();
  if(!messages.length){box.append(node('p','暂时没有需要确认的转写。','meta'));return;}
  for(const item of messages){
    const card=node('section',undefined,'voice-card');card.append(node('p',time(item.created),'meta'));
    const listen=node('button','载入原音');const audio=document.createElement('audio');audio.controls=true;
    if(audioUrls.has(item.id))audio.src=audioUrls.get(item.id);
    listen.onclick=async()=>{try{
      const response=await fetch('/api/voice-inbox/'+encodeURIComponent(item.id)+'/audio',{headers:{'X-Personal-Agent-Nonce':nonce},cache:'no-store'});
      if(!response.ok)throw Error('原音暂不可用');
      if(audioUrls.has(item.id))URL.revokeObjectURL(audioUrls.get(item.id));
      const url=URL.createObjectURL(await response.blob());audioUrls.set(item.id,url);audio.src=url;
    }catch(e){error(e.message);}};
    const input=document.createElement('textarea');input.rows=3;input.maxLength=8000;input.value=voiceDrafts.get(item.id)??item.text??'';
    input.placeholder='确认或修正这段转写';input.setAttribute('aria-label','待确认的语音转写');input.oninput=()=>voiceDrafts.set(item.id,input.value);
    const submit=node('button','确认后发给助手');submit.onclick=async()=>{submit.disabled=true;try{
      await api('/voice-inbox/'+encodeURIComponent(item.id)+'/submit',{text:input.value});voiceDrafts.delete(item.id);document.activeElement?.blur();await refresh();
    }catch(e){error(e.message);}finally{submit.disabled=false;}};
    card.append(listen,audio,input,submit);box.append(card);
  }
}
refresh();setInterval(refresh,4000);
