import {PDFWorker,TextLayer,getDocument,type PDFDocumentLoadingTask} from 'pdfjs-dist/legacy/build/pdf.mjs';
let worker:PDFWorker|undefined;
const tasks=new Set<PDFDocumentLoadingTask>();

// A supplied PDFWorker belongs to us. Let every document close its transport
// before terminating that worker, including active covers and loading PDFs.
export function pdfDocument(options:NonNullable<Parameters<typeof getDocument>[0]>){
 const task=getDocument({...options,worker:worker||=new PDFWorker()});
 tasks.add(task);
 const destroy=task.destroy.bind(task);
 let closing:Promise<void>|undefined;
 task.destroy=()=>closing||=(async()=>{
  try{await destroy();}finally{tasks.delete(task);TextLayer.cleanup();}
 })();
 return task;
}
window.addEventListener('ipaper-private-cache-clear',()=>{
 const previous=worker;worker=undefined;
 void Promise.allSettled([...tasks].map(task=>task.destroy())).then(()=>{
  previous?.destroy();TextLayer.cleanup();
 });
});
