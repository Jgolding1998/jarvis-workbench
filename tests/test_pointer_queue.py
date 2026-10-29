from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PointerQueueTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript checks")
    def test_practice_moves_are_bounded_latest_only_and_cancelled_on_stop_or_exit(self):
        script = r'''
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(0,'utf8');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function fixture(){
 const nodes=new Map(),requests=[],timers=new Map();let clock=0,timerID=0;
 const node=id=>{
  if(!nodes.has(id)){
   const classes=new Set();
   nodes.set(id,{textContent:'',hidden:false,disabled:false,dataset:{},style:{},listeners:{},
    classList:{contains:key=>classes.has(key),remove:key=>classes.delete(key),toggle(key,on){if(on)classes.add(key);else classes.delete(key);}},
    addEventListener(kind,handler){this.listeners[kind]=handler;},replaceChildren(){},append(){},
    getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})});
  }
  return nodes.get(id);
 };
 const state=(sequence,mode='practice')=>({instance:'1234567890abcdef',sequence,active:false,action_error:'',camera:'waiting',
  people:null,absent_seconds:0,voice:'off',uptime:0,lock_armed:false,sheet_armed:false,demo:true,
  hand:{mode,allowed:false,clicks:0},events:[]});
 const context=vm.createContext({AbortController,Date:class extends Date{static now(){return clock;}},
  document:{getElementById:node,activeElement:null,querySelector:()=>null,
   querySelectorAll:selector=>selector==='button, input'?[node('stop'),node('exit')]:[],
   createElement:()=>({append(){},textContent:''}),createTextNode:x=>x},
  setInterval:()=>1,clearInterval(){},setTimeout:(fn,delay)=>{timers.set(++timerID,{fn,delay});return timerID;},clearTimeout:id=>timers.delete(id),
  fetch:(path,options={})=>{
   const response=body=>({ok:true,json:async()=>body});
   if(path==='/api/session')return Promise.resolve(response({token:'owned-session',instance:'1234567890abcdef'}));
   if(path==='/api/status')return Promise.resolve(response(state(1)));
   if(path==='/api/setup/diagnostics')return Promise.resolve(response({can_exit:true}));
   return new Promise((resolve,reject)=>{
    const request={path,options,resolve:body=>resolve(response(body)),reject};requests.push(request);
    if(options.signal)options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
   });
  }});
 node('error').hidden=true;
 vm.runInContext(source,context);
 return {context,node,requests,timers,state,move(x,y){clock+=100;node('pad').listeners.pointermove({clientX:x,clientY:y});},
  pointers:()=>requests.filter(item=>item.path==='/api/hand/practice')};
}
(async()=>{
 const current=fixture();await flush();
 for(let i=1;i<=50;i++)current.move(i,100-i);
 console.log(JSON.stringify({pointer_requests_after_50_events:current.pointers().length}));
 assert.equal(current.pointers().length,1,'one pointer read in flight despite a stalled response');
 current.pointers()[0].resolve(current.state(2));await flush();
 assert.equal(current.pointers().length,2,'only the latest queued point is sent');
 assert.deepEqual(JSON.parse(current.pointers()[1].options.body),{type:'pointer',x:.5,y:.5});
 current.move(70,20);
 current.node('stop').listeners.click();await flush();
 const stop=current.requests.find(item=>item.path==='/api/command');
 assert(stop,'Stop has its own request and does not wait for pointer work');
 stop.resolve(current.state(3,'off'));await flush();
 assert.equal(current.pointers()[1].options.signal.aborted,true);
 assert.equal(current.pointers().length,2,'Stop discards the pending latest point');
 assert.equal(current.node('pad').classList.contains('live'),false);
 assert.equal(current.timers.size,0);

 const timed=fixture();await flush();timed.move(20,30);timed.move(90,80);
 const deadline=[...timed.timers.values()][0];assert.equal(deadline.delay,8000);
 deadline.fn();await flush();
 assert.equal(timed.pointers()[0].options.signal.aborted,true);
 assert.match(timed.node('error').textContent,/pointer.*timed out/i);
 assert.equal(timed.pointers().length,1,'a failed move does not automatically replay a queued move');
 timed.move(60,50);await flush();assert.equal(timed.pointers().length,2,'a later move can recover');
 timed.context.endSession('Jarvis stopped','closed');await flush();
 assert.equal(timed.pointers()[1].options.signal.aborted,true);
 timed.move(80,80);await flush();assert.equal(timed.pointers().length,2);
 assert.equal(timed.timers.size,0);
 assert.equal(timed.node('state').textContent,'Jarvis stopped');

 const camera=fixture();await flush();camera.move(40,40);camera.move(50,50);
 camera.context.render(camera.state(2,'watching'));await flush();
 assert.equal(camera.pointers()[0].options.signal.aborted,true);
 assert.equal(camera.pointers().length,1,'changing away from practice discards queued moves');
 assert.equal(camera.node('error').hidden,true,'intentional cancellation is not shown as a timeout');
 console.log('pointer queue, latest position, Stop independence, timeout recovery and Exit checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", script], input=(ROOT / "web/app.js").read_text(encoding="utf-8"),
                                capture_output=True, text=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timeout recovery and Exit checks passed", result.stdout)
