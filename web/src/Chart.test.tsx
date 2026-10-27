import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,test} from 'vitest';
import Chart,{splitSegments} from './Chart';
import {history,point,stamp} from './test-fixtures';
afterEach(cleanup);
test('null and segment changes produce independent polylines',()=>{
  const points=[point(1,10),point(2,null),point(3,20),point(4,30,2)];
  expect(splitSegments(points,'receive_rate').map(x=>x.length)).toEqual([1,1,1]);
  const {container}=render(<Chart title="网络测试" history={{...history,points}} rate series={[{key:'receive_rate',label:'接收',color:'#65d4b4'}]}/>);
  expect(container.querySelectorAll('polyline')).toHaveLength(3);expect(screen.getByText('缺口')).toBeInTheDocument();
});
test('empty history does not draw a zero line',()=>{
  const {container}=render(<Chart title="CPU 测试" history={{...history,points:[point(stamp,null,null)]}} series={[{key:'cpu_percent',label:'CPU',color:'#72b7ff'}]}/>);
  expect(screen.getByText('暂无历史数据')).toBeInTheDocument();expect(container.querySelector('polyline')).toBeNull();
});
