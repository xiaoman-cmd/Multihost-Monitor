export type User = { username: string; csrf_token: string };
export type Status = 'online' | 'offline' | 'disabled';
export type Latest = {sampled_at:number; received_at:number; cpu_percent:number; memory_percent:number; memory_used_bytes:number; memory_total_bytes:number; disk_percent:number|null; receive_rate:number|null; send_rate:number|null};
export type Host = {id:number; name:string; hostname:string; cloud_provider:string; region:string; tags:string[]; systemd_units:string[]; status:Status; last_seen_at:number|null; metadata_at:number|null; os:string|null; arch:string|null; agent_version:string|null; freshness:'none'|'stale'|'fresh'; sample_age_seconds:number|null; latest:Latest|null};
export type Overview = {server_time:number; offline_after_seconds:number; stats:{total:number;online:number;offline:number;disabled:number}; total:number; items:Host[]; next_after_id:number|null; providers:string[]};
export type Sample = {sample_id:string;sampled_at:number; cpu_percent:number;load_1:number;load_5:number;load_15:number;memory_total_bytes:number;memory_available_bytes:number;swap_total_bytes:number;swap_used_bytes:number;uptime_seconds:number; metadata:{hostname:string;os:string;arch:string;agent_version:string};disks:{mountpoint:string;total_bytes:number;used_bytes:number;available_bytes:number;inode_percent:number|null}[];networks:{interface:string;received_bytes:number;sent_bytes:number;receive_rate:number|null;send_rate:number|null}[];services:{unit:string;state:string}[]};
export type Detail = {server_time:number;host:Host;sample:Sample|null};
export type Point = {at:number;bucket_at:number;segment:number|null;first_sample_at:number|null;last_sample_at:number|null;sample_count:number;cpu_percent:number|null;memory_percent:number|null;swap_percent:number|null;memory_used_bytes:number|null;swap_used_bytes:number|null;receive_rate:number|null;send_rate:number|null};
export type History = {server_time:number;from:number;until:number;bucket_seconds:number;gap_after_seconds:number;points:Point[]};
export type Profile = {name:string;hostname:string;cloud_provider:string;region:string;tags:string[];systemd_units:string[]};
export class ApiError extends Error { constructor(public status:number) {super(status===401?'会话已过期，请重新登录。':status===409?'主机名称冲突，请更换名称后重试。':status===422?'请求超出限制或字段无效，请检查输入；历史数据过多时请选择更短范围。':status===429?'请求过于频繁，请稍后重试。':'服务请求失败，请检查连接后重试。');} }
export async function api<T>(path:string,options:RequestInit={}):Promise<T>{
  const controller=new AbortController();const abort=()=>controller.abort();
  options.signal?.addEventListener('abort',abort,{once:true});
  if(options.signal?.aborted) controller.abort();
  const timer=setTimeout(abort,15000);
  try{const response=await fetch(`/api${path}`,{...options,signal:controller.signal,credentials:'same-origin'});
    if(!response.ok) throw new ApiError(response.status);
    return response.status===204?undefined as T:await response.json() as T;
  }finally{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
}
export function write<T>(path:string,csrf:string,body?:unknown,method='POST'){
  return api<T>(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});
}
export function bytes(value:number|null|undefined):string{if(value==null)return '—';const units=['B','KiB','MiB','GiB','TiB','PiB'];let n=value;let i=0;while(Math.abs(n)>=1024&&i<units.length-1){n/=1024;i++;}return `${n.toLocaleString('zh-CN',{maximumFractionDigits:i?1:0})} ${units[i]}`;}
export function percent(value:number|null|undefined){return value==null?'—':`${value.toFixed(1)}%`;}
export function date(value:number|null|undefined,short=false){return value==null?'从未上报':new Intl.DateTimeFormat('zh-CN',{...(short?{}:{month:'2-digit',day:'2-digit'}),hour:'2-digit',minute:'2-digit',...(short?{}:{second:'2-digit'}),hour12:false}).format(value*1000);}
export function age(value:number|null,stamp:number){if(value===null)return '从未上报';const n=Math.max(0,stamp-value);return n<60?`${n} 秒前`:n<3600?`${Math.floor(n/60)} 分钟前`:n<86400?`${Math.floor(n/3600)} 小时前`:`${Math.floor(n/86400)} 天前`;}
