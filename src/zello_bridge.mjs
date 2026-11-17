// Candidate single-account duplex transport. Not a second concurrent Zello login.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {mintToken,readOpus,audioPacket} from './zello_sender.mjs';

const output=value=>process.stdout.write(JSON.stringify(value)+'\n');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function crc(buffer){
  let value=0;
  for(const byte of buffer){value^=byte<<24;for(let bit=0;bit<8;bit++)value=(value&0x80000000)?(value<<1)^0x04c11db7:value<<1;}
  return value>>>0;
}
function page(packet,serial,sequence,granule,flags){
  const lacing=[];let remaining=packet.length;
  while(remaining>=255){lacing.push(255);remaining-=255;}lacing.push(remaining);
  if(lacing.length>255)throw Error('Packet too large');
  const header=Buffer.alloc(27+lacing.length);header.write('OggS');header[5]=flags;
  header.writeBigUInt64LE(BigInt(granule),6);header.writeUInt32LE(serial>>>0,14);
  header.writeUInt32LE(sequence,18);header[26]=lacing.length;Buffer.from(lacing).copy(header,27);
  const result=Buffer.concat([header,packet]);result.writeUInt32LE(crc(result),22);return result;
}
export function opusOgg(packets,sampleRate,packetMilliseconds,serial=1){
  if(!packets.length||![8000,12000,16000,24000,48000].includes(sampleRate))throw Error('Invalid stream');
  const head=Buffer.alloc(19);head.write('OpusHead');head[8]=1;head[9]=1;
  head.writeUInt32LE(sampleRate,12); // Zello frames have no Ogg pre-skip metadata.
  const tags=Buffer.alloc(16);tags.write('OpusTags'); // empty vendor and comment list
  const pages=[page(head,serial,0,0,2),page(tags,serial,1,0,0)];let samples=0;
  packets.forEach((packet,index)=>{
    samples+=Math.round(packetMilliseconds*48); // Ogg Opus granules always use 48kHz units.
    pages.push(page(packet,serial,index+2,samples,index===packets.length-1?4:0));
  });return Buffer.concat(pages);
}

export class Bridge {
  constructor(config,spool,nativeStreaming=false){
    this.config=config;this.spool=path.resolve(spool);this.running=true;this.nativeStreaming=nativeStreaming;
    this.socket=null;this.pending=new Map();this.streams=new Map();this.seq=2;
    this.connected=false;this.session='';this.sendChain=Promise.resolve();
    this.allowed=new Set([config.receiver].filter(Boolean).map(s=>s.toLowerCase()));
    if(!this.allowed.size||this.allowed.has(config.username.toLowerCase()))throw Error('Distinct receiver allowlist required');
    fs.mkdirSync(this.spool,{recursive:true,mode:0o700});
  }
  async request(command){
    const seq=this.seq++;
    return await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(seq);reject(Error('Zello response timeout'));},15000);
      this.pending.set(seq,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
      try{this.socket.send(JSON.stringify({...command,seq}));}
      catch(error){this.pending.delete(seq);clearTimeout(timer);reject(error);}
    });
  }
  control(message){
    if(message.seq!==undefined&&this.pending.has(message.seq)){
      const pending=this.pending.get(message.seq);this.pending.delete(message.seq);
      if(message.success===false||message.error)pending.reject(Error('Zello command rejected'));
      else pending.resolve(message);
    }
    if(message.command==='on_channel_status'&&message.channel===this.config.channel){
      this.connected=message.status==='online';output({event:'connection',online:this.connected});
    }
    if(message.command==='on_stream_start')this.start(message);
    if(message.command==='on_stream_stop')this.finish(message.stream_id);
    if(message.command==='on_error')output({event:'transport_error'});
  }
  start(message){
    if(message.channel!==this.config.channel||message.type!=='audio'||message.codec!=='opus')return;
    if(!this.allowed.has(String(message.from||'').toLowerCase())||message.call_id!==undefined)return;
    if(message.for&&message.for.toLowerCase()!==this.config.username.toLowerCase())return;
    if(!Number.isInteger(message.stream_id)||this.streams.size>=2)return;
    const header=Buffer.from(message.codec_header||'','base64');if(header.length!==4)return;
    const rate=header.readUInt16LE(0),duration=Number(message.packet_duration||header[2]*header[3]);
    if(![8000,12000,16000,24000,48000].includes(rate)||!Number.isFinite(duration)||duration<2.5||duration>120)return;
    const stream={rate,duration,from:message.from,started:Date.now(),packets:[],last:null,bad:false,bytes:0,fd:null};
    if(this.nativeStreaming){
      const key=this.session+'-'+message.stream_id;
      try{
        const files=fs.readdirSync(this.spool).map(name=>path.join(this.spool,name));
        const size=files.reduce((sum,file)=>sum+(fs.statSync(file).isFile()?fs.statSync(file).size:0),0);
        if(size>128*1024*1024||files.filter(file=>file.endsWith('.json')).length>=50)throw Error('Inbox capacity');
        stream.fd=fs.openSync(path.join(this.spool,key+'.stream'),'wx',0o600);
        fs.writeSync(stream.fd,Buffer.concat([Buffer.from('PAO1'),header]));
        const metadata=path.join(this.spool,key+'.json');
        fs.writeFileSync(metadata,JSON.stringify({id:key,sender:stream.from,created:Date.now()/1000,audio:path.join(this.spool,key+'.opus'),streaming:true}),{mode:0o600});
        output({event:'incoming_start',metadata});
      }catch{this.closeJournal(stream,true);output({event:'input_dropped',reason:'spool_failure'});return;}
    }
    stream.timer=setTimeout(()=>{this.streams.delete(message.stream_id);this.closeJournal(stream,true);output({event:'input_dropped',reason:'duration_limit'});},100000);
    this.streams.set(message.stream_id,stream);
  }
  binary(buffer){
    if(buffer.length<10||buffer[0]!==1)return;
    const id=buffer.readUInt32BE(1),index=buffer.readUInt32BE(5),stream=this.streams.get(id);
    if(!stream)return;
    if(stream.last!==null&&index<=stream.last)return;
    if(stream.last!==null&&index!==stream.last+1)stream.bad=true;
    stream.last=index;stream.bytes+=buffer.length-9;
    if(stream.bad){this.closeJournal(stream,true);return;}
    if(stream.bytes>4*1024*1024||stream.packets.length*stream.duration>90000){stream.bad=true;this.closeJournal(stream,true);return;}
    const packet=Buffer.from(buffer.subarray(9));stream.packets.push(packet);
    if(stream.fd!==null){
      try{const size=Buffer.alloc(4);size.writeUInt32LE(packet.length);fs.writeSync(stream.fd,Buffer.concat([size,packet]));}
      catch{stream.bad=true;this.closeJournal(stream,true);}
    }
  }
  closeJournal(stream,bad){
    if(stream.fd===null)return;
    try{const end=Buffer.alloc(4);end.writeUInt32LE(bad?0xffffffff:0);fs.writeSync(stream.fd,end);}catch{}
    try{fs.closeSync(stream.fd);}catch{}stream.fd=null;
  }
  finish(id){
    const stream=this.streams.get(id);if(!stream)return;
    clearTimeout(stream.timer);this.streams.delete(id);this.closeJournal(stream,stream.bad||!stream.packets.length);
    if(stream.bad||!stream.packets.length){output({event:'input_dropped',reason:'incomplete_audio'});return;}
    const key=this.session+'-'+id;
    const audio=path.join(this.spool,key+'.opus'),metadata=path.join(this.spool,key+'.json');
    const value={id:key,sender:stream.from,created:Date.now()/1000,seconds:stream.packets.length*stream.duration/1000,audio};
    try{
      const files=fs.readdirSync(this.spool).map(name=>path.join(this.spool,name));
      const size=files.reduce((sum,file)=>sum+(fs.statSync(file).isFile()?fs.statSync(file).size:0),0);
      if(size>128*1024*1024||files.filter(file=>file.endsWith('.opus')).length>=50)throw Error('Inbox capacity');
      fs.writeFileSync(audio+'.tmp',opusOgg(stream.packets,stream.rate,stream.duration,id),{mode:0o600});
      fs.renameSync(audio+'.tmp',audio);
      fs.writeFileSync(metadata+'.tmp',JSON.stringify(value),{mode:0o600});fs.renameSync(metadata+'.tmp',metadata);
      output({event:'incoming',metadata});
    }catch{output({event:'input_dropped',reason:'spool_failure'});}
  }
  async connect(){
    this.session=randomUUID();this.seq=2;this.connected=false;
    const socket=new WebSocket('wss://zello.io/ws');socket.binaryType='arraybuffer';this.socket=socket;
    socket.addEventListener('message',event=>{
      try{
        if(typeof event.data==='string')this.control(JSON.parse(event.data));
        else this.binary(Buffer.from(event.data));
      }catch{output({event:'transport_error'});}
    });
    const closed=new Promise(resolve=>socket.addEventListener('close',()=>{
      this.connected=false;
      for(const item of this.pending.values())item.reject(Error('Zello disconnected'));this.pending.clear();
      for(const stream of this.streams.values()){clearTimeout(stream.timer);this.closeJournal(stream,true);}this.streams.clear();
      output({event:'connection',online:false});resolve();
    },{once:true}));
    try{
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Connect timeout')),15000);
        socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
        socket.addEventListener('error',()=>{clearTimeout(timer);reject(Error('Connection error'));},{once:true});
      });
      if(fs.statSync(this.config.private_key).mode&0o077)throw Error('Private key mode must be 600');
      const token=mintToken(this.config.issuer,fs.readFileSync(this.config.private_key));
      await this.request({command:'logon',username:this.config.username,password:this.config.password,
        auth_token:token,channels:[this.config.channel]});
      const deadline=Date.now()+15000;
      while(!this.connected&&Date.now()<deadline&&socket.readyState===WebSocket.OPEN)await delay(100);
      if(!this.connected)throw Error('Channel offline');
      await closed;
    }finally{socket.close();}
  }
  async send(file){
    const audio=readOpus(fs.readFileSync(file));if(audio.seconds>90)throw Error('Speech too long');
    const deadline=Date.now()+120000;
    while(this.running&&(!this.connected||this.streams.size)&&Date.now()<deadline)await delay(100);
    if(!this.running||!this.connected||this.streams.size)throw Error('Channel not ready');
    const socket=this.socket;
    const started=await this.request({command:'start_stream',channel:this.config.channel,type:'audio',codec:'opus',codec_header:audio.codecHeader,packet_duration:20});
    if(!Number.isInteger(started.stream_id))throw Error('Missing stream id');
    const clock=performance.now();
    for(let index=0;index<audio.packets.length;index++){
      if(!this.running||socket.readyState!==WebSocket.OPEN||socket!==this.socket)throw Error('Disconnected during speech');
      socket.send(audioPacket(started.stream_id,0,audio.packets[index]));
      const wait=clock+(index+1)*20-performance.now();if(wait>0)await delay(wait);
    }
    socket.send(JSON.stringify({command:'stop_stream',stream_id:started.stream_id}));await delay(500);
  }
  async run(){
    let backoff=2;
    while(this.running){try{await this.connect();backoff=2;}catch{output({event:'connection',online:false});}
      if(this.running){await delay(backoff*1000);backoff=Math.min(backoff*2,30);}}
  }
  stop(){this.running=false;for(const stream of this.streams.values()){clearTimeout(stream.timer);this.closeJournal(stream,true);}this.streams.clear();this.socket?.close();}
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
const [configPath,spool,nativeFlag]=process.argv.slice(2);
if(!configPath||!spool)throw Error('Private config and spool paths required');
if(fs.statSync(configPath).mode&0o077)throw Error('Config must be private');
const bridge=new Bridge(JSON.parse(fs.readFileSync(configPath,'utf8')),spool,nativeFlag==='--native-stream');
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{
  let request;try{request=JSON.parse(line);}catch{return;}
  if(request.command!=='send'||!/^[-a-zA-Z0-9]{1,80}$/.test(request.id||''))return;
  bridge.sendChain=bridge.sendChain.then(async()=>{
    try{await bridge.send(request.file);output({id:request.id,ok:true});}
    catch{output({id:request.id,ok:false,error:'Zello transmission failed'});}
  });
});
input.on('close',()=>bridge.stop());
process.once('SIGTERM',()=>bridge.stop());process.once('SIGINT',()=>bridge.stop());
await bridge.run();input.close();

}
