import {afterEach, expect, it, vi} from 'vitest';
import {resourceRead,setResourceOwner} from './resourceRequests';
afterEach(()=>setResourceOwner(''));
it('shares in-flight reads, isolates cancellation, never caches completed data',async()=>{
 setResourceOwner('one');let complete!:(v:number)=>void;
 const load=vi.fn(()=>new Promise<number>(r=>complete=r));
 const a=new AbortController(),b=new AbortController();
 const first=resourceRead('/same',load,a.signal).catch(e=>e.name);
 const second=resourceRead('/same',load,b.signal);
 a.abort();complete(7);
 expect(await first).toBe('AbortError');expect(await second).toBe(7);expect(load).toHaveBeenCalledTimes(1);
 expect(await resourceRead('/same',async()=>8,new AbortController().signal)).toBe(8);
});
it('account changes abort old reads and cannot share them with another owner',async()=>{
 setResourceOwner('one');let oldSignal!:AbortSignal;
 const first=resourceRead('/same',signal=>{oldSignal=signal;return new Promise<number>((_,reject)=>signal.addEventListener('abort',()=>reject(new Error('old'))));},new AbortController().signal).catch(e=>e.message);
 setResourceOwner('two');expect(oldSignal.aborted).toBe(true);
 expect(await resourceRead('/same',async()=>2,new AbortController().signal)).toBe(2);expect(await first).toBe('old');
});
