import {useEffect,useRef,useState} from 'react';
import {History,Point,bytes,date,percent} from './api';
type Key='cpu_percent'|'memory_percent'|'swap_percent'|'receive_rate'|'send_rate';
export type Series={key:Key;label:string;color:string;dash?:string};
export function splitSegments(points:Point[],key:Key):Point[][]{
  const groups:Point[][]=[];let current:Point[]=[];
  for(const point of points){if(point[key]===null||point.segment===null){if(current.length)groups.push(current);current=[];continue;}
    if(current.length&&point.segment!==current[current.length-1].segment){groups.push(current);current=[];}current.push(point);
  }if(current.length)groups.push(current);return groups;
}
export default function Chart({title,history,series,rate=false}:{title:string;history:History;series:Series[];rate?:boolean}){
  const [index,setIndex]=useState<number|null>(null);const [width,setWidth]=useState(660);const plot=useRef<SVGSVGElement>(null);const points=history.points;const activeIndex=Math.min(index??points.length-1,points.length-1);const active=points[activeIndex];
  const hasData=points.some(p=>series.some(s=>p[s.key]!==null));
  useEffect(()=>{const element=plot.current;if(!element)return;const measure=()=>{const size=element.getBoundingClientRect().width;if(size)setWidth(Math.max(280,size));};measure();if(typeof ResizeObserver==='undefined')return;const observer=new ResizeObserver(measure);observer.observe(element);return()=>observer.disconnect();},[hasData]);
  const max=rate?Math.max(1,...points.flatMap(p=>series.map(s=>p[s.key]??0)))*1.1:100;
  const x=(at:number)=>48+(at-history.from)/Math.max(1,history.until-history.from)*(width-64);
  const y=(value:number)=>152-value/max*122;
  const format=(n:number|null)=>rate?`${bytes(n)}${n===null?'':'/s'}`:percent(n);
  return <section className="panel chart-panel"><div className="section-title"><h2>{title}</h2><span className="muted text-xs">{history.bucket_seconds} 秒 / 桶</span></div>
    <div className="chart-legend">{series.map(s=><span key={s.key}><i className={`series-${s.key}`}/>{s.label}</span>)}</div>
    {!hasData?<div className="chart-empty">暂无历史数据</div>:<>
      <svg ref={plot} viewBox={`0 0 ${width} 182`} className="chart" role="img" aria-label={`${title}，空值与采样间断处断线`}>
        {[0,.5,1].map(n=><g key={n}><line x1="48" x2={width-16} y1={y(max*n)} y2={y(max*n)} className="chart-grid"/><text x="42" y={y(max*n)+4} textAnchor="end">{rate?bytes(max*n):`${Math.round(max*n)}%`}</text></g>)}
        {series.flatMap(s=>splitSegments(points,s.key).map((group,i)=><g key={`${s.key}-${i}`} data-segment={group[0].segment}><polyline data-series={s.key} points={group.map(p=>`${x(p.at)},${y(p[s.key]!)}`).join(' ')} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash}/>{group.length===1&&<circle cx={x(group[0].at)} cy={y(group[0][s.key]!)} r="2.5" fill={s.color}/>}</g>))}
        <text x="48" y="177">{date(history.from,true)}</text><text x={width-16} y="177" textAnchor="end">{date(history.until,true)}</text>
      </svg>
      {active&&<div className="chart-inspect"><label htmlFor={`${title}-point`} className="sr-only">{title}采样时间</label><input id={`${title}-point`} type="range" min="0" max={Math.max(0,points.length-1)} value={activeIndex} onChange={e=>setIndex(Number(e.target.value))} aria-valuetext={date(active.at)}/><div><span>{date(active.at)}</span>{series.map(s=><span key={s.key}>{s.label} <b>{format(active[s.key])}</b></span>)}</div></div>}
    </>}
    <details className="data-details"><summary>查看数据表（含缺口）</summary><div className="table-scroll"><table><thead><tr><th>UTC 采样时间</th><th>条数</th>{series.map(s=><th key={s.key}>{s.label}</th>)}</tr></thead><tbody>{points.map((p,i)=><tr key={i}><td>{new Date(p.at*1000).toISOString()}</td><td>{p.sample_count}</td>{series.map(s=><td key={s.key}>{p[s.key]===null?'缺口':format(p[s.key])}</td>)}</tr>)}</tbody></table></div></details>
  </section>;
}
