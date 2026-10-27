import {useEffect,useState} from 'react';
import {Host} from './api';
import {Icon,Modal} from './ui';
export function GuideContent({id,units=[]}:{id:number;units?:string[]}){
  const secure=window.location.protocol==='https:';
  const config={server_url:secure?window.location.origin:'https://monitor.example.com',host_id:id,token_file:'/etc/multihost-monitor/agent.token',ca_file:'',queue_capacity:240,batch_size:16,timeout_seconds:10,mountpoints:['/'],interfaces:[],systemd_units:units};
  return <div className="guide"><div className="notice"><Icon name="shield"/><span>以下为部署准备指引，当前页面不会在服务器执行任何安装。使用专用低权限账户；不要将 Token 放进命令行。</span></div>
    <ol><li><b>确认系统与架构</b><code>uname -m &amp;&amp; cat /etc/os-release</code><p>x86_64 选择 AMD64，aarch64 选择 ARM64；不能依据云厂商猜测。</p></li><li><b>准备匹配架构的二进制与 systemd 模板</b><p>从本项目 agent/bin 与 agent/packaging/systemd 获取，经主机管理员批准后安装；不得以 root 运行 Agent。</p></li><li><b>安全置入 Token</b><p>通过安全文件流程写入 agent.token，属主为专用 Agent 用户，权限 0600；不要粘贴进 Shell、日志、URL 或 Git。</p></li><li><b>配置可信 HTTPS 与本地白名单</b>{!secure&&<p className="warning-text">当前为 HTTP 开发页面。下面的 HTTPS 地址是占位符，部署前必须替换为可信中心入口。</p>}<pre>{JSON.stringify(config,null,2)}</pre></li><li><b>由管理员启用专用服务后确认上报</b><p>首次采样建立基线，约 30 秒后出现有效数据；检查最后接收时间与本机系统工具。安装、卸载、回滚按仓库 docs/phase-02-operations.md 执行。</p></li></ol></div>;
}
export function InstallGuide({host,onClose}:{host:Host;onClose:()=>void}){return <Modal title="Agent 接入指引" onClose={onClose}><div className="modal-body"><p className="muted mb-5">{host.name} · 主机 #{host.id}</p><GuideContent id={host.id} units={host.systemd_units}/></div></Modal>;}
export function TokenDialog({id,token,units=[],onClose}:{id:number;token:string;units?:string[];onClose:()=>void}){
  const [copied,setCopied]=useState(false);const [copyError,setCopyError]=useState(false);
  useEffect(()=>{const timer=setTimeout(onClose,300000);return()=>clearTimeout(timer);},[onClose]);
  async function copy(){try{await navigator.clipboard.writeText(token);setCopied(true);}catch{setCopyError(true);}}
  return <Modal title="保存 Agent Token" onClose={onClose}><div className="modal-body"><div className="notice warning"><Icon name="key"/><span>只展示这一次。关闭、离开页面或 5 分钟后会清除；丢失后需轮换，无法再次查询。</span></div><code className="token-value" aria-label="一次性 Agent Token">{token}</code><button className="btn w-full" onClick={()=>void copy()}>{copied?'已复制，请安全保存':'复制 Token'}</button><p className="help" role="status">{copyError?'无法访问剪贴板，请手动安全保存；不要截取含 Token 的画面。':'Token 不会保存到浏览器存储、日志或 URL。'}</p><details className="mt-6"><summary>查看安装指引</summary><GuideContent id={id} units={units}/></details><div className="modal-actions"><button className="btn primary" onClick={onClose}>已安全保存，关闭</button></div></div></Modal>;
}
