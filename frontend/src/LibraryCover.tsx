import {useEffect,useRef,useState} from 'react';
import {FileText} from 'lucide-react';
export function LibraryCover({id,title}:{id:string;title:string}){
 const host=useRef<HTMLDivElement>(null),canvas=useRef<HTMLCanvasElement>(null);
 const [status,setStatus]=useState('waiting');
 useEffect(()=>{
  const abort=new AbortController();setStatus('waiting');
  const observer=new IntersectionObserver(entries=>{
   if(!entries.some(e=>e.isIntersecting))return;observer.disconnect();
   setStatus('loading');
   void import('./library-cover-renderer').then(m=>m.renderCover(id,canvas.current!,abort.signal))
    .then(()=>{if(!abort.signal.aborted)setStatus('ready');})
    .catch(()=>{if(!abort.signal.aborted)setStatus('failed');});
  },{rootMargin:'120px'});
  if(host.current)observer.observe(host.current);
  return()=>{observer.disconnect();abort.abort();};
 },[id]);
 return <div ref={host} className={'library-cover '+status} aria-label={title+' 原文首页预览'}>
  <canvas ref={canvas} aria-hidden="true"/>
  {status!=='ready'&&<div className="library-cover-placeholder"><FileText size={28}/><span>{status==='failed'?'暂无预览，可打开原文':status==='loading'?'正在读取首页':'原文 PDF'}</span></div>}
 </div>;
}
