/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');

function browser(tenant = '') {
  const states = []; let index = 0;
  const record = {id:'record',name:'api.example.cn',zone:'example.cn',type:'A',value:'192.0.2.1',status:'启用',tenantId:'a',zoneId:'public',visibility:'public'};
  const cloud = {available:true,tenants:[],zones:[
    {id:'public',resourceKey:'a/public',name:'example.cn',tenantId:'a',tenantName:'账号 A',visibility:'public',recordCount:1,records:[record]},
    {id:'zero',resourceKey:'a/zero',name:'empty.example.cn',tenantId:'a',tenantName:'账号 A',visibility:'public',recordCount:0,records:[]},
    {id:'private',resourceKey:'a/private',name:'example.cn',tenantId:'a',tenantName:'账号 A',visibility:'private',recordCount:0,records:[]},
    {id:'other',resourceKey:'b/public',name:'other.example.cn',tenantId:'b',tenantName:'账号 B',visibility:'public',recordCount:0,records:[]},
  ]};
  const sandbox={exports:{},require:id=>{
    if(id==='react') return {...React,useEffect(){},useState(initial){const slot=index++;if(!(slot in states))states[slot]=initial;return[states[slot],v=>states[slot]=v];}};
    if(id==='react/jsx-runtime')return require(id);
    if(id.endsWith('.css'))return {};
    if(id==='@/lib/clb')return {clb:{instances:[]}};
    if(id==='@/lib/ecs')return {instances:[]};
    if(id==='@/lib/dns')return {dns:{records:[]},staticDns:{zones:['folidaymall.com'],records:[]},integrateDns:()=>[]};
    if(id==='@/lib/cloud-dns')return {cloudDns:cloud};
    if(id==='@/lib/eip')return {eip:{records:[]},integrateEip:()=>[]};
    if(id==='@/lib/datetime')return {formatBeijingTime:(at,fallback)=>at||fallback||'未知'};
    if(id.endsWith('/button'))return {Button:props=>React.createElement('button',props)};
    if(id.endsWith('/input'))return {Input:props=>React.createElement('input',props)};
    return {};
  }};
  const compiled=ts.transpileModule(fs.readFileSync('app/aliyun/clb/page.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  vm.runInNewContext(compiled+'\nexports.DnsBrowser=DnsBrowser;',sandbox);
  const props={tenant,rows:[{record,matches:[],eipMatches:[],status:'未匹配当前 CLB IP'}],query:'',expanded:{},onQuery(){},onExpand(){},onCollapse(){},toggle(){}};
  function nodes(node){
    if(Array.isArray(node))return node.flatMap(nodes);
    if(!React.isValidElement(node))return [];
    if(typeof node.type==='function')return nodes(node.type(node.props));
    return [node,...nodes(node.props.children)];
  }
  function render(){index=0;return nodes(sandbox.exports.DnsBrowser(props));}
  const text=node=>Array.isArray(node)?node.map(text).join(''):React.isValidElement(node)?text(node.props.children):String(node??'');
  return {render,labels:()=>render().map(n=>text(n.props.children)).join('\n'),click(label){const button=render().find(n=>n.type==='button'&&text(n.props.children)===label);assert.ok(button,`button: ${label}`);button.props.onClick();}};
}

test('DNS starts with domain list, drills into records and returns to the list',()=>{
  const view=browser('a');
  assert.ok(!view.labels().includes('api.example.cn'));
  view.click('example.cn');
  assert.match(view.labels(),/api.example.cn/);
  view.click('返回域名列表');
  assert.ok(!view.labels().includes('api.example.cn'));
});
test('public and private tabs separate duplicate names and retain zero-record domains',()=>{
  const view=browser('a');
  assert.match(view.labels(),/empty.example.cn/);
  assert.ok(!view.labels().includes('other.example.cn'));
  view.click('empty.example.cn');
  assert.match(view.labels(),/解析记录数为 0，已跳过解析详情采集/);
  view.click('内网域名解析（1）');
  assert.ok(!view.labels().includes('empty.example.cn'));
  view.click('example.cn');
  assert.match(view.labels(),/解析记录数为 0/);
});
