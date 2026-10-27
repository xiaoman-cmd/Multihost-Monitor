import {FormEvent,useCallback,useEffect,useState} from 'react';
import {ApiError,Host,User,api,write} from './api';
import Dashboard from './Dashboard';
import HostDetail,{Action} from './HostDetail';
import HostForm from './HostForm';
import {InstallGuide,TokenDialog} from './InstallGuide';
import {Brand,ErrorNotice,Icon,Modal} from './ui';
function route(){const value=window.location.hash;const match=value.match(/^#\/hosts\/([1-9]\d*)$/);return match?{kind:'detail' as const,id:Number(match[1])}:{kind:value==='#/manage'?'manage' as const:'overview' as const,id:0};}
export default function App(){
  const [user,setUser]=useState<User|null>(null);const [loading,setLoading]=useState(true);const [sessionError,setSessionError]=useState(false);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const [current,setCurrent]=useState(route);const [refreshing,setRefreshing]=useState(true);const [revision,setRevision]=useState(0);
  const [editor,setEditor]=useState<Host|null|undefined>(undefined);const [guide,setGuide]=useState<Host|null>(null);const [token,setToken]=useState<{id:number;value:string;units?:string[]}|null>(null);const [action,setAction]=useState<{host:Host;kind:Action}|null>(null);const [actionError,setActionError]=useState('');const [message,setMessage]=useState('');
  const clearSensitive=useCallback(()=>{setToken(null);setEditor(undefined);setGuide(null);setAction(null);},[]);
  const expired=useCallback(()=>{clearSensitive();setUser(null);setError('会话已过期，请重新登录。');},[clearSensitive]);
  async function restore(){setLoading(true);setSessionError(false);try{setUser(await api<User>('/auth/me'));}catch(e){if(!(e instanceof ApiError&&e.status===401))setSessionError(true);}finally{setLoading(false);}}
  useEffect(()=>{void restore();},[]);
  useEffect(()=>{const changed=()=>{setCurrent(route());clearSensitive();setError('');document.querySelector<HTMLElement>('main')?.focus({preventScroll:true});window.scrollTo(0,0);};window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);},[clearSensitive]);
  async function login(event:FormEvent<HTMLFormElement>){event.preventDefault();setBusy(true);setError('');const form=new FormData(event.currentTarget);
    try{setUser(await api<User>('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.get('username'),password:form.get('password')})}));}
    catch(e){setError(e instanceof ApiError&&e.status===401?'用户名或密码不正确。':e instanceof ApiError?e.message:'无法登录，请检查服务连接后重试。');}finally{setBusy(false);}}
  async function logout(){setBusy(true);setError('');try{await write<void>('/auth/logout',user!.csrf_token);clearSensitive();setUser(null);}catch(e){if(e instanceof ApiError&&e.status===401)expired();else setError('退出失败，请重试。');}finally{setBusy(false);}}
  async function confirmAction(){if(!action||!user)return;setBusy(true);setActionError('');try{
    if(action.kind==='rotate'){const value=await write<{agent_token:string}>(`/v1/hosts/${action.host.id}/token/rotate`,user.csrf_token);setToken({id:action.host.id,value:value.agent_token,units:action.host.systemd_units});}
    else if(action.kind==='revoke')await write(`/v1/hosts/${action.host.id}/token/revoke`,user.csrf_token);
    else await write(`/v1/hosts/${action.host.id}/state`,user.csrf_token,{disabled:action.kind==='disable'},'PATCH');
    setMessage(action.kind==='rotate'?'旧 Token 已撤销，请安全保存新 Token。':action.kind==='revoke'?'Agent Token 已撤销。':'主机状态已更新。');setAction(null);setRevision(n=>n+1);
  }catch(e){if(e instanceof ApiError&&e.status===401)expired();else setActionError(e instanceof ApiError?e.message:'操作失败，请重试。');}finally{setBusy(false);}}
  const closeToken=useCallback(()=>{setToken(null);},[]);
  if(loading)return <main className="auth-shell" role="status">正在验证会话…</main>;
  if(sessionError)return <main className="auth-shell"><div className="panel auth-panel"><h1>服务连接失败</h1><p className="muted">暂时无法验证会话，请稍后重试。</p><button className="btn primary" onClick={()=>void restore()}>重新连接</button></div></main>;
  if(!user)return <main className="auth-shell"><div className="auth-wrap"><Brand/><section className="panel auth-panel"><p className="eyebrow">管理员控制台</p><h1>欢迎回来</h1><p className="muted mb-8">登录以查看您的主机工作区。</p><form onSubmit={login} className="form-stack" aria-busy={busy}><div><label htmlFor="username">用户名</label><input id="username" name="username" autoComplete="username" required maxLength={64} autoFocus/></div><div><label htmlFor="password">密码</label><input id="password" name="password" type="password" autoComplete="current-password" required maxLength={1024} aria-describedby={error?'login-error':undefined}/></div>{error&&<p id="login-error" role="alert" className="error-text">{error}</p>}<button className="btn primary w-full" disabled={busy}>{busy?'正在登录…':'登录控制台'}</button></form></section><p className="auth-footer">V1.0 · 安全会话 · 真实采样</p></div></main>;
  const names:Record<Action,string>={rotate:'轮换 Agent Token',revoke:'撤销 Agent Token',disable:'禁用主机',enable:'启用主机'};
  return <div className="app-shell"><aside className="sidebar"><Brand/><div className="workspace-label">WORKSPACE</div><nav aria-label="工作区导航"><a href="#/" aria-current={current.kind==='overview'?'page':undefined}><Icon name="grid"/>主机总览</a><a href="#/manage" aria-current={current.kind==='manage'?'page':undefined}><Icon name="server"/>主机管理</a></nav><div className="sidebar-note"><Icon name="shield"/><p>只读系统采集<br/><span>无远程命令执行</span></p></div><div className="sidebar-footer"><span className="avatar">{user.username.slice(0,1).toUpperCase()}</span><div><b>{user.username}</b><p>管理员工作区</p></div></div></aside>
    <div className="workspace"><header className="topbar"><div className="mobile-brand"><Brand/></div><span className="desktop-breadcrumb">工作区 <span>/</span> {current.kind==='detail'?'主机详情':current.kind==='manage'?'主机管理':'主机总览'}</span><div className="top-actions"><span className="refresh-hint">20 秒刷新</span><button className="btn small" aria-pressed={!refreshing} onClick={()=>setRefreshing(v=>!v)}>{refreshing?'暂停刷新':'恢复刷新'}</button><button className="icon-btn" aria-label="立即刷新" onClick={()=>setRevision(n=>n+1)}><Icon name="refresh"/></button><button className="btn small" disabled={busy} onClick={()=>void logout()}>退出登录</button></div></header><nav className="mobile-nav" aria-label="移动工作区导航"><a href="#/">主机总览</a><a href="#/manage">主机管理</a></nav>
    <main className="content" tabIndex={-1}>{error&&<ErrorNotice message={error}/>} {message&&<div className="notice success" role="status"><span>{message}</span><button className="icon-btn" aria-label="关闭提示" onClick={()=>setMessage('')}><Icon name="close"/></button></div>}
      {current.kind==='detail'?<HostDetail key={current.id} id={current.id} refreshing={refreshing} revision={revision} onExpired={expired} onEdit={setEditor} onGuide={setGuide} onAction={(host,kind)=>{setActionError('');setAction({host,kind});}}/>:<Dashboard managed={current.kind==='manage'} key={current.kind} refreshing={refreshing} revision={revision} onExpired={expired} onEdit={setEditor} onGuide={setGuide}/>}
      <footer className="page-footer"><span>MultiHost Monitor <b>V1.0</b></span><span>状态以中心接收时间为准 · 原始数据保留 7 天</span></footer>
    </main></div>
    {editor!==undefined&&<HostForm host={editor} csrf={user.csrf_token} onClose={()=>setEditor(undefined)} onExpired={expired} onSaved={(id,value,units)=>{setEditor(undefined);setRevision(n=>n+1);if(value)setToken({id,value,units});else setMessage('主机配置已保存。');}}/>}
    {guide&&<InstallGuide host={guide} onClose={()=>setGuide(null)}/>}
    {token&&<TokenDialog id={token.id} token={token.value} units={token.units} onClose={closeToken}/>}
    {action&&<Modal title={names[action.kind]} onClose={()=>setAction(null)}><div className="modal-body"><p>主机：<b>{action.host.name}</b></p><p className="muted my-4">{action.kind==='rotate'?'轮换会立即撤销旧 Token。新 Token 只显示一次，需要安全替换 Agent 的私有凭据文件。':action.kind==='revoke'?'撤销后该主机的 Agent 将无法继续上报。此操作不会删除历史指标。':action.kind==='disable'?'禁用后拒绝 Agent 上报，并从在线／离线统计中单独区分。不会停止远程业务服务。':'启用后允许有效 Token 上报；此操作不会自动启动远程 Agent。'}</p>{actionError&&<ErrorNotice message={actionError}/>}<div className="modal-actions"><button className="btn" disabled={busy} onClick={()=>setAction(null)}>取消</button><button className={`btn ${action.kind==='revoke'||action.kind==='disable'?'danger-btn':'primary'}`} disabled={busy} onClick={()=>void confirmAction()}>{busy?'正在提交…':`确认${names[action.kind]}`}</button></div></div></Modal>}
  </div>;
}
