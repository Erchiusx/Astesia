import test from 'node:test';
import assert from 'node:assert/strict';
import {opusOgg} from '../src/zello_bridge.mjs';
import {readOpus,audioPacket} from '../src/zello_sender.mjs';
test('Zello Opus frames survive Ogg reconstruction',()=>{
 const packets=[Buffer.from([0xf8,0xff,0xfe]),Buffer.from([0xf8,0xff,0xfe])];
 const decoded=readOpus(opusOgg(packets,48000,20,123));
 assert.equal(decoded.sampleRate,48000);assert.equal(decoded.packets.length,2);assert.deepEqual(decoded.packets,packets);
 const packet=audioPacket(23,5,packets[0]);assert.equal(packet.readUInt32BE(1),23);assert.equal(packet.readUInt32BE(5),5);
});
test('rejects truncated Ogg input',()=>assert.throws(()=>readOpus(Buffer.from('OggS'))));
