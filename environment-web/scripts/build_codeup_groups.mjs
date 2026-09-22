import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const [input,output]=process.argv.slice(2);
if(!input||!output)throw new Error('Usage: node build_codeup_groups.mjs input.json output_directory');
const data=JSON.parse(await fs.readFile(input,'utf8'));
const wb=Workbook.create();
const main=wb.worksheets.add('代码组');
const sources=wb.worksheets.add('字段说明与来源');
function safeText(value){return typeof value==='string'&&value.startsWith('=')?"'"+value:value;}
function chinaDate(value){
  if(!value)return null;
  const d=new Date(value);
  if(!Number.isFinite(d.getTime()))throw new Error('Invalid update date');
  // Store the Shanghai wall clock as a real Excel datetime, independent of host TZ.
  const fields=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(d).map(p=>[p.type,p.value]));
  return new Date(`${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}:${fields.second}Z`);
}
const rows=data.groups.map(g=>[safeText(g.group_name),safeText(g.english_name),safeText(g.chinese_name),chinaDate(g.updated_at),g.repository_count]);
main.showGridLines=false;
main.getRange('A1:E1').merge();main.getRange('A1').values=[['Codeup 代码组清单']];
main.getRange('A1:E1').format={fill:'#173B51',font:{bold:true,color:'#FFFFFF',size:18},rowHeight:36};
main.getRange('A2:E2').merge();main.getRange('A2').values=[[`来源：${data.source_url} ｜更新时间按北京时间显示`]];
main.getRange('A2:E2').format={font:{color:'#526578'},rowHeight:26};
main.getRange('A3').values=[['代码组数量']];main.getRange('D3').values=[['代码库数量合计']];
const end=rows.length+6;
main.getRange('B3').formulas=[[rows.length?`=COUNTA(A7:A${end})`:'=0']];
main.getRange('E3').formulas=[[rows.length?`=SUM(E7:E${end})`:'=0']];
main.getRange('A4:E4').merge();main.getRange('A4').values=[['中文名称取页面描述原文；空白表示未填写。数量按列表显示值导出，不代表当前账号可读取每个代码库。']];
main.getRange('A4:E4').format={font:{color:'#896000'},rowHeight:30,wrapText:true};
main.getRange('A6:E6').values=[['代码组','英文名称','中文名称','更新日期','代码库数量']];
main.getRange('A6:E6').format={fill:'#285A70',font:{bold:true,color:'#FFFFFF'},rowHeight:30};
for(const [col,width] of [['A',31],['B',31],['C',55],['D',26],['E',20]])main.getRange(`${col}:${col}`).format.columnWidth=width;
if(rows.length){
  main.getRange(`A7:E${end}`).values=rows;
  main.getRange(`A7:E${end}`).format.wrapText=true;
  main.getRange(`D7:D${end}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
  main.getRange(`E7:E${end}`).setNumberFormat('#,##0');
  for(let i=0;i<rows.length;i++)main.getRange(`A${i+7}:E${i+7}`).format.rowHeight=Math.max(30,Math.ceil(Math.max(String(rows[i][2]).length/28,String(rows[i][0]).length/22))*17+10);
  main.tables.add(`A6:E${end}`,true,'CodeupGroups').showFilterButton=true;
}
main.freezePanes.freezeRows(6);
sources.showGridLines=false;
sources.getRange('A1:E1').merge();sources.getRange('A1').values=[['字段映射与数据来源']];
sources.getRange('A1:E1').format={fill:'#173B51',font:{bold:true,color:'#FFFFFF',size:18},rowHeight:36};
const mappings=[
 ['代码组','name：页面显示名称'],['英文名称','path：代码组英文路径标识（保留大小写，不翻译）'],
 ['中文名称','description：页面描述原文；无独立中文名字段，空值保留为空'],
 ['更新日期','last_activity_at 优先，否则 updated_at；与页面取值一致，统一显示 Asia/Shanghai 时区'],
 ['代码库数量','project_count：页面接口提供的代码库数量，不重新逐库验证权限'],
 ['导出范围',data.scope],['导出时间',data.exported_at.replace('T',' ').slice(0,19)+'（北京时间）'],
 ['组织 namespace_id',data.namespace_id],['列表页面',data.source_url],
 ['分页完整性','按 page/per_page 获取，直到空页；重复 ID 或字段异常则停止，不将半份结果标为完成']
];
for(let i=0;i<mappings.length;i++){
  sources.getRange(`A${i+3}`).values=[[mappings[i][0]]];sources.getRange(`B${i+3}:E${i+3}`).merge();sources.getRange(`B${i+3}`).values=[[mappings[i][1]]];
}
sources.getRange('A3:E12').format={rowHeight:30,wrapText:true};
sources.getRange('A15:E15').values=[['代码组ID','代码组完整路径','代码组URL','更新日期原值','日期取值字段']];
sources.getRange('A15:E15').format={fill:'#285A70',font:{bold:true,color:'#FFFFFF'},rowHeight:30};
for(const [col,width] of [['A',22],['B',65],['C',80],['D',33],['E',23]])sources.getRange(`${col}:${col}`).format.columnWidth=width;
if(rows.length){
  sources.getRange(`A16:E${rows.length+15}`).values=data.groups.map(g=>[g.id,g.full_path,g.url,g.updated_at?"'"+g.updated_at:'',g.date_source_field]);
  sources.getRange(`A16:E${rows.length+15}`).format.rowHeight=28;
  sources.tables.add(`A15:E${rows.length+15}`,true,'GroupSources').showFilterButton=true;
}
sources.freezePanes.freezeRows(15);
console.log((await wb.inspect({kind:'table',range:`'代码组'!A6:E${Math.min(end,10)}`,include:'values,formulas',tableMaxRows:5,tableMaxCols:5,maxChars:1200})).ndjson);
const check=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',options:{useRegex:true,maxResults:10},summary:'formula errors',maxChars:600});
console.log(check.ndjson);
for(const [sheet,range,name] of [[main,`A1:E${Math.min(end,14)}`,'groups_preview'],[sources,`A1:E${Math.min(rows.length+15,18)}`,'sources_preview']]){
  const preview=await wb.render({sheetName:sheet.name,range,scale:1,format:'png'});
  await fs.writeFile(path.join(output,name+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
const file=await SpreadsheetFile.exportXlsx(wb);
await file.save(path.join(output,'Codeup代码组清单.xlsx'));
console.log('Excel 已保存');
