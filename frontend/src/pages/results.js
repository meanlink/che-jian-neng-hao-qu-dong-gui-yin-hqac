import {jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {status,attemptStatus} from '../components/common/status.js';
import {activeStatuses} from '../constants/enums.js';
import {esc,fmt,date,notify} from '../utils/dom.js';

function attemptHistory(j){
 const rows=(j.attempts||[]).map(a=>'<tr class="attempt'+(a.current?' current':'')+'"><td>第 '+a.attempt_no+' 次'+(a.current?' <small>当前</small>':'')+'</td><td>'+attemptStatus(a.status)+'</td><td>'+esc(a.worker||'—')+'</td><td>'+(a.created_at?date(a.created_at):'—')+'</td><td>'+(a.started_at?date(a.started_at):'未开始')+'</td><td>'+(a.finished_at?date(a.finished_at):'进行中')+'</td><td>'+esc(a.takeover_reason||a.cancel_reason||a.error||'—')+'</td></tr>').join('');
 return '<section class="panel"><div class="section-title"><h2>各次尝试</h2><span>共 '+(j.attempts||[]).length+' 次 · 每次尝试仅一个有效执行者</span></div>'+(rows?'<div class="table-wrap"><table class="attempts-table"><thead><tr><th>尝试</th><th>状态</th><th>执行器</th><th>入队</th><th>开始</th><th>结束</th><th>接管 / 取消 / 失败说明</th></tr></thead><tbody>'+rows+'</tbody></table></div>':'<div class="empty">尚无执行记录。</div>')+'</section>';
}

export async function results(id){
 workspace.jobs=await jobs.list();const j=id?await jobs.get(id):(workspace.jobs.find(j=>j.id===workspace.selectedJob)||workspace.jobs[0]);
 if(j&&!workspace.jobs.some(x=>x.id===j.id))workspace.jobs.push(j);
 if(!j)return heading('04 / REVIEW','结果复核','尚无分析结果。')+'<div class="empty">先到分析工作台，选择批次并提交一次计算。<a href="#/workbench">前往工作台 ↗</a></div>';
 workspace.selectedJob=j.id;const r=j.result||{},canWrite=session.user.role!=='viewer',active=activeStatuses.includes(j.status),retryable=j.status==='failed'||j.status==='cancelled';
 const actions=[];
 if(active&&canWrite)actions.push('<label class="inline-action">取消原因<input id="cancel-reason" maxlength="300" placeholder="例如：参数填错，需要重新提交"></label><button class="button" id="cancel-job">取消此尝试</button>');
 if(retryable&&canWrite)actions.push('<button class="primary" id="retry-job">按原参数重试</button>');
 if(j.status==='completed'&&session.user.role==='admin'&&!j.approved)actions.push('<button class="primary" id="approve-job">批准此业务结论</button>');
 if(j.status==='completed'&&j.approved)actions.push('<span class="approval">已获业务批准</span>');
 const stateNote=j.status==='cancelled'?'已取消：'+esc(j.cancel_reason||'用户取消')+'。结果不可批准，也不会进入分发简报。':(j.status==='failed'?'计算失败：'+esc(j.error)+'。可按原参数重试。':(j.status==='queued'?'已排队，等待执行器接单。':'后台正在执行计算，第 '+j.attempt_no+' 次尝试进行中。'));
 return heading('04 / REVIEW','结果复核与业务解释',j.name,'<button class="button" id="refresh-results">刷新结果 ↻</button>')
  +'<div class="result-selector"><label>分析记录<select id="select-job">'+workspace.jobs.map(x=>'<option value="'+x.id+'" '+(x.id===j.id?'selected':'')+'>'+esc(x.name)+' · '+esc(x.status)+'</option>').join('')+'</select></label>'+status(j.status)+'<span>'+date(j.created_at)+'</span><span class="approval">'+(j.status==='completed'&&j.approved?'已获业务批准':(j.status==='cancelled'?'已取消，不可批准':'待负责人复核'))+'</span></div>'
  +(actions.length?'<div class="result-actions">'+actions.join('')+'</div>':'')
  +(j.status==='completed'?'<section class="result-lead"><p class="eyebrow">COMPUTED FINDING</p><h2>'+esc(r.summary)+'</h2><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></section><section class="panel">'+chart(r.chart)+'</section><div class="two-columns"><section class="panel"><div class="section-title"><h2>业务解释</h2><span>由计算结果生成</span></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section><section class="method-sheet compact"><p class="eyebrow">REPRODUCIBILITY</p><h3>计算口径与参数</h3><p>'+esc(r.method)+'</p><pre>'+esc(JSON.stringify(j.params,null,2))+'</pre><p class="hint">仅已完成且获批准的结果可进入定时分发档案。</p></section></div><section class="panel"><div class="section-title"><h2>结果明细</h2><span>'+(r.table?.rows?.length||0)+' 行</span></div>'+table(r.table?.columns||[],r.table?.rows||[])+'</section>':'<section class="panel empty"><p>'+stateNote+'</p><a href="#/workbench">返回工作台 ↗</a></section>')
  +attemptHistory(j);
}
export function bindResults(refresh){
 document.querySelector('#refresh-results')?.addEventListener('click',refresh);
 document.querySelector('#select-job')?.addEventListener('change',e=>{workspace.selectedJob=Number(e.target.value);location.hash='#/results/'+e.target.value;});
 document.querySelector('#cancel-job')?.addEventListener('click',async e=>{const button=e.currentTarget;button.disabled=true;try{await jobs.cancel(workspace.selectedJob,document.querySelector('#cancel-reason')?.value||'');notify('任务已取消');await refresh();}catch(error){notify(error.message,true);button.disabled=false;}});
 document.querySelector('#retry-job')?.addEventListener('click',async e=>{const button=e.currentTarget;if(button.disabled)return;button.disabled=true;try{await jobs.retry(workspace.selectedJob);notify('已按原参数重新排队');await refresh();}catch(error){notify(error.message,true);button.disabled=false;}});
 document.querySelector('#approve-job')?.addEventListener('click',async e=>{const button=e.currentTarget;if(button.disabled)return;button.disabled=true;try{await jobs.approve(workspace.selectedJob);notify('已批准，可创建分发计划');await refresh();}catch(error){notify(error.message,true);button.disabled=false;}});
}
