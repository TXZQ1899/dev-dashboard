const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const RUNTIME=path.join(__dirname,'..','runtime');
const SETUP=[
 'import sys,json',
 'sys.path.insert(0,'+JSON.stringify(RUNTIME)+')',
 'import server_inventory as si',
 'class FakeTerminal:',
 '    def run(self, command, timeout=60):',
 '        if PAYLOAD.get("throw"): raise TimeoutError("timeout")',
 '        return PAYLOAD.get("rc",0), PAYLOAD.get("body","")',
].join('\n');
function py(expression,payload) {
 const script=SETUP+'\nPAYLOAD=json.loads('+JSON.stringify(JSON.stringify(payload||{}))+')\nprint(json.dumps(eval('+JSON.stringify(expression)+')))\n';
 return JSON.parse(execFileSync('python3',['-c',script],{encoding:'utf8',timeout:60000}));
}
const collectUsage=(payload)=>py('si.collect_usage(FakeTerminal())',payload);

// --- collect_usage ---
// USAGE_COMMAND output format (single tab-separated line):
// USAGE\t<cpu1>\t<cpu2>\t<MemTotal>\t<MemAvailable>\t<MemFree>\t<Buffers>\t<Cached>

test('collect_usage computes CPU% from /proc/stat delta and memory from MemAvailable',()=>{
 // cpu1: 3360+0+1820+88000+330=93510, idle=88000+330=88330
 // cpu2: 3361+0+1821+88000+330=93512, idle=88000+330=88330
 // delta_total=2, delta_idle=0 -> cpu%=100.0
 // MemTotal=16332784, MemAvailable=8432012 -> used=7900772, %=48.4
 // memTotalMB = 16332784//1024 = 15949, memUsedMB = 7900772//1024 = 7715
 const body='USAGE\tcpu  3360 0 1820 88000 330 0 0 0 0 0\tcpu  3361 0 1821 88000 330 0 0 0 0 0\t16332784\t8432012\t2000000\t100000\t5000000';
 const result=collectUsage({body});
 assert.equal(result.cpuPercent,100.0);
 assert.equal(result.memPercent,48.4);
 assert.equal(result.memTotalMB,15949);
 assert.equal(result.memUsedMB,7715);
});

test('collect_usage reports partial CPU when not fully idle',()=>{
 // cpu1: 100+0+1+100+0=201, idle=100+0=100
 // cpu2: 199+0+2+199+0=400, idle=199+0=199
 // delta_total=199, delta_idle=99 -> 100*(199-99)/199=50.251... -> 50.3
 const body='USAGE\tcpu  100 0 1 100 0 0 0 0 0 0\tcpu  199 0 2 199 0 0 0 0 0 0\t8192\t4096\t2000\t100\t500';
 const result=collectUsage({body});
 assert.equal(result.cpuPercent,50.3);
 assert.equal(result.memPercent,50.0);
 assert.equal(result.memTotalMB,8);
 assert.equal(result.memUsedMB,4);
});

test('collect_usage falls back to MemFree+Buffers+Cached when MemAvailable empty',()=>{
 // MemAvailable is empty -> fallback: used = total - free - buffers - cached
 // = 16332784 - 2000000 - 100000 - 5000000 = 9232784
 // memUsedMB = 9232784//1024 = 9016
 // % = 9232784*100/16332784 = 56.5
 const body='USAGE\tcpu  100 0 1 100 0 0 0 0 0 0\tcpu  199 0 2 199 0 0 0 0 0 0\t16332784\t\t2000000\t100000\t5000000';
 const result=collectUsage({body});
 assert.equal(result.memPercent,56.5);
 assert.equal(result.memUsedMB,9016);
});

test('collect_usage returns null on rc!=0',()=>{
 assert.equal(collectUsage({rc:1,body:''}),null);
});

test('collect_usage returns null when USAGE marker missing',()=>{
 assert.equal(collectUsage({body:'some random output'}),null);
});

test('collect_usage returns null on timeout',()=>{
 assert.equal(collectUsage({throw:true}),null);
});

test('collect_usage handles PTY echo noise before marker',()=>{
 // cpu1: 100+0+1+100+0=201, idle=100
 // cpu2: 200+0+2+200+0=402, idle=200
 // delta_total=201, delta_idle=100 -> 100*(201-100)/201=50.248... -> 50.2
 const body=[
  'cpu1=$(head -1 /proc/stat); sleep 1; ...',
  'some echo noise',
  'USAGE\tcpu  100 0 1 100 0 0 0 0 0 0\tcpu  200 0 2 200 0 0 0 0 0 0\t8192\t4096\t2000\t100\t500',
 ].join('\n');
 const result=collectUsage({body});
 assert.equal(result.cpuPercent,50.2);
 assert.equal(result.memPercent,50.0);
});

test('collect_usage returns null when fewer than 7 fields',()=>{
 const body='USAGE\tcpu  100 0 1 100 0\tcpu  200 0 2 200 0\t8192';
 assert.equal(collectUsage({body}),null);
});

// --- parse_processes (with %cpu) ---
// ps output format: pid ppid user etimes lstart comm %cpu %mem rss vsz args
// lstart has NO quotes: "Mon Oct 10 12:00:00 2026"

test('parse_processes captures cpuPercent from updated ps output',()=>{
 const body=' 1234 1 root 3600 Mon Oct 10 12:00:00 2026 java 12.5 3.2 32768 65536 java -jar app.jar';
 const result=py('si.parse_processes('+JSON.stringify(body)+')',{});
 assert.equal(result[0].length,1);
 assert.equal(result[0][0].cpuPercent,12.5);
 assert.equal(result[0][0].memPercent,3.2);
 assert.equal(result[0][0].rssKB,32768);
 assert.equal(result[0][0].vszKB,65536);
 assert.equal(result[0][0].command,'java -jar app.jar');
});

test('parse_processes includes system processes with system flag',()=>{
 const body=' 2 0 root 3600 Mon Oct 10 12:00:00 2026 kthreadd 0.0 0.0 0 0 [kthreadd]';
 const result=py('si.parse_processes('+JSON.stringify(body)+')',{});
 assert.equal(result[0].length,1,'system process now included in rows');
 assert.equal(result[0][0].system,true,'system flag set');
 assert.equal(result[1],1,'excluded counter still tracks system processes');
});

test('parse_processes marks old-format lines without %cpu as malformed',()=>{
 // Old format: pid ppid user etimes lstart comm %mem rss vsz args (no %cpu column)
 const body=' 1234 1 root 3600 Mon Oct 10 12:00:00 2026 java 3.2 32768 65536 java -jar app.jar';
 const result=py('si.parse_processes('+JSON.stringify(body)+')',{});
 assert.equal(result[0].length,0);
 assert.equal(result[2],1);
});
