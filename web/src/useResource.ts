import {useCallback,useEffect,useRef,useState} from 'react';
import {api,ApiError} from './api';
export function useResource<T>(path:string,refreshing:boolean,onExpired:()=>void,externalRevision=0){
  const [result,setResult]=useState<{path:string;data:T|null;error:string;loading:boolean}>({path,data:null,error:'',loading:true});
  const [revision,setRevision]=useState(0);const expired=useRef(onExpired);expired.current=onExpired;
  const reload=useCallback(()=>setRevision(x=>x+1),[]);
  useEffect(()=>{
    let active=true;let pending=false;let controller:AbortController|null=null;
    setResult(old=>old.path===path?{...old,loading:true}:{path,data:null,error:'',loading:true});
    async function load(){if(pending)return;pending=true;controller=new AbortController();
      try{const data=await api<T>(path,{signal:controller.signal});if(active)setResult({path,data,error:'',loading:false});}
      catch(e){if(!active)return;if(e instanceof ApiError&&e.status===401){expired.current();return;}setResult(old=>({...old,error:e instanceof ApiError?e.message:'连接失败，当前内容可能已过期，请重试。',loading:false}));}
      finally{pending=false;}
    }
    void load();
    const timer=refreshing?setInterval(()=>{if(!document.hidden)void load();},20000):undefined;
    const visible=()=>{if(refreshing&&!document.hidden)void load();};document.addEventListener('visibilitychange',visible);
    return()=>{active=false;controller?.abort();if(timer)clearInterval(timer);document.removeEventListener('visibilitychange',visible);};
  },[path,refreshing,revision,externalRevision]);
  return {...(result.path===path?result:{path,data:null,error:'',loading:true}),reload};
}
