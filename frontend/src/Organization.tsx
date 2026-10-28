import {useEffect,useState} from 'react';
import {api,Modal,Status,useResource} from './ui';
import {errorText,ApiError} from './api';
const labels:Record<string,string>={queued:'排队中',running:'处理中',completed:'已完成',reused:'已复用',
 failed:'未完成',interrupted:'结果未确认',stale:'内容已变化',cancelled:'已取消',needs_content:'内容不足',deleted:'论文已移除'};
export function OrganizationDialog({selection,onClose,onChanged}:{selection:Record<string,unknown>;onClose:()=>void;onChanged:()=>void}){
 const [preview,setPreview]=useState<any>(null),[job,setJob]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const settings=useResource<any>('/api/organization/settings',{automatic:false});
 const [automatic,setAutomatic]=useState<boolean|null>(null);
 const history=useResource<any>('/api/keywords/jobs',{jobs:[]});
 const status=useResource<any>(job?'/api/keywords/jobs/'+encodeURIComponent(job):'',null);
 useEffect(()=>{let active=true;setPreview(null);void api('/api/keywords/preview','POST',{...selection,method:'organize'})
 .then(p=>{if(active)setPreview(p);}).catch(e=>{if(active)setError(errorText(e));});return()=>{active=false;};},[selection]);
 useEffect(()=>{if(!job)return;const timer=setInterval(()=>{if(document.visibilityState==='visible'&&!status.loading)status.refresh();},3000);return()=>clearInterval(timer);},[job,status.loading]);
 useEffect(()=>{if(status.data?.status&&status.data.status!=='running'){onChanged();history.refresh();}},[status.data?.status]);
 return <Modal title="智能整理" onClose={onClose}>
  <p>先生成中文概述，再整理技术标签，最后归入研究主题。已有概述优先复用。</p>
  {!job&&<>
   <Status loading={!preview&&!error} error={error}/>
   {preview&&<><p>本次 {preview.count} 篇 · 最多 {preview.requests} 次模型请求 · 复用 {preview.items.filter((i:any)=>i.hasSummary).length} 篇概述。</p>
    <p className="muted">每次请求最长 120 秒；仅使用已有摘要或正文片段，不会自动解析或 OCR。模型调用将使用“论文问答与分析”的服务端配置。</p>
    {preview.items.some((i:any)=>i.needsContent)&&<p role="alert">部分论文缺少可用内容，请先补全信息或提供正文，再重新预检。</p>}
    <button className="primary" disabled={busy||preview.items.some((i:any)=>i.needsContent)} onClick={async()=>{setBusy(true);try{const result=await api('/api/keywords/jobs','POST',{...selection,method:'organize',previewKey:preview.previewKey});setJob(result.id);}catch(e){setError(errorText(e));}finally{setBusy(false);}}}>生成概述并整理</button>
   </>}
   <section className="detail-section"><h3>后续入库自动整理</h3>
    <label><input type="checkbox" checked={automatic??settings.data.automatic} onChange={e=>setAutomatic(e.target.checked)}/>信息补全后，自动生成中文概述并整理标签与主题</label>
    <p className="muted">启用后，后续入库或内容更新的每篇论文最多发起 2 次模型请求，已有合适概述会复用。</p>
    <button disabled={automatic===null||busy} onClick={async()=>{setBusy(true);try{await api('/api/organization/settings','PUT',{automatic});settings.refresh();setAutomatic(null);}catch(e){setError(errorText(e));}finally{setBusy(false);}}}>保存自动整理设置</button>
   </section>
   <h3>最近的智能整理</h3>
   {history.data.jobs.filter((j:any)=>j.method==='organize').slice(0,8).map((j:any)=><button className="text-button" key={j.id} onClick={()=>setJob(j.id)}>{labels[j.status]||j.status} · {j.completed}/{j.total} 篇</button>)}
  </>}
  {job&&<>
   <Status error={status.error||error} loading={status.loading&&!status.loaded}/>
   <p role="status">概述与标签：{labels[status.data?.status]||'读取进度'} · {status.data?.completed||0}/{status.data?.total||0}</p>
   {status.data?.items?.map((item:any)=>{
    const result=JSON.parse(item.result_json||'{}'),checkpoint=JSON.parse(item.checkpoint_json||'{}');
    return <div key={item.id} className="organization-item"><strong>{item.title}</strong><span>{labels[item.status]||item.status}{item.status==='running'?' · '+({summary:'生成概述',keywords:'整理标签'}[checkpoint.stage as 'summary'|'keywords']||'准备中'):''}</span>
     {item.error&&<p>{errorText(new ApiError(400,item.error))}</p>}{result.topicBatch&&<TopicProgress id={result.topicBatch} onChanged={onChanged}/>}</div>;
   })}
   {status.data?.status==='running'&&<button onClick={async()=>{try{await api('/api/keywords/jobs/'+job+'/cancel','POST',{});status.refresh();}catch(e){setError(errorText(e));}}}>停止后续处理</button>}
   {status.data?.status!=='running'&&<button onClick={()=>{setJob('');history.refresh();onChanged();}}>返回</button>}
  </>}
 </Modal>;
}
function TopicProgress({id,onChanged}:{id:string;onChanged:()=>void}){
 const task=useResource<any>('/api/topics/jobs/'+encodeURIComponent(id),null);
 useEffect(()=>{if(!task.data||task.data.status==='running'){const timer=setInterval(()=>{if(document.visibilityState==='visible')task.refresh();},3000);return()=>clearInterval(timer);}onChanged();},[task.data?.status]);
 return <p>研究主题：{labels[task.data?.status]||'排队中'}</p>;
}
