import {getDocument,GlobalWorkerOptions} from 'pdfjs-dist/legacy/build/pdf.mjs';
import worker from 'pdfjs-dist/legacy/build/pdf.worker.mjs?url';
GlobalWorkerOptions.workerSrc=worker;
let queue:Promise<unknown>=Promise.resolve();
export function renderCover(id:string,canvas:HTMLCanvasElement,signal:AbortSignal){
 const work=queue.then(async()=>{
  if(signal.aborted)return;
  const task=getDocument({url:`/api/paper/${encodeURIComponent(id)}/file`,withCredentials:true,
   disableAutoFetch:true,disableStream:true,maxImageSize:2000000,
   cMapUrl:'/static/workbench/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/workbench/pdfjs/standard_fonts/'});
  const cancel=()=>{void task.destroy();};signal.addEventListener('abort',cancel,{once:true});
  try{
   const pdf=await task.promise;if(signal.aborted)return;
   const page=await pdf.getPage(1),base=page.getViewport({scale:1});
   const viewport=page.getViewport({scale:Math.min(640/base.width,900/base.height)});
   canvas.width=viewport.width;canvas.height=viewport.height;
   await page.render({canvas,viewport}).promise;
  }finally{signal.removeEventListener('abort',cancel);await task.destroy();}
 });
 queue=work.catch(()=>{});return work;
}
