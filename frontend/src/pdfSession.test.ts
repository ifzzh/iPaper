import {afterEach,expect,it,vi} from 'vitest';
import {acquirePdf,clearPdfSession} from './pdfSession';
import type {PDFDocumentLoadingTask,PDFDocumentProxy} from 'pdfjs-dist/legacy/build/pdf.mjs';
afterEach(()=>{clearPdfSession();vi.useRealTimers();});
function fixture(){
 const close=vi.fn(async()=>{}),cleanup=vi.fn(async()=>{});
 const doc={numPages:22,cleanup} as unknown as PDFDocumentProxy;
 const open=vi.fn((_config:{etag:string;stream:boolean})=>({promise:Promise.resolve(doc),destroy:close}) as unknown as PDFDocumentLoadingTask);
 let etag='"one"',status=200;
 const head=vi.fn(async()=>new Response(null,{status,headers:{ETag:etag,'Content-Length':'1000000'}})) as unknown as typeof fetch;
 return {close,open,head,etag:(value:string)=>etag=value,status:(value:number)=>status=value};
}
it('reopening reauthenticates but avoids a second parse, and validates changes',async()=>{
 const f=fixture(),options={owner:'one',url:'/file',signal:new AbortController().signal,open:f.open,head:f.head};
 const a=await acquirePdf(options);expect(a.reused).toBe(false);a.release();
 const b=await acquirePdf(options);expect(b.reused).toBe(true);expect(f.open).toHaveBeenCalledTimes(1);expect(f.head).toHaveBeenCalledTimes(2);b.release();
 f.etag('"changed"');const c=await acquirePdf(options);expect(c.reused).toBe(false);expect(f.close).toHaveBeenCalledTimes(1);expect(f.open).toHaveBeenCalledTimes(2);c.release();
});
it('denied permission cannot reuse an old document; owners and logout are isolated',async()=>{
 const f=fixture(),base={url:'/file',signal:new AbortController().signal,open:f.open,head:f.head};
 (await acquirePdf({...base,owner:'a'})).release();
 const b=await acquirePdf({...base,owner:'b'});expect(b.reused).toBe(false);b.release();
 f.status(403);await expect(acquirePdf({...base,owner:'a'})).rejects.toMatchObject({status:403});
 expect(f.close).toHaveBeenCalledTimes(1);clearPdfSession();expect(f.close).toHaveBeenCalledTimes(2);
});
it('does not retain oversized PDFs or expired documents',async()=>{
 vi.useFakeTimers();const f=fixture(),base={owner:'a',url:'/file',signal:new AbortController().signal,open:f.open,head:f.head};
 (await acquirePdf(base)).release();await vi.advanceTimersByTimeAsync(120001);expect(f.close).toHaveBeenCalledTimes(1);
 const large=async()=>new Response(null,{headers:{ETag:'"big"','Content-Length':String(32*1024*1024)}});
 const a=await acquirePdf({...base,head:large as typeof fetch});a.release();expect(f.close).toHaveBeenCalledTimes(2);
 expect(f.open.mock.calls.at(-1)?.[0]).toMatchObject({stream:false});
});

it('evicts the third idle document immediately, not after a cleanup timeout',async()=>{
 const f=fixture(),base={owner:'a',signal:new AbortController().signal,open:f.open,head:f.head};
 for(const url of ['/one','/two','/three'])(await acquirePdf({...base,url})).release();
 expect(f.close).toHaveBeenCalledTimes(1);
 const cached=await acquirePdf({...base,url:'/two'});expect(cached.reused).toBe(true);cached.release();
 const evicted=await acquirePdf({...base,url:'/one'});expect(evicted.reused).toBe(false);evicted.release();
});

it('an owner change while HEAD is pending cannot revive an old lease',async()=>{
 const f=fixture();let finish!:(r:Response)=>void;
 const head=vi.fn(()=>new Promise<Response>(resolve=>finish=resolve)) as unknown as typeof fetch;
 const pending=acquirePdf({owner:'a',url:'/file',signal:new AbortController().signal,open:f.open,head});
 clearPdfSession();finish(new Response(null,{headers:{ETag:'"one"','Content-Length':'1000000'}}));
 await expect(pending).rejects.toMatchObject({name:'AbortError'});expect(f.open).not.toHaveBeenCalled();
});
