import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import App from './App';
import {detail,emptyOverview,history} from './test-fixtures';
const ok=(body:unknown,status=200)=>Promise.resolve({ok:status<400,status,json:()=>Promise.resolve(body)});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();window.location.hash='';});
test('login, truthful empty state and CSRF logout',async()=>{
  const fetch=vi.fn().mockImplementationOnce(()=>ok({},401)).mockImplementationOnce(()=>ok({username:'admin',csrf_token:'csrf'}))
    .mockImplementationOnce(()=>ok(emptyOverview)).mockImplementationOnce(()=>ok(null,204));vi.stubGlobal('fetch',fetch);
  render(<App/>);fireEvent.change(await screen.findByLabelText('用户名'),{target:{value:'admin'}});fireEvent.change(screen.getByLabelText('密码'),{target:{value:'password'}});
  fireEvent.click(screen.getByRole('button',{name:'登录控制台'}));expect(await screen.findByText('尚未接入主机')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'退出登录'}));await screen.findByRole('heading',{name:'欢迎回来'});
  expect(fetch.mock.calls[3][1].headers['X-CSRF-Token']).toBe('csrf');
});
test('failed login is visible and form remains usable',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>ok({},401)));render(<App/>);await screen.findByLabelText('用户名');
  fireEvent.submit(screen.getByRole('button',{name:'登录控制台'}).closest('form')!);expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码不正确');
});
test('connection failure offers retry instead of empty dashboard',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));render(<App/>);
  expect(await screen.findByText('服务连接失败')).toBeInTheDocument();expect(screen.getByRole('button',{name:'重新连接'})).toBeEnabled();
});
test('create host token is one-time memory only and sent with CSRF',async()=>{
  const local=vi.spyOn(Storage.prototype,'setItem');
  const fetch=vi.fn().mockImplementation((url:string,options?:RequestInit)=>{
    if(url.endsWith('/auth/me'))return ok({username:'admin',csrf_token:'csrf'});
    if(url.includes('/dashboard'))return ok(emptyOverview);
    if(url==='/api/v1/hosts'&&options?.method==='POST')return ok({host:{id:1},agent_token:'test-one-time-secret'},201);
    return ok({});
  });vi.stubGlobal('fetch',fetch);render(<App/>);
  fireEvent.click(await screen.findByRole('button',{name:'添加主机'}));
  fireEvent.change(screen.getByLabelText('主机名称 *'),{target:{value:'new-host'}});fireEvent.change(screen.getByLabelText('初始 hostname *'),{target:{value:'new-host'}});
  fireEvent.click(screen.getByRole('button',{name:'创建主机并签发 Token'}));
  expect(await screen.findByLabelText('一次性 Agent Token')).toHaveTextContent('test-one-time-secret');
  const create=fetch.mock.calls.find(([url,options])=>url==='/api/v1/hosts'&&options?.method==='POST');expect(create?.[1]?.headers).toHaveProperty('X-CSRF-Token','csrf');
  expect(local).not.toHaveBeenCalled();expect(window.location.href).not.toContain('test-one-time-secret');
  fireEvent.click(screen.getByRole('button',{name:'已安全保存，关闭'}));await waitFor(()=>expect(screen.queryByText('test-one-time-secret')).not.toBeInTheDocument());
});
test('detail revoke confirmation uses CSRF and does not expose stored tokens',async()=>{
  window.location.hash='#/hosts/1';
  const fetch=vi.fn().mockImplementation((url:string)=>url.endsWith('/auth/me')?ok({username:'admin',csrf_token:'csrf'}):url.includes('/history')?ok(history):url.endsWith('/token/revoke')?ok(null,204):ok(detail));
  vi.stubGlobal('fetch',fetch);render(<App/>);await screen.findByRole('heading',{name:'fixture-host'});
  fireEvent.click(screen.getByRole('button',{name:'撤销 Token'}));const dialog=await screen.findByRole('dialog',{name:'撤销 Agent Token'});
  fireEvent.click(within(dialog).getByRole('button',{name:'确认撤销 Agent Token'}));await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(fetch.mock.calls.find(([url])=>url.endsWith('/token/revoke'))?.[1].headers['X-CSRF-Token']).toBe('csrf');
});
