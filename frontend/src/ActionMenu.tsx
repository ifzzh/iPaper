import {useEffect,useLayoutEffect,useRef,useState,useId,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {Ellipsis} from 'lucide-react';
type Action={label:string;onSelect:()=>void;icon?:ReactNode;disabled?:boolean;danger?:boolean};
export function ActionMenu({label,actions}:{label:string;actions:Action[]}){
 const [open,setOpen]=useState(false),trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null),id=useId();
 useLayoutEffect(()=>{
  if(!open)return;
  const position=()=>{if(!menu.current||!trigger.current)return;const r=trigger.current.getBoundingClientRect(),m=menu.current;const top=r.bottom+8+m.offsetHeight>innerHeight?r.top-m.offsetHeight-8:r.bottom+8;m.style.left=Math.max(12,Math.min(innerWidth-m.offsetWidth-12,r.right-m.offsetWidth))+'px';m.style.top=Math.max(12,top)+'px';};
  position();menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
  return()=>{window.removeEventListener('resize',position);window.removeEventListener('scroll',position,true);};
 },[open]);
 useEffect(()=>{if(!open)return;const outside=(e:PointerEvent)=>{if(!menu.current?.contains(e.target as Node)&&!trigger.current?.contains(e.target as Node))setOpen(false);};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside);},[open]);
 const close=()=>{setOpen(false);trigger.current?.focus();};
 return <><button ref={trigger} className="icon-button action-menu-trigger" aria-label={label} title={label} aria-expanded={open} aria-haspopup="menu" aria-controls={open?id:undefined} onClick={()=>setOpen(v=>!v)} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();setOpen(true);}}}><Ellipsis size={18}/></button>
 {open&&createPortal(<div ref={menu} id={id} role="menu" aria-label={label} className="action-menu" onKeyDown={e=>{
  if(e.key==='Escape'){e.preventDefault();close();return;}if(e.key==='Tab'){setOpen(false);return;}
  const buttons=Array.from(menu.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));let index=buttons.indexOf(document.activeElement as HTMLButtonElement);
  if(!buttons.length)return;
  if(e.key==='ArrowDown')index=(index+1)%buttons.length;else if(e.key==='ArrowUp')index=(index+buttons.length-1)%buttons.length;else if(e.key==='Home')index=0;else if(e.key==='End')index=buttons.length-1;else return;e.preventDefault();buttons[index]?.focus();
 }}>{actions.map(a=><button key={a.label} role="menuitem" aria-label={a.label} className={a.danger?'danger':''} disabled={a.disabled} onClick={()=>{close();a.onSelect();}}>{a.icon}<span>{a.label}</span></button>)}</div>,document.body)}</>;
}
