const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const RUNTIME=path.join(__dirname,'..','runtime');
// server_inventory imports only the stdlib at module level (websocket import is
// deferred to Terminal.__init__), so the parsing/filter logic is testable here.
const SETUP=[
 'import sys,json',
 'sys.path.insert(0,'+JSON.stringify(RUNTIME)+')',
 'import server_inventory as si',
 'from datetime import datetime, timezone',
 'class FakeTerminal:',
 '    def run(self, command, timeout=60):',
 '        if PAYLOAD.get("throw"): raise TimeoutError("timeout")',
 '        return PAYLOAD.get("rc",0), PAYLOAD.get("body","")',
].join('\n');
function py(expression,payload) {
 const script=SETUP+'\nPAYLOAD=json.loads('+JSON.stringify(JSON.stringify(payload||{}))+')\nprint(json.dumps(eval('+JSON.stringify(expression)+')))\n';
 return JSON.parse(execFileSync('python3',['-c',script],{encoding:'utf8',timeout:60000}));
}
const collect=(payload)=>py('si.collect_webapps(FakeTerminal())',payload);

test('filter keeps applications and skips __ prefix, _buff suffix, dated and hidden copies',()=>{
 const cases={'tc-fostay':true,'tc-fostay_buff':false,'__tmp':false,'.hidden':false,
  'tc-fostay_20260930-110536':false,'tc-fostay_20260930-1105':false,'tc-fostay_20260930_1105':false,
  'web_2024':true,'api-v2':true,'foo__bar':true};
 for(const [name,keep] of Object.entries(cases)) assert.equal(py('si.is_webapp_dir('+JSON.stringify(name)+')'),keep,name);
});

test('collect parses names with mtimes, sorts them and filters backup copies',()=>{
 // First line mimics the PTY echo of the command itself; its literal \t must not parse.
 const body=[
  'dir=/var/www/webapps; if [ -d "$dir" ]; then find "$dir" -maxdepth 1 -mindepth 1 -type d -printf \'%T@\\t%f\\n\' 2>/dev/null;',
  '1696051200.5\ttc-fostay',
  '1696100000.0\ttc-fostay_20260930-110536',
  '1696100001.0\tsettings_buff',
  '1696100002.0\t__cache',
  '1696100003.0\tportal',
  'WEBAPPS_DONE\t0',
 ].join('\n');
 const result=collect({body});
 assert.equal(result.status,'complete');
 assert.deepEqual(result.apps.map(a=>a.name),['portal','tc-fostay']);
 assert.equal(result.apps[1].mtime,py('datetime.fromtimestamp(1696051200.5, timezone.utc).isoformat()'));
});

test('duplicate entries collapse and malformed lines are skipped',()=>{
 const body='1696051200.5\ttc-fostay\n1696051200.9\ttc-fostay\nnot-a-number\tx\nWEBAPPS_DONE\t0';
 const result=collect({body});
 assert.deepEqual(result.apps.map(a=>a.name),['tc-fostay']);
});

test('missing webapps directory reports absent, not failed',()=>{
 const result=collect({body:'WEBAPPS_DONE\tabsent'});
 assert.deepEqual(result,{status:'absent',apps:[]});
});

test('find errors degrade to partial with data and failed without',()=>{
 const partial=collect({body:'1696051200.5\ttc-fostay\nWEBAPPS_DONE\t1'});
 assert.equal(partial.status,'partial');assert.equal(partial.apps.length,1);
 assert.equal(collect({body:'WEBAPPS_DONE\t2'}).status,'failed');
});

test('terminal timeouts and missing markers never raise',()=>{
 assert.equal(collect({throw:true}).status,'failed');
 assert.equal(collect({body:'only command echo noise'}).status,'failed');
});
