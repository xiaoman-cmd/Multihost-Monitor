import {useEffect,useRef,ReactNode} from 'react';
import {Status} from './api';
const paths={server:'M4 3h16v7H4zM4 14h16v7H4zM7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6',grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',list:'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',plus:'M12 4v16M4 12h16',search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',refresh:'M21 11a9 9 0 1 0-2 7M21 3v8h-8',back:'M19 12H5M12 5l-7 7 7 7',close:'M5 5l14 14M19 5L5 19',cpu:'M7 7h10v10H7zM3 9h4M3 15h4M17 9h4M17 15h4M9 3v4M15 3v4M9 17v4M15 17v4',activity:'M2 12h5l3-8 4 16 3-8h5',key:'M15 7a4 4 0 1 1-4 4L3 19v3h3v-3h3l4-4',edit:'M14 5l5 5M4 20l1-6L16 3l5 5L10 19z',shield:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',clock:'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',network:'M4 17h16M12 5v12M8 9l4-4 4 4M8 21h8'};
export function Icon({name,className=''}:{name:keyof typeof paths;className?:string}){return <svg className={`icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;}
export function Brand(){return <a href="#/" className="brand"><span className="brand-mark"><Icon name="activity"/></span><span>MultiHost<span className="brand-sub">MONITOR</span></span></a>;}
export function Badge({status}:{status:Status}){return <span className={`badge status-${status}`}><span aria-hidden="true"/>{status==='online'?'在线':status==='disabled'?'已禁用':'离线'}</span>;}
export function ErrorNotice({message,retry}:{message:string;retry?:()=>void}){return <div className="notice danger" role="alert"><span>{message}</span>{retry&&<button className="btn small" onClick={retry}>重试</button>}</div>;}
export function Modal({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}){
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const dialog=ref.current!;const previous=document.activeElement as HTMLElement|null;
    if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
    dialog.querySelector<HTMLElement>('input,button,textarea')?.focus();
    return()=>{if(typeof dialog.close==='function')dialog.close();previous?.focus();};},[]);
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===event.currentTarget)onClose();}}><div className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label={`关闭${title}`}><Icon name="close"/></button></div>{children}</dialog>;
}
