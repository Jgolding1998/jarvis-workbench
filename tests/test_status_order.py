from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

from jarvis.actions import Actions
from jarvis.server import LocalHTTPServer, Service, make_handler


ROOT = Path(__file__).resolve().parents[1]


class StatusOrderTests(unittest.TestCase):
    def test_snapshots_have_one_instance_and_unique_increasing_sequences(self):
        service = Service(actions=Actions(demo=True))
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(lambda _: service.snapshot(), range(100)))
        self.assertEqual(sorted(state["sequence"] for state in states), list(range(1, 101)))
        self.assertEqual(len({state["instance"] for state in states}), 1)
        self.assertRegex(states[0]["instance"], r"^[0-9a-f]{16}$")

    def test_stop_snapshot_is_newer_and_disarmed(self):
        service = Service(actions=Actions(demo=True))
        service.command("active")
        service.arm(True, False)
        before = service.snapshot()
        service.command("stop")
        after = service.snapshot()
        self.assertGreater(after["sequence"], before["sequence"])
        self.assertEqual(after["instance"], before["instance"])
        self.assertFalse(after["active"] or after["lock_armed"] or after["sheet_armed"])

    def test_a_new_service_has_a_distinct_instance(self):
        first = Service(actions=Actions(demo=True)).snapshot()
        second = Service(actions=Actions(demo=True)).snapshot()
        self.assertEqual((first["sequence"], second["sequence"]), (1, 1))
        self.assertNotEqual(first["instance"], second["instance"])

    def test_session_identifies_the_same_service_as_status_over_http(self):
        service = Service(actions=Actions(demo=True))
        server = LocalHTTPServer(("127.0.0.1", 0), make_handler(service, "test-session-only"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            connection.request("GET", "/api/session")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            session = json.loads(response.read())
            connection.close()
            self.assertEqual(session["token"], "test-session-only")
            self.assertEqual(session["instance"], service.snapshot()["instance"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            service.shutdown()

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript checks")
    def test_browser_pins_session_before_first_status_and_bounds_polling(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(0, 'utf8');
const instance = '1234567890abcdef';
const state = (sequence=1) => ({instance,sequence,active:false,action_error:'',camera:'waiting',
  people:null,absent_seconds:0,voice:'off',uptime:0,lock_armed:false,sheet_armed:false,
  demo:true,hand:{mode:'off',allowed:false},events:[]});
const flush = async () => {for(let i=0;i<12;i++) await Promise.resolve();};
function fixture() {
  const nodes = new Map(), requests = [], intervals = [], timers = new Map();
  let nextTimer = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id,{textContent:'',hidden:false,disabled:false,dataset:{},style:{},
      classList:{toggle(){},remove(){}},listeners:{},addEventListener(kind,fn){this.listeners[kind]=fn;},
      replaceChildren(){},append(){}});
    return nodes.get(id);
  };
  const context = vm.createContext({AbortController,document:{getElementById:node,activeElement:null,
    querySelector:()=>null,querySelectorAll:selector=>selector==='button, input'?[node('stop')]:[],
    createElement:()=>({append(){},textContent:''}),createTextNode:x=>x},
    setInterval:fn=>{intervals.push(fn);return 1;},clearInterval:()=>{intervals.length=0;},
    setTimeout:(fn,delay)=>{timers.set(++nextTimer,{fn,delay});return nextTimer;},clearTimeout:id=>timers.delete(id),
    fetch:(path,options={})=>new Promise((resolve,reject)=>{
      const request={path,options,resolve:body=>resolve({ok:true,json:async()=>body}),reject};
      requests.push(request);
      if(options.signal)options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
    })});
  vm.runInContext(source,context);
  return {context,node,requests,intervals,timers};
}
(async()=>{
  const restart=fixture();
  restart.requests[0].resolve({token:'old-session',instance});await flush();
  const first=restart.requests.find(r=>r.path==='/api/status');
  first.resolve({...state(),instance:'fedcba0987654321'});await flush();
  assert.equal(restart.node('state').textContent,'Jarvis restarted');
  assert.equal(restart.node('armed-label').textContent,'RELOAD REQUIRED');
  assert.equal(restart.node('stop').disabled,true);
  assert.equal(restart.requests.length,2,'restart must not continue bootstrap diagnostics');

  const stalled=fixture();
  for(let i=0;i<100;i++)stalled.intervals[0]();
  assert.equal(stalled.requests.length,1,'polling waits for the session');
  stalled.requests[0].resolve({token:'owned-session',instance});await flush();
  for(let i=0;i<100;i++)stalled.intervals[0]();
  assert.equal(stalled.requests.filter(r=>r.path==='/api/status').length,1,'at most one status read in flight');
  const hanging=stalled.requests.find(r=>r.path==='/api/status');
  const deadline=[...stalled.timers.values()][0];
  assert.equal(deadline.delay,8000);
  deadline.fn();await flush();
  assert.equal(hanging.options.signal.aborted,true);
  assert.match(stalled.node('error').textContent,/timed out/i);
  stalled.requests.find(r=>r.path==='/api/setup/diagnostics').resolve({});await flush();
  stalled.intervals[0]();await flush();
  const retry=stalled.requests.filter(r=>r.path==='/api/status')[1];
  assert(retry,'poll retries after a timeout');
  retry.resolve(state(2));await flush();
  assert.equal(stalled.node('state').textContent,'Standby');
  assert.equal(stalled.node('error').hidden,true);
  assert.equal(stalled.timers.size,0,'completed reads clear their deadline');
  stalled.intervals[0]();await flush();
  stalled.context.showError(new Error('Command was rejected'));
  stalled.requests.filter(r=>r.path==='/api/status')[2].resolve(state(3));await flush();
  assert.equal(stalled.node('error').textContent,'Command was rejected');
  assert.equal(stalled.node('error').hidden,false,'poll recovery must preserve a newer action error');
  stalled.intervals[0]();await flush();
  const pending=stalled.requests.filter(r=>r.path==='/api/status')[3];
  const failedExit=stalled.node('exit').listeners.click();await flush();
  stalled.requests.find(r=>r.path==='/api/setup/exit').reject(new Error('Owned exit failure'));
  await failedExit;await flush();
  assert.equal(pending.options.signal.aborted,false,'failed Exit keeps status polling active');
  assert.equal(stalled.node('exit').disabled,false);
  assert.equal(stalled.intervals.length,1);
  const exit=stalled.node('exit').listeners.click();await flush();
  stalled.requests.filter(r=>r.path==='/api/setup/exit')[1].resolve({stopped:true});
  await exit;await flush();
  assert.equal(pending.options.signal.aborted,true,'successful Exit aborts only the owned status read');
  assert.equal(stalled.node('state').textContent,'Jarvis stopped');
  assert.equal(stalled.intervals.length,0);
  assert.equal(stalled.timers.size,0);
  const before=stalled.requests.length;
  await stalled.context.refresh();
  assert.equal(stalled.requests.length,before,'Exit prevents further status requests');
  hanging.resolve({...state(999),active:true});await flush();
  assert.equal(stalled.node('state').textContent,'Jarvis stopped','late timed-out response cannot repaint Exit');
  console.log('session bootstrap, single-flight polling, timeout recovery and Exit checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", script], input=(ROOT / "web/app.js").read_text(encoding="utf-8"),
                                text=True, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("timeout recovery and Exit checks passed", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript checks")
    def test_browser_rejects_old_snapshots_and_requires_reload_after_restart(self):
        service = Service(actions=Actions(demo=True))
        service.command("active")
        active = service.snapshot()
        service.command("stop")
        stopped = service.snapshot()
        restarted = Service(actions=Actions(demo=True)).snapshot()
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {textContent:'', hidden:false, disabled:false, dataset:{}, style:{},
    classList:{toggle(){},remove(){}}, addEventListener(){}, replaceChildren(){}, append(){}});
  return nodes.get(id);
};
let fetches = 0, cleared = 0;
const document = {getElementById:node, activeElement:null, querySelector:()=>null,
  querySelectorAll:selector => selector === 'button, input' ? [node('stop'),node('arm')] : [],
  createElement:()=>({append(){},textContent:''}),createTextNode:value=>value};
const context = vm.createContext({document, fetch:()=>{fetches++;return new Promise(()=>{});},
  setInterval:()=>1, clearInterval:()=>{cleared++;}});
vm.runInContext(fs.readFileSync(data.source,'utf8'),context);
context.render(data.active);
assert.equal(node('state').textContent,'Guard active');
context.render(data.stopped);
assert.equal(node('state').textContent,'Standby');
context.render(data.active);
assert.equal(node('state').textContent,'Standby');
context.render({...data.stopped,active:true});
assert.equal(node('state').textContent,'Standby');
for (const sequence of [0,-1,1.5,Infinity,'3',null]) {
  assert.throws(()=>context.render({...data.stopped,sequence}),/Session unavailable/);
}
context.render(data.restarted);
assert.equal(node('state').textContent,'Jarvis restarted');
assert.equal(node('armed-label').textContent,'RELOAD REQUIRED');
assert.equal(node('hand-label').textContent,'UNKNOWN');
assert.equal(node('stop').disabled,true);
assert.equal(node('arm').disabled,true);
context.render({...data.active,sequence:99999});
assert.equal(node('state').textContent,'Jarvis restarted');
context.send('/api/command',{command:'active'}).then(()=>{
  assert.equal(fetches,1);
  assert.equal(cleared,1);
  console.log('status ordering and restart checks passed');
}).catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps({
            "source": str(ROOT / "web/app.js"), "active": active, "stopped": stopped, "restarted": restarted}),
            text=True, capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status ordering and restart checks passed", result.stdout)
