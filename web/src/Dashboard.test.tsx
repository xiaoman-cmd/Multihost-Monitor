import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import Dashboard from './Dashboard';
import {emptyOverview,host,populated} from './test-fixtures';
const ok=(body:unknown)=>Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(body)});
const props={refreshing:true,revision:0,onExpired:vi.fn(),onEdit:vi.fn(),onGuide:vi.fn()};
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();vi.useRealTimers();});
test('stale metrics have explicit warning, null is not zero, disabled stays separate',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>ok({...populated,stats:{total:1,online:0,offline:0,disabled:1},items:[{...host,status:'disabled',freshness:'stale'}]})));
  render(<Dashboard {...props}/>);expect(await screen.findByText('指标已过期 · 以下为历史值')).toBeInTheDocument();expect(screen.getAllByText('已禁用').length).toBeGreaterThan(0);expect(screen.queryByText('0 B/s')).not.toBeInTheDocument();
});
test('search and filters reset stable cursor and list mode remains usable',async()=>{
  const fetch=vi.fn().mockImplementation(()=>ok({...populated,next_after_id:1,total:13}));vi.stubGlobal('fetch',fetch);render(<Dashboard {...props}/>);
  await screen.findByText('fixture-host');fireEvent.click(screen.getByRole('button',{name:'下一页'}));await waitFor(()=>expect(fetch.mock.calls.at(-1)?.[0]).toContain('after_id=1'));
  fireEvent.change(screen.getByLabelText('搜索主机'),{target:{value:'oracle'}});fireEvent.submit(screen.getByLabelText('搜索主机').closest('form')!);
  await waitFor(()=>expect(fetch.mock.calls.at(-1)?.[0]).toContain('search=oracle'));
  expect(fetch.mock.calls.at(-1)?.[0]).toContain('after_id=0');
  fireEvent.change(screen.getByLabelText('在线状态'),{target:{value:'disabled'}});await waitFor(()=>expect(fetch.mock.calls.at(-1)?.[0]).toContain('status=disabled'));
  fireEvent.click(screen.getByRole('button',{name:'列表模式'}));expect(screen.getByRole('table')).toBeInTheDocument();
});
test('read failure has retry, never shown as empty registered hosts',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('network')));render(<Dashboard {...props}/>);
  expect(await screen.findByRole('alert')).toBeInTheDocument();expect(screen.queryByText('尚未接入主机')).not.toBeInTheDocument();
});
test('20-second refresh pauses while hidden and while user-paused',async()=>{
  vi.useFakeTimers();const fetch=vi.fn().mockImplementation(()=>ok(emptyOverview));vi.stubGlobal('fetch',fetch);
  Object.defineProperty(document,'hidden',{value:false,configurable:true});let view:ReturnType<typeof render>;
  await act(async()=>{view=render(<Dashboard {...props}/>);});expect(fetch).toHaveBeenCalledTimes(1);
  Object.defineProperty(document,'hidden',{value:true,configurable:true});await act(async()=>{await vi.advanceTimersByTimeAsync(20000);});expect(fetch).toHaveBeenCalledTimes(1);
  Object.defineProperty(document,'hidden',{value:false,configurable:true});await act(async()=>{await vi.advanceTimersByTimeAsync(20000);});expect(fetch).toHaveBeenCalledTimes(2);
  await act(async()=>{view!.rerender(<Dashboard {...props} refreshing={false}/>);});const count=fetch.mock.calls.length;
  await act(async()=>{await vi.advanceTimersByTimeAsync(40000);});expect(fetch).toHaveBeenCalledTimes(count);
});
