const $ = id => document.getElementById(id);
const names = {field_generation:'翼板造风场',piv:'PIV 拍摄',flow_control:'主动流动控制'};
const statuses = {queued:'已排队',planned:'已规划',waiting:'等待执行',completed:'已完成',failed:'失败',unknown:'结果未知',cancelled:'已取消',stopped:'Agent 已停止'};
const componentNames = {fastapi:'FastAPI 后端',database:'数据库',langgraph:'LangGraph',device_gateway:'设备 Gateway',arduino:'Arduino 控制器',rpa:'影刀 RPA'};
const viewTitles = {overview:'系统总览',monitor:'流程监控',tasks:'实验任务',devices:'设备与急停',agent:'Agent 与模型',logs:'日志与设置',memory:'实验记忆'};
let records = [], dashboard = null, selectedTask = null, monitorTask = null;

async function api(path, options={}) {
  const response = await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','Authorization':'Bearer '+$('token').value,...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
  return data;
}
function el(tag, content='', cls='') { const node=document.createElement(tag); node.textContent=content; if(cls)node.className=cls; return node; }
function time(value){return value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'--';}
function notify(message,error=false){const n=$('notice');n.textContent=message;n.className='notice '+(error?'error':'success');n.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>n.hidden=true,5000);}
function statusBadge(status){return 'badge '+status;}

function switchView(name){
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===name+'-view'));
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  $('page-title').textContent=viewTitles[name];document.body.classList.remove('nav-open');
  if(name==='logs') loadLogs(); if(name==='memory') loadMemory();
}

function renderDashboard(){
  if(!dashboard)return;
  $('metric-active').textContent=dashboard.tasks.active;$('metric-paused').textContent=dashboard.tasks.paused+' 个暂停';
  $('metric-calls').textContent=dashboard.usage.calls;$('metric-model').textContent=dashboard.model.model;
  $('metric-tokens').textContent=dashboard.usage.total_tokens.toLocaleString();$('metric-cost').textContent='$'+dashboard.usage.estimated_cost_usd.toFixed(4);
  $('updated-at').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  const components=$('component-list');components.replaceChildren();
  Object.entries(dashboard.components).forEach(([key,value])=>{const row=el('div','','component-row');const left=el('div');left.append(el('span','',`dot ${value.status}`),el('strong',componentNames[key]||key));const right=el('span',value.status==='ok'?'正常':value.status==='stale'?'心跳已过期':'未连接',`component-state ${value.status}`);row.append(left,right);components.append(row);});
  const active=$('active-task-list');active.replaceChildren();const live=records.filter(t=>['queued','planned','waiting'].includes(t.status)).slice(0,5);
  live.forEach(t=>{const row=el('button','','compact-task');row.append(el('strong',t.objective),el('span',(names[t.scenario]||t.scenario)+' · '+statuses[t.status]));row.onclick=()=>{monitorTask=t.id;$('monitor-task').value=t.id;switchView('monitor');renderMonitor();};active.append(row);});
  if(!live.length)active.append(el('p','当前没有运行中的任务','muted'));
  $('provider').value=dashboard.model.provider;$('model-name').value=dashboard.model.model;$('temperature').value=dashboard.model.temperature;$('max-tokens').value=dashboard.model.max_tokens;
  $('temperature-value').value=dashboard.model.temperature;$('max-tokens-value').value=dashboard.model.max_tokens;
  $('key-state').textContent=dashboard.model.api_key_configured?'服务器已配置 API Key':'服务器未配置该服务商 API Key';
  $('usage-detail').replaceChildren(stat('调用次数',dashboard.usage.calls),stat('输入 Token',dashboard.usage.input_tokens),stat('输出 Token',dashboard.usage.output_tokens),stat('平均延迟',dashboard.usage.average_latency_ms+' ms'),stat('估算费用','$'+dashboard.usage.estimated_cost_usd.toFixed(4)));
  renderDeviceHealth();
}
function stat(label,value){const node=el('div');node.append(el('small',label),el('strong',String(value)));return node;}

function renderDeviceHealth(){
  const root=$('device-health');root.replaceChildren();if(!dashboard)return;
  ['device_gateway','arduino','rpa'].forEach(key=>{const value=dashboard.components[key];const card=el('article','','device-card');const label=value.status==='ok'?'在线':value.status==='stale'?'心跳已过期':'尚未连接';card.append(el('span','',`dot ${value.status}`),el('h3',componentNames[key]),el('strong',label),el('p',value.last_seen?'最后心跳：'+time(value.last_seen):'启动对应 Bridge 后才会收到心跳'));root.append(card);});
  const history=$('emergency-history');history.replaceChildren();
  (dashboard.emergency_stops||[]).forEach(stop=>{const row=el('div','','emergency-row');const info=el('div');info.append(el('strong',stop.parameters.target+' · '+stop.id),el('small',time(stop.created_at)+' · '+stop.parameters.reason));row.append(info,el('span',({queued:'等待 Gateway',running:'Gateway 已领取',completed:'控制器已回报完成',failed:'执行失败'}[stop.status]||stop.status),statusBadge(stop.status)));history.append(row);});
  if(!(dashboard.emergency_stops||[]).length)history.append(el('p','暂无急停命令','muted'));
}

function renderTasks(){
  const body=$('tasks');body.replaceChildren();records.forEach(t=>{const tr=document.createElement('tr');const objective=el('button',t.objective,'row-link');objective.onclick=()=>{selectedTask=t.id;renderTaskDetail();};const td=document.createElement('td');td.append(objective);tr.append(td,el('td',names[t.scenario]||t.scenario),el('td',t.mode==='simulation'?'模拟':'外部 Gateway'),el('td',t.control_state==='paused'?'已暂停':t.control_state==='stopped'?'已停止':'运行'),el('td',statuses[t.status]||t.status,statusBadge(t.status)),el('td',time(t.created_at)));body.append(tr);});
  const select=$('monitor-task'),current=select.value;select.replaceChildren(new Option('选择任务',''));records.forEach(t=>select.add(new Option(t.objective+' · '+(statuses[t.status]||t.status),t.id)));select.value=current||monitorTask||'';
  if(selectedTask)renderTaskDetail();if(monitorTask)renderMonitor();
}

function renderTaskDetail(){
  const t=records.find(row=>row.id===selectedTask);if(!t)return;const root=$('task-detail');root.replaceChildren();const head=el('div','','detail-head');const title=el('div');title.append(el('h3',t.objective),el('p',t.id,'muted'));head.append(title,el('span',statuses[t.status]||t.status,statusBadge(t.status)));root.append(head);
  const timeline=el('div','','timeline');
  (t.events||[]).forEach(e=>{
    const label=statuses[e.stage]||({paused:'已暂停',resumed:'已继续'}[e.stage])||e.stage;
    timeline.append(el('span',label+' '+new Date(e.at*1000).toLocaleTimeString('zh-CN',{hour12:false})));
  });
  root.append(timeline);
  if(t.result?.samples){const canvas=document.createElement('canvas');canvas.width=900;canvas.height=190;canvas.setAttribute('aria-label','模拟风速时间序列');root.append(canvas);drawSamples(canvas,t.result.samples);}
  const pre=el('pre',JSON.stringify({plan:t.plan,result:t.result},null,2));root.append(pre);
}
function drawSamples(canvas,samples){const c=canvas.getContext('2d');c.clearRect(0,0,canvas.width,canvas.height);c.strokeStyle='#167260';c.lineWidth=2;c.beginPath();const values=samples.map(p=>p.velocity),min=Math.min(...values)-.2,max=Math.max(...values)+.2;samples.forEach((p,i)=>{const x=40+i*820/Math.max(samples.length-1,1),y=155-(p.velocity-min)*115/Math.max(max-min,.1);i?c.lineTo(x,y):c.moveTo(x,y)});c.stroke();c.fillStyle='#52605e';c.font='13px system-ui';c.fillText('模拟风速 (m/s)，不代表真实实验数据',16,22);}

function renderMonitor(){
  monitorTask=$('monitor-task').value||monitorTask;const t=records.find(row=>row.id===monitorTask);$('monitor-empty').hidden=!!t;$('monitor-content').hidden=!t;if(!t)return;
  $('monitor-objective').textContent=t.objective;$('monitor-status').textContent=(statuses[t.status]||t.status)+(t.control_state==='paused'?' / 流程暂停':'');$('monitor-status').className=statusBadge(t.status);
  const active=['queued','planned','waiting'].includes(t.status);$('pause-task').disabled=!active||t.control_state==='paused';$('resume-task').disabled=t.control_state!=='paused';$('stop-agent').disabled=!active;
  const root=$('node-runs');root.replaceChildren();const runs=t.node_runs||[];
  runs.forEach(run=>{const item=el('details','','node-run');const summary=document.createElement('summary');const runLabel={completed:'完成',waiting:'等待设备',failed:'失败'}[run.status]||run.status;const dotState=run.status==='completed'?'ok':run.status==='waiting'?'pending':'failed';summary.append(el('span','',`dot ${dotState}`),el('strong',run.node),el('span',runLabel,`node-state ${run.status}`),el('time',run.duration_ms+' ms'));item.append(summary,el('pre',JSON.stringify({输入:run.input,输出:run.output,错误:run.error,开始:time(run.started_at),结束:time(run.ended_at)},null,2)));root.append(item);});
  if(!runs.length)root.append(el('p','任务尚未执行任何业务节点。','muted'));
  const notes=$('interventions');notes.replaceChildren();(t.interventions||[]).forEach(x=>{const row=el('div','','intervention');row.append(el('strong','人工补充'),el('p',x.message),el('small',time(x.at)));notes.append(row);});if(!(t.interventions||[]).length)notes.append(el('p','暂无人工补充','muted'));
}

async function loadLogs(){try{const rows=await api('/logs?limit=100');const root=$('log-lines');root.replaceChildren();rows.forEach(row=>{const line=el('div','','log-line '+row.level);line.append(el('time',time(row.at)),el('strong',row.level.toUpperCase()),el('span',row.event),el('code',JSON.stringify(row.details)));root.append(line);});if(!rows.length)root.append(el('p','暂无日志'));}catch(e){notify(e.message,true);}}
async function loadMemory(){try{const rows=await api('/memories');const root=$('memory-list');root.replaceChildren();rows.forEach(m=>{const item=el('article','','memory-item');const content=el('div');content.append(el('h3',m.objective),el('p',(names[m.scenario]||m.scenario)+' · '+(statuses[m.status]||m.status)+' · 未审核经验'));const del=el('button','删除');del.onclick=async()=>{await api('/memories/'+m.id,{method:'DELETE'});loadMemory();};item.append(content,del);root.append(item);});if(!rows.length)root.append(el('p','暂无实验记忆','muted'));}catch(e){notify(e.message,true);}}

async function refresh(){try{[records,dashboard]=await Promise.all([api('/tasks'),api('/dashboard')]);$('connection').textContent='API 已连接';$('connection-dot').className='dot ok';renderDashboard();renderTasks();}catch(e){$('connection').textContent='API 连接失败';$('connection-dot').className='dot failed';notify(e.message,true);}}
async function taskAction(action,body){if(!monitorTask)return;try{await api('/tasks/'+monitorTask+'/'+action,{method:'POST',body:JSON.stringify(body||{})});await refresh();notify('任务控制命令已记录');}catch(e){notify(e.message,true);}}
async function emergencyStop(){try{const command=await api('/emergency-stop',{method:'POST',body:JSON.stringify({target:$('stop-target').value,reason:$('stop-reason').value})});notify('急停命令已排队：'+command.id+'。请立即确认现场设备状态。');await refresh();}catch(e){notify(e.message,true);}}

document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>switchView(b.dataset.view));document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>switchView(b.dataset.go));
$('menu').onclick=()=>document.body.classList.toggle('nav-open');$('refresh').onclick=refresh;
const taskDialog=$('task-dialog');$('new-task').onclick=$('new-task-secondary').onclick=()=>taskDialog.showModal();$('close-dialog').onclick=$('cancel-dialog').onclick=()=>taskDialog.close();
$('scenario').onchange=()=>{$('motion').hidden=$('scenario').value==='piv';$('capture').hidden=$('scenario').value!=='piv';};
$('task-form').onsubmit=async e=>{e.preventDefault();$('submit').disabled=true;try{const t=await api('/tasks',{method:'POST',body:JSON.stringify({scenario:$('scenario').value,objective:$('objective').value,mode:$('mode').value,parameters:{angle_deg:Number($('angle').value),rate_deg_s:Number($('rate').value),target_velocity:Number($('velocity').value),target_ti:Number($('ti').value)/100,image_pairs:Number($('pairs').value)}})});selectedTask=t.id;monitorTask=t.id;taskDialog.close();await refresh();switchView('monitor');$('monitor-task').value=t.id;renderMonitor();notify('实验任务已创建');}catch(e){notify(e.message,true);}finally{$('submit').disabled=false;}};
$('monitor-task').onchange=()=>{monitorTask=$('monitor-task').value;renderMonitor();};$('pause-task').onclick=()=>taskAction('pause');$('stop-agent').onclick=()=>taskAction('stop-agent');
const intervention=$('intervention-dialog');$('resume-task').onclick=()=>intervention.showModal();$('close-intervention').onclick=$('cancel-intervention').onclick=()=>intervention.close();$('intervention-form').onsubmit=async e=>{e.preventDefault();await taskAction('resume',{message:$('intervention-message').value});$('intervention-message').value='';intervention.close();};
document.querySelectorAll('.emergency-trigger').forEach(b=>b.onclick=()=>{switchView('devices');$('stop-reason').focus();});$('emergency-stop').onclick=emergencyStop;
$('temperature').oninput=()=>$('temperature-value').value=$('temperature').value;$('max-tokens').oninput=()=>$('max-tokens-value').value=$('max-tokens').value;
$('model-form').onsubmit=async e=>{e.preventDefault();try{await api('/settings/model',{method:'PUT',body:JSON.stringify({provider:$('provider').value,model:$('model-name').value,temperature:Number($('temperature').value),max_tokens:Number($('max-tokens').value)})});await refresh();notify('模型设置已保存；API Key 仍只从服务器环境变量读取。');}catch(e){notify(e.message,true);}};
$('refresh-logs').onclick=loadLogs;$('run-diagnostic').onclick=async()=>{await refresh();await loadLogs();notify('连接诊断完成：结果已更新到组件状态和日志。');};
$('token').value=sessionStorage.getItem('lab-token')||'';$('token').onchange=()=>{sessionStorage.setItem('lab-token',$('token').value);refresh();};
refresh();setInterval(refresh,2000);
