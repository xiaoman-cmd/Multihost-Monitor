import {FormEvent,useRef,useState} from 'react';
import {ApiError,Host,Profile,write} from './api';
import {ErrorNotice,Modal} from './ui';
export default function HostForm({host,csrf,onClose,onSaved,onExpired}:{host:Host|null;csrf:string;onClose:()=>void;onSaved:(id:number,token?:string,units?:string[])=>void;onExpired:()=>void}){
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const errorRef=useRef<HTMLDivElement>(null);
  async function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();const form=new FormData(event.currentTarget);
    const list=(name:string)=>String(form.get(name)||'').split(/[,，\n]/).map(x=>x.trim()).filter(Boolean);
    const payload:Profile={name:String(form.get('name')).trim(),hostname:String(form.get('hostname')).trim(),cloud_provider:String(form.get('cloud_provider')).trim(),region:String(form.get('region')).trim(),tags:list('tags'),systemd_units:list('systemd_units')};
    let invalid='';if(payload.tags.length>32||payload.tags.some(x=>x.length>64)||new Set(payload.tags).size!==payload.tags.length)invalid='标签最多 32 个，每个不超过 64 字符，不能重复。';
    if(payload.systemd_units.length>32||new Set(payload.systemd_units).size!==payload.systemd_units.length||payload.systemd_units.some(x=>!(/^[A-Za-z0-9_:@.\-]{1,120}\.service$/).test(x)))invalid='服务白名单最多 32 个不重复的 .service 名称，不允许路径或 Shell 命令。';
    if(!payload.name||!payload.hostname)invalid='主机名称和初始 hostname 不能为空。';
    if(invalid){setError(invalid);setTimeout(()=>errorRef.current?.focus(),0);return;}
    setBusy(true);setError('');try{
      if(host){await write(`/v1/hosts/${host.id}`,csrf,payload,'PUT');onSaved(host.id);}
      else{const created=await write<{host:{id:number};agent_token:string}>('/v1/hosts',csrf,payload);onSaved(created.host.id,created.agent_token,payload.systemd_units);}
    }catch(e){if(e instanceof ApiError&&e.status===401){onExpired();return;}setError(e instanceof ApiError?e.message:'无法保存，请检查连接后重试。');setTimeout(()=>errorRef.current?.focus(),0);}finally{setBusy(false);}
  }
  return <Modal title={host?'编辑主机':'创建主机'} onClose={onClose}><form onSubmit={submit} className="modal-body form-stack" aria-busy={busy}>
    <p className="muted text-sm">{host?'修改管理信息；实际系统信息由 Agent 上报。':'创建后获得只展示一次的专用 Agent Token。'}</p>
    {error&&<div ref={errorRef} tabIndex={-1}><ErrorNotice message={error}/></div>}
    <div className="form-row"><div><label htmlFor="host-name">主机名称 *</label><input id="host-name" name="name" required maxLength={128} defaultValue={host?.name} placeholder="例如：Oracle 主节点"/></div><div><label htmlFor="host-hostname">初始 hostname *</label><input id="host-hostname" name="hostname" required maxLength={255} defaultValue={host?.hostname} placeholder="例如：oracle-node-01"/></div></div>
    <div className="form-row"><div><label htmlFor="host-provider">云厂商</label><input id="host-provider" name="cloud_provider" maxLength={64} defaultValue={host?.cloud_provider} placeholder="Oracle Cloud / Google Cloud"/></div><div><label htmlFor="host-region">地区</label><input id="host-region" name="region" maxLength={64} defaultValue={host?.region} placeholder="例如：us-west-1"/></div></div>
    <div><label htmlFor="host-tags">标签</label><input id="host-tags" name="tags" defaultValue={host?.tags.join(', ')} aria-describedby="tags-help" placeholder="production, api"/><p id="tags-help" className="help">逗号分隔，最多 32 个。标签只是管理信息，不影响采集权限。</p></div>
    <div><label htmlFor="host-services">systemd 服务白名单</label><textarea id="host-services" name="systemd_units" rows={4} defaultValue={host?.systemd_units.join('\n')} aria-describedby="services-help" placeholder="ssh.service"/><p id="services-help" className="help">每行一个 .service；Agent 还需在本地允许该服务。仅读状态，不执行命令或重启。</p></div>
    <div className="modal-actions"><button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button><button className="btn primary" disabled={busy}>{busy?'正在保存…':host?'保存更改':'创建主机并签发 Token'}</button></div>
  </form></Modal>;
}
