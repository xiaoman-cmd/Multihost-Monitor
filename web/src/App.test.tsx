import {afterEach, expect, test, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import App from './App';
const ok = (body: unknown, status = 200) => Promise.resolve({ok: status < 400, status, json: () => Promise.resolve(body)});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
test('login, truthful empty state and CSRF logout', async () => {
  const fetch = vi.fn().mockImplementationOnce(() => ok({}, 401))
    .mockImplementationOnce(() => ok({username:'admin', csrf_token:'csrf'}))
    .mockImplementationOnce(() => ok({items:[]}))
    .mockImplementationOnce(() => ok(null, 204));
  vi.stubGlobal('fetch', fetch);
  render(<App/>);
  fireEvent.change(await screen.findByLabelText('用户名'), {target:{value:'admin'}});
  fireEvent.change(screen.getByLabelText('密码'), {target:{value:'password'}});
  fireEvent.click(screen.getByRole('button', {name:'登录控制台'}));
  expect(await screen.findByText('尚未接入主机')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'退出登录'}));
  await screen.findByRole('heading', {name:'欢迎回来'});
  expect(fetch.mock.calls[3][1].headers['X-CSRF-Token']).toBe('csrf');
});
test('failed login is visible and form remains usable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => ok({}, 401)));
  render(<App/>);
  await screen.findByLabelText('用户名');
  fireEvent.submit(screen.getByRole('button', {name:'登录控制台'}).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码不正确');
});
test('connection failure offers retry instead of empty dashboard', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  render(<App/>);
  expect(await screen.findByText('服务连接失败')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', {name:'重新连接'})).toBeEnabled());
});
