// Test-only sender using the official Zello Channel API. No agent data is sent.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
import {sign} from 'node:crypto';

export function mintToken(issuer,privateKey,now=Math.floor(Date.now()/1000)) {
  if(typeof issuer!=='string' || issuer.trim().length<8)throw Error('Missing Zello Issuer');
  const header=Buffer.from(JSON.stringify({alg:'RS256',typ:'JWT'})).toString('base64url');
  const claims=Buffer.from(JSON.stringify({iss:issuer.trim(),exp:now+120})).toString('base64url');
  const input=header+'.'+claims;
  return input+'.'+sign('RSA-SHA256',Buffer.from(input),privateKey).toString('base64url');
}

export function readOpus(buffer) {
  let offset=0, current=[], length=0, packets=[];
  while(offset<buffer.length) {
    if(offset+27>buffer.length || buffer.toString('ascii',offset,offset+4)!=='OggS' || buffer[offset+4]!==0) throw Error('Invalid Ogg page');
    const count=buffer[offset+26], table=offset+27;
    if(table+count>buffer.length) throw Error('Truncated Ogg header');
    offset=table+count;
    for(let i=0;i<count;i++) {
      const size=buffer[table+i];
      if(offset+size>buffer.length) throw Error('Truncated Ogg segment');
      current.push(buffer.subarray(offset,offset+size));length+=size;offset+=size;
      if(size<255) {packets.push(Buffer.concat(current,length));current=[];length=0;}
    }
  }
  if(current.length || packets.length<3 || packets[0].toString('ascii',0,8)!=='OpusHead' || packets[1].toString('ascii',0,8)!=='OpusTags') throw Error('Invalid Opus headers');
  if(packets[0][9]!==1) throw Error('Mono Opus audio required');
  const sampleRate=packets[0].readUInt32LE(12);
  if(![8000,12000,16000,24000,48000].includes(sampleRate)) throw Error('Unsupported sample rate');
  packets=packets.slice(2);
  for(const p of packets) {
    if(!p.length) throw Error('Empty Opus packet');
    const config=p[0]>>3, frames=p[0]&3;
    const milliseconds=config>=16 ? 2.5*(2**(config&3)) : config>=12 ? 10*(2**(config&1)) : [10,20,40,60][config&3];
    if(frames!==0 || milliseconds!==20) throw Error('Encode with one 20 ms frame per packet');
  }
  const header=Buffer.alloc(4);header.writeUInt16LE(sampleRate);header[2]=1;header[3]=20;
  return {packets,sampleRate,codecHeader:header.toString('base64'),seconds:packets.length*.02};
}

export function audioPacket(streamId,index,bytes) {
  const header=Buffer.alloc(9);header[0]=1;header.writeUInt32BE(streamId,1);header.writeUInt32BE(index,5);
  return Buffer.concat([header,bytes]);
}

async function send(config,audio) {
  if(!config.username || !config.password || !config.channel) throw Error('Missing local Zello configuration');
  let token=config.token;
  if(config.private_key && config.issuer) {
    if(fs.statSync(config.private_key).mode&0o077)throw Error('Private key file must have permissions 600');
    token=mintToken(config.issuer,fs.readFileSync(config.private_key));
  }
  if(!token)throw Error('Missing Zello Issuer/private key or test token');
  const ws=new WebSocket('wss://zello.io/ws');
  const queue=[],waiters=[];
  let closed=false;
  ws.addEventListener('message',event=>{
    if(typeof event.data!=='string') return;
    try {const data=JSON.parse(event.data);if(waiters.length)waiters.shift()(data);else queue.push(data);}catch{}
  });
  ws.addEventListener('close',()=>{closed=true;for(const wake of waiters.splice(0))wake(null);});
  async function receive() {
    if(queue.length)return queue.shift();
    if(closed)throw Error('Zello connection closed');
    let timer, wake;
    try {
      const value=await Promise.race([new Promise(resolve=>{wake=resolve;waiters.push(wake);}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Zello response timed out')),15000);})]);
      if(value===null)throw Error('Zello connection closed');
      return value;
    } finally {clearTimeout(timer);const i=waiters.indexOf(wake);if(i>=0)waiters.splice(i,1);}
  }
  try {
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{ws.close();reject(Error('Zello connect timed out'));},15000);
      ws.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
      ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('Zello TLS/WebSocket connection failed'));},{once:true});
    });
    ws.send(JSON.stringify({command:'logon',seq:1,username:config.username,password:config.password,auth_token:token,channels:[config.channel]}));
    let authenticated=false,online=false;
    while(!authenticated || !online) {
      const d=await receive();
      if(d.seq===1) {if(!d.success)throw Error('Zello authentication rejected');authenticated=true;}
      if(d.command==='on_channel_status' && d.channel===config.channel) {
        if(d.status!=='online')throw Error('Zello channel is unavailable or account is not authorized');online=true;
      }
    }
    console.log('Zello sender authenticated; channel online.');
    if(audio===null) {
      console.log('Temporary receiver presence is online for up to 3 minutes. On the phone, use the channel owner account to Trust this user. Press Ctrl+C after approval, before signing this receiver into the phone.');
      await new Promise(resolve=>{
        let timer;
        const finish=()=>{clearTimeout(timer);process.removeListener('SIGINT',finish);ws.removeEventListener('close',finish);resolve();};
        timer=setTimeout(finish,180000);
        process.once('SIGINT',finish);ws.addEventListener('close',finish,{once:true});
      });
      return;
    }
    ws.send(JSON.stringify({command:'start_stream',seq:2,channel:config.channel,type:'audio',codec:'opus',codec_header:audio.codecHeader,packet_duration:20}));
    let stream;
    while(stream===undefined) {
      const d=await receive();
      if(d.seq===2) {if(!d.success || !Number.isInteger(d.stream_id))throw Error('Zello audio stream rejected');stream=d.stream_id;}
    }
    const started=performance.now();
    for(let i=0;i<audio.packets.length;i++) {
      if(ws.readyState!==WebSocket.OPEN)throw Error('Disconnected during audio');
      ws.send(audioPacket(stream,i,audio.packets[i]));
      const delay=started+(i+1)*20-performance.now();
      if(delay>0)await new Promise(resolve=>setTimeout(resolve,delay));
    }
    ws.send(JSON.stringify({command:'stop_stream',stream_id:stream}));
    await new Promise(resolve=>setTimeout(resolve,500));
    console.log('Test audio sent; phone playback still needs user confirmation.');
  } finally {ws.close();}
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const [mode,filename,configPath='zello-config.json']=process.argv.slice(2);
    if(mode==='presence') {
      // Helper passes ephemeral credentials on stdin, never argv or a disk file.
      await send(JSON.parse(fs.readFileSync(0,'utf8')),null);
      process.exit(0);
    }
    if(!['inspect','send-test','send-audio'].includes(mode) || !filename)throw Error('Usage: node zello_sender.mjs inspect|send-test|send-audio AUDIO.opus [CONFIG.json]');
    const audio=readOpus(fs.readFileSync(filename));
    console.log(JSON.stringify({sampleRate:audio.sampleRate,packets:audio.packets.length,seconds:audio.seconds}));
    if(mode==='send-test' || mode==='send-audio') {
      if(audio.seconds>(mode==='send-test'?30:90))throw Error('Audio exceeds the allowed duration');
      if(fs.statSync(configPath).mode&0o077)throw Error('Credential file must be private: chmod 600');
      await send(JSON.parse(fs.readFileSync(configPath,'utf8')),audio);
    }
  }catch(error){console.error(error.message);process.exitCode=1;}
}
