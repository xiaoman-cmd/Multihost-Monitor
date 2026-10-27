import { FormEvent, useEffect, useState } from 'react';

type User = { username: string; csrf_token: string };
type Host = { id: number; name: string; hostname: string; status: string; last_seen_at: number | null };
class ApiError extends Error { constructor(public status: number) { super(`HTTP ${status}`); } }
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', ...options });
  if (!response.ok) throw new ApiError(response.status);
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
function Mark() {
  return <svg aria-hidden="true" viewBox="0 0 32 32" className="h-9 w-9 text-emerald-400" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="26" height="19" rx="4"/><path d="M7 15h5l3-6 4 10 3-4h3M12 28h8M16 23v5"/></svg>;
}
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [hosts, setHosts] = useState<Host[]>([]);
  const [hostsState, setHostsState] = useState<'loading' | 'ready' | 'error'>('loading');
  async function restore() {
    setLoading(true); setSessionError(false);
    try { setUser(await api<User>('/auth/me')); }
    catch (e) { if (!(e instanceof ApiError && e.status === 401)) setSessionError(true); }
    finally { setLoading(false); }
  }
  useEffect(() => { void restore(); }, []);
  useEffect(() => {
    if (!user) return;
    let active = true;
    setHostsState('loading');
    api<{items: Host[]}>('/hosts').then(data => {
      if (active) { setHosts(data.items); setHostsState('ready'); }
    }).catch(e => {
      if (!active) return;
      if (e instanceof ApiError && e.status === 401) { setUser(null); setError('会话已过期，请重新登录。'); }
      else setHostsState('error');
    });
    return () => { active = false; };
  }, [user]);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    try {
      setUser(await api<User>('/auth/login', { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: form.get('username'), password: form.get('password')}) }));
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? '用户名或密码不正确。' :
        e instanceof ApiError && e.status === 429 ? '登录尝试过多，请 60 秒后重试。' : '无法登录，请检查服务连接后重试。');
    } finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true); setError('');
    try {
      await api<void>('/auth/logout', { method: 'POST', headers: {'X-CSRF-Token': user!.csrf_token} });
      setUser(null); setHosts([]);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
      else setError('退出失败，请重试。');
    } finally { setBusy(false); }
  }
  if (loading) return <main className="grid min-h-screen place-items-center" role="status">正在验证会话…</main>;
  if (sessionError) return <main className="grid min-h-screen place-items-center p-6"><div className="panel max-w-md p-8"><h1 className="text-xl font-semibold">服务连接失败</h1><p className="my-4 text-slate-300">暂时无法验证会话，请稍后重试。</p><button onClick={() => void restore()} className="primary">重新连接</button></div></main>;
  if (!user) return <main className="grid min-h-screen place-items-center px-6 py-12">
    <div className="w-full max-w-md">
      <div className="mb-10 flex items-center gap-3"><Mark/><div><p className="font-semibold tracking-tight">MultiHost Monitor</p><p className="text-xs text-slate-400">基础设施监控 · V1.0</p></div></div>
      <section className="panel p-8 sm:p-10" aria-labelledby="login-title">
        <p className="eyebrow">管理员控制台</p><h1 id="login-title" className="mb-2 mt-3 text-3xl font-semibold tracking-tight">欢迎回来</h1>
        <p className="mb-8 text-sm text-slate-300">登录以查看您的主机工作区。</p>
        <form onSubmit={login} className="space-y-5" aria-busy={busy}>
          <div><label htmlFor="username">用户名</label><input id="username" name="username" autoComplete="username" required maxLength={64} autoFocus/></div>
          <div><label htmlFor="password">密码</label><input id="password" name="password" type="password" autoComplete="current-password" required maxLength={1024} aria-describedby={error ? 'login-error' : undefined}/></div>
          {error && <p id="login-error" role="alert" className="text-sm text-red-300">{error}</p>}
          <button className="primary w-full" disabled={busy}>{busy ? '正在登录…' : '登录控制台'}</button>
        </form>
      </section><p className="mt-6 text-center text-xs text-slate-400">Phase 01 · 基础框架与安全认证</p>
    </div>
  </main>;
  return <div className="min-h-screen">
    <header className="border-b border-slate-700 bg-slate-900"><div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-6 py-5 sm:px-10"><div className="flex items-center gap-3"><Mark/><span className="font-semibold">MultiHost Monitor</span><span className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-300">V1.0</span></div><div className="flex items-center gap-5"><span className="text-sm text-slate-300">{user.username}</span><button className="secondary" disabled={busy} onClick={() => void logout()}>{busy ? '正在退出…' : '退出登录'}</button></div></div></header>
    <main className="mx-auto max-w-7xl px-6 py-10 sm:px-10">
      <p className="eyebrow">工作区 / 概览</p><h1 className="mb-3 mt-3 text-3xl font-semibold">仪表盘</h1><p className="text-slate-300">您的基础设施，从这里开始。</p>
      {error && <p role="alert" className="mt-4 text-red-300">{error}</p>}
      <section className="panel mt-10" aria-labelledby="hosts-title">
        <div className="flex items-center justify-between border-b border-slate-700 px-6 py-5"><h2 id="hosts-title" className="font-semibold">主机</h2><span className="text-xs text-slate-400">Phase 01</span></div>
        {hostsState === 'loading' ? <p role="status" className="p-10 text-slate-300">正在加载主机…</p> : hostsState === 'error' ? <div role="alert" className="p-10"><p className="mb-4 text-red-300">主机列表加载失败。</p><button className="secondary" onClick={() => setUser({...user})}>重新加载</button></div> : hosts.length === 0 ? <div className="px-6 py-20 text-center"><div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-600 bg-slate-800"><Mark/></div><h3 className="text-xl font-semibold">尚未接入主机</h3><p className="mx-auto mt-3 max-w-md text-sm leading-7 text-slate-300">当前阶段已就绪：数据模型、安全会话与管理界面。<br/>Agent 接入和指标采集将在后续阶段开放。</p><p className="mt-6 text-xs text-slate-400">暂无监控数据</p></div> : <ul className="divide-y divide-slate-700">{hosts.map(h => <li key={h.id} className="flex flex-wrap justify-between gap-4 p-6"><span>{h.name}<span className="ml-3 text-sm text-slate-400">{h.hostname}</span></span><span className="text-sm text-slate-300">{h.status}</span></li>)}</ul>}
      </section><footer className="mt-8 flex flex-wrap justify-between gap-2 text-xs text-slate-400"><span>MultiHost Monitor</span><span>Phase 01 · 尚未启用采集和告警执行</span></footer>
    </main>
  </div>;
}
