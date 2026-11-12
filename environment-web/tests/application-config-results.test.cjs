const { test }=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const React=require('react');
const { renderToStaticMarkup }=require('react-dom/server');
const sandbox={exports:{},require};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-config-results.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,sandbox);
const {ApplicationConfigResults}=sandbox.exports;
test('completed applications and partial, failed and skipped reasons are visible by category',()=>{
 const results=[{appId:'1',name:'success',status:'ready'}, {appId:'2',name:'partial',status:'partial',warnings:['原因一','原因二']}, {appId:'3',name:'failed',status:'failed',error:'无权限'}, {appId:'4',name:'skip',status:'skipped',reason:'非 Java'}];
 const html=renderToStaticMarkup(React.createElement(ApplicationConfigResults,{results}));
 for (const text of ['查看已完成的应用（1）','success（1）','查看部分完成的应用及原因（1）','partial（2）','原因一；原因二','查看失败的应用及原因（1）','无权限','查看跳过的应用（1）','非 Java']) assert.ok(html.includes(text),text);
});
test('missing partial reason is explicit',()=>{
 const html=renderToStaticMarkup(React.createElement(ApplicationConfigResults,{results:[{appId:'2',name:'partial',status:'partial'}]}));
 assert.match(html,/未记录具体原因/);
});
function findAll(tree, predicate, found=[]) {
 if (!tree || typeof tree !== 'object') return found;
 if (predicate(tree)) found.push(tree);
 for (const child of React.Children.toArray(tree.props?.children)) findAll(child,predicate,found);
 return found;
}
test('only eligible groups can be marked; bulk excludes already marked and markers can be reversed',()=>{
 const results=[{appId:'1',name:'missing',status:'failed',error:'Codeup 未找到该仓库，或当前账号无法查看'},
   {appId:'2',name:'missing2',status:'failed',error:'Codeup 未找到该仓库，或当前账号无法查看'},
   {appId:'3',name:'unsupported',status:'failed',error:'仅支持 Codeup HTTPS 仓库'},
   {appId:'4',name:'other',status:'failed',error:'连接失败'}, {appId:'5',name:'skipped',status:'skipped'},
   {appId:'6',name:'unmatched',status:'failed',error:'当前快照中仓库匹配数量为 0'},
   {appId:'7',name:'no-address',status:'failed',error:'当前 DevOps 快照未提供应用仓库地址'}];
 const calls=[];
 const tree=ApplicationConfigResults({results,exclusions:[{appId:'1',name:'missing',category:'codeup_not_found',markedAt:'now'}],onUpdate:(ids,value)=>calls.push([Array.from(ids),value])});
 const buttons=findAll(tree,item=>item.type==='button');
 const bulk=buttons.find(item=>Array.isArray(item.props.children) && item.props.children[0]==='本类全部标记不用同步（' && item.props.children[1]===1);
 assert.ok(bulk); bulk.props.onClick();
 assert.deepEqual(calls[0],[['2'],true]);
 assert.equal(buttons.filter(item=>item.props.children==='标记不用同步').length,5);
 const undo=buttons.find(item=>item.props.children==='撤销不用同步');
 assert.ok(undo); undo.props.onClick();
 assert.deepEqual(calls[1],[['1'],false]);
});
