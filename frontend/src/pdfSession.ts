import type {PDFDocumentProxy, PDFDocumentLoadingTask} from 'pdfjs-dist/legacy/build/pdf.mjs';
type Entry={owner:string;url:string;etag:string;size:number;doc:PDFDocumentProxy;expires:number;close:()=>Promise<void>;cleanupTimer?:ReturnType<typeof setTimeout>};
const idle:Entry[]=[];
let generation=0,timer:ReturnType<typeof setTimeout>|undefined;
const LIMIT=24*1024*1024,TTL=120000;
function destroy(entry:{close:()=>Promise<void>;cleanupTimer?:ReturnType<typeof setTimeout>}){clearTimeout(entry.cleanupTimer);void entry.close().catch(()=>{});}
export function clearPdfSession(){generation++;clearTimeout(timer);for(const entry of idle.splice(0))destroy(entry);}
if(typeof window!=='undefined')window.addEventListener('ipaper-private-cache-clear',clearPdfSession);
function trim(){
 clearTimeout(timer);
 for(let i=idle.length-1;i>=0;i--)if(idle[i].expires<=Date.now())destroy(idle.splice(i,1)[0]);
 while(idle.length>2||idle.reduce((n,e)=>n+e.size,0)>LIMIT)destroy(idle.shift()!);
 if(idle.length)timer=setTimeout(trim,Math.max(1,Math.min(...idle.map(e=>e.expires))-Date.now()));
}
export async function acquirePdf(options:{owner:string;url:string;signal:AbortSignal;open:(config:{etag:string;stream:boolean})=>PDFDocumentLoadingTask;head?:typeof fetch;onDestroyed?:()=>void}){
 const {owner,url,signal}=options,epoch=generation;
 const response=await(options.head||fetch)(url,{method:'HEAD',credentials:'same-origin',cache:'no-store',signal});
 if(!response.ok){
  for(let i=idle.length-1;i>=0;i--)if(idle[i].owner===owner&&idle[i].url===url)destroy(idle.splice(i,1)[0]);
  if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('ipaper-session-expired'));
  throw Object.assign(new Error('pdf_unavailable'),{status:response.status});
 }
 if(signal.aborted||epoch!==generation)throw new DOMException('Aborted','AbortError');
 const etag=response.headers.get('ETag')||'',size=Number(response.headers.get('Content-Length'));
 trim();let doc:PDFDocumentProxy|undefined,close:()=>Promise<void>=async()=>{};
 for(let i=idle.length-1;i>=0;i--){
  const entry=idle[i];if(entry.owner!==owner||entry.url!==url)continue;
  idle.splice(i,1);
  clearTimeout(entry.cleanupTimer);
  if(entry.etag===etag&&etag){doc=entry.doc;close=entry.close;}else destroy(entry);
 }
 const reused=!!doc;
 if(!doc){
  // Small, explicitly opened PDFs stream in one request; covers still use ranges.
  const task=options.open({etag,stream:size>0&&size<=12*1024*1024});
  close=async()=>{try{await task.destroy();}finally{options.onDestroyed?.();}};
  const abort=()=>{void task.destroy().catch(()=>{});};signal.addEventListener('abort',abort,{once:true});
  try{doc=await task.promise;}catch(error){await task.destroy().catch(()=>{});throw error;}
  finally{signal.removeEventListener('abort',abort);}
 }
 if(signal.aborted||epoch!==generation){destroy({close});throw new DOMException('Aborted','AbortError');}
 let released=false;
 return {doc,reused,release(){
  if(released)return;released=true;
  if(epoch!==generation||!owner||!etag||!Number.isFinite(size)||size<=0||size>LIMIT||doc!.numPages>400){destroy({close});return;}
  const entry:Entry={owner,url,etag,size,doc:doc!,close,expires:Date.now()+TTL};idle.push(entry);
  entry.cleanupTimer=setTimeout(()=>{if(epoch===generation&&idle.includes(entry)){void entry.doc.cleanup().catch(()=>{});trim();}},10000);
  trim();
 }};
}
