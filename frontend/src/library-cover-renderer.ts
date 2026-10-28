import {getDocument,GlobalWorkerOptions,PDFWorker} from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.mjs?url';
GlobalWorkerOptions.workerSrc=workerUrl;
let queue:Promise<unknown>=Promise.resolve();
let worker:PDFWorker|undefined;
// Rendered pixels only, bounded to 12 MiB. Reuse requires a successful
// authenticated HEAD with matching ETag. Nothing goes to persistent storage.
const cache=new Map<string,{etag:string;canvas:HTMLCanvasElement}>();
let bytes=0,epoch=0;
window.addEventListener('ipaper-private-cache-clear',()=>{
 epoch++;cache.clear();bytes=0;void worker?.destroy();worker=undefined;
});
export function renderCover(id:string,canvas:HTMLCanvasElement,signal:AbortSignal){
 const generation=epoch;
 const work=queue.then(async()=>{
  if(signal.aborted||generation!==epoch)return;
  const url=`/api/paper/${encodeURIComponent(id)}/file`;
  const head=await fetch(url,{method:'HEAD',credentials:'same-origin',signal,cache:'no-store'});
  if(!head.ok)throw new Error('cover_unavailable');
  if(signal.aborted||generation!==epoch)return;
  const etag=head.headers.get('ETag'),cached=cache.get(id);
  if(etag&&cached?.etag===etag){
   cache.delete(id);cache.set(id,cached);
   canvas.width=cached.canvas.width;canvas.height=cached.canvas.height;
   canvas.getContext('2d')!.drawImage(cached.canvas,0,0);return;
  }
  if(cached){bytes-=cached.canvas.width*cached.canvas.height*4;cache.delete(id);}
  const task=getDocument({url,worker:worker||=new PDFWorker(),withCredentials:true,
   disableAutoFetch:true,disableStream:true,rangeChunkSize:262144,maxImageSize:2000000,
   cMapUrl:'/static/workbench/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/workbench/pdfjs/standard_fonts/'});
  const cancel=()=>{void task.destroy();};signal.addEventListener('abort',cancel,{once:true});
  try{
   const pdf=await task.promise;if(signal.aborted||generation!==epoch)return;
   const page=await pdf.getPage(1),base=page.getViewport({scale:1});
   const viewport=page.getViewport({scale:Math.min(640/base.width,900/base.height)});
   canvas.width=viewport.width;canvas.height=viewport.height;
   await page.render({canvas,viewport}).promise;
   if(etag&&!signal.aborted&&generation===epoch){
    const copy=document.createElement('canvas');copy.width=canvas.width;copy.height=canvas.height;
    copy.getContext('2d')!.drawImage(canvas,0,0);
    cache.set(id,{etag,canvas:copy});bytes+=copy.width*copy.height*4;
    while(bytes>12*1024*1024&&cache.size){const key=cache.keys().next().value!;const old=cache.get(key)!;bytes-=old.canvas.width*old.canvas.height*4;cache.delete(key);}
   }
  }finally{signal.removeEventListener('abort',cancel);await task.destroy();}
 });
 queue=work.catch(()=>{});return work;
}
