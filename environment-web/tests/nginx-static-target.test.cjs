const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const RUNTIME=path.join(__dirname,'..','runtime');
// server_inventory imports only the stdlib at module level (websocket import is
// deferred to Terminal.__init__), so parse_nginx is testable directly.
const SETUP=[
 'import sys,json',
 'sys.path.insert(0,'+JSON.stringify(RUNTIME)+')',
 'import server_inventory as si',
].join('\n');
function py(expression) {
 const script=SETUP+'\nprint(json.dumps(eval('+JSON.stringify(expression)+')))\n';
 return JSON.parse(execFileSync('python3',['-c',script],{encoding:'utf8',timeout:60000}));
}
// A synthetic nginx -T dump: main config includes conf.d, one server mixing
// alias, root at server/location level, regex location, proxy and a bare one.
const DUMP=[
 '# configuration file /etc/nginx/nginx.conf:',
 'http {',
 '    include /etc/nginx/conf.d/*.conf;',
 '}',
 '# configuration file /etc/nginx/conf.d/hotels.conf:',
 'server {',
 '    listen 443 ssl;',
 '    server_name hotels.folidaymall.com;',
 '    root /home/www;',
 '    location ^~ /tc-fostay {',
 '        alias /var/www/webapps/tc-fostay;',
 '        index index.html;',
 '        try_files $uri $uri/ /tc-fostay/index.html;',
 '    }',
 '    location /api {',
 '        proxy_pass http://backend;',
 '    }',
 '    location /static {',
 '        root /data/assets;',
 '    }',
 '    location ~ \\.(jpg|png)$ {',
 '        root /data/img;',
 '    }',
 '    location / {',
 '    }',
 '}',
 'upstream backend { server 10.0.0.1:8080; }',
 '# configuration file /etc/nginx/conf.d/plain.conf:',
 'server {',
 '    listen 80;',
 '    server_name plain.test;',
 '    location /nothing {',
 '    }',
 '    location /dynamic {',
 '        alias /var/www/$host;',
 '    }',
 '}',
 ''].join('\n');
const parsed=py('si.parse_nginx('+JSON.stringify(DUMP)+')');
const routes=parsed[0];
function route(domain,uri){return routes.find(r=>r.domains.includes(domain)&&r.uri===uri);}

test('alias location resolves to the aliased directory',()=>{
 const r=route('hotels.folidaymall.com','^~ /tc-fostay');
 assert.equal(r.directive,'static/other');
 assert.equal(r.target,'/var/www/webapps/tc-fostay');
 assert.deepEqual(r.backends,[]);
});

test('location root appends a plain prefix uri, server root is inherited',()=>{
 assert.equal(route('hotels.folidaymall.com','/static').target,'/data/assets/static');
 assert.equal(route('hotels.folidaymall.com','/').target,'/home/www/');
 const fallback=routes.find(r=>r.domains.includes('hotels.folidaymall.com')&&r.uri==='/'&&r.directive==='static/other');
 assert.ok(fallback);
});

test('regex location cannot derive a path and keeps the raw root',()=>{
 assert.equal(route('hotels.folidaymall.com','~ \\.(jpg|png)$').target,'/data/img');
});

test('proxy routes are untouched and static fields stay empty without root/alias',()=>{
 const api=route('hotels.folidaymall.com','/api');
 assert.equal(api.directive,'proxy_pass');
 assert.equal(api.upstream,'backend');
 assert.deepEqual(api.backends,[{host:'10.0.0.1',port:'8080',resolution:'ip'}]);
 assert.equal(route('plain.test','/nothing').target,'');
});

test('variables in alias are kept verbatim',()=>{
 assert.equal(route('plain.test','/dynamic').target,'/var/www/$host');
});

// --- locationHint: layman explanations for location modifiers ---
const lib=(()=>{
 const sandbox={exports:{},require:()=>{throw new Error('no imports')}};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'..','lib','server-processes.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,sandbox);
 return sandbox.exports;
})();

test('locationHint explains each location modifier in plain language',()=>{
 const cases={
  '/':'匹配所有请求（默认入口）',
  '^~ /tc-fostay':'匹配所有以 /tc-fostay 开头的请求',
  '/api':'匹配所有以 /api 开头的请求',
  '= /health':'仅精确匹配 /health',
  '~ \\.php$':'正则匹配（区分大小写）：\\.php$',
  '~* \\.jpg$':'正则匹配（不区分大小写）：\\.jpg$',
  '(TCP/UDP)':'四层 stream 转发，按监听端口匹配',
 };
 for(const [uri,hint] of Object.entries(cases))assert.equal(lib.locationHint(uri),hint,uri);
 assert.match(lib.locationHint('@rewrite'),/^命名 location/);
 assert.equal(lib.locationHint(''),'');
 assert.equal(lib.locationHint('garbage'),'');
});

// Mirrors the 10.58.1.218 openresty config: alias locations carrying `if`
// blocks used to emit one static route per if; `location /` plus the
// server-level fallback used to double the `/` route.
const IF_DUMP=[
 '# configuration file /usr/local/openresty/nginx/conf/nginx.conf:',
 'http {',
 '    server {',
 '        listen 80;',
 '        server_name localhost;',
 '        location / {',
 '            root html;',
 '        }',
 '        location /img/M00 {',
 '            alias /var/images/cache;',
 '            set $image_root "/var/images/cache";',
 '            if ($uri ~ "/(.*)") {',
 '                set $image_dir "$image_root/$1/";',
 '            }',
 '            if (!-f $file) {',
 '                content_by_lua_file "conf/lua/fastdfs.lua";',
 '            }',
 '        }',
 '        location /file/M00 {',
 '            alias /var/files/cache;',
 '            if (!-f $file) {',
 '                content_by_lua_file "conf/lua/fastdfs.lua";',
 '            }',
 '        }',
 '        location = /50x.html {',
 '            root html;',
 '        }',
 '    }',
 '}',
 ''].join('\n');
const ifRoutes=py('si.parse_nginx('+JSON.stringify(IF_DUMP)+')')[0];
const count=(uri)=>ifRoutes.filter(r=>r.uri===uri).length;

test('if blocks inside a location never duplicate its static route',()=>{
 assert.equal(count('/img/M00'),1);
 assert.equal(count('/file/M00'),1);
 assert.equal(route0('/img/M00').target,'/var/images/cache');
 assert.equal(count('= /50x.html'),1);
});
function route0(uri){return ifRoutes.find(r=>r.uri===uri);}

test('an explicit location / suppresses the redundant server-level fallback',()=>{
 assert.equal(count('/'),1);
 assert.equal(route0('/').target,'html/');
});

test('if blocks with their own proxy_pass are still captured',()=>{
 const dump=[
  '# configuration file /etc/nginx/nginx.conf:',
  'http { server {',
  '    listen 80; server_name x.test;',
  '    location /api {',
  '        if ($arg_debug) { proxy_pass http://debug_backend; }',
  '    }',
  ' } upstream debug_backend { server 10.9.9.9:8080; } }',
  ''].join('\n');
 const rs=py('si.parse_nginx('+JSON.stringify(dump)+')')[0];
 const proxy=rs.find(r=>r.directive==='proxy_pass');
 assert.ok(proxy,'if 内的 proxy_pass 必须保留');
 assert.deepEqual(proxy.backends,[{host:'10.9.9.9',port:'8080',resolution:'ip'}]);
 assert.equal(rs.filter(r=>r.uri==='/api').length,2,'同一 uri 一条 static 一条 conditional proxy');
});

test('diamond includes of the same file do not duplicate routes',()=>{
 const dump=[
  '# configuration file /etc/nginx/nginx.conf:',
  'http {',
  '    include /etc/nginx/conf.d/a.conf;',
  '    include /etc/nginx/conf.d/b.conf;',
  '}',
  '# configuration file /etc/nginx/conf.d/a.conf:',
  'include /etc/nginx/conf.d/shared.conf;',
  '# configuration file /etc/nginx/conf.d/b.conf:',
  'include /etc/nginx/conf.d/shared.conf;',
  '# configuration file /etc/nginx/conf.d/shared.conf:',
  'server { listen 80; server_name dup.test; location /x { root /srv; } }',
  ''].join('\n');
 const rs=py('si.parse_nginx('+JSON.stringify(dump)+')')[0];
 assert.equal(rs.filter(r=>r.domains.includes('dup.test')&&r.uri==='/x').length,1);
});
