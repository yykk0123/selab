/* Local graphical client. Server services enforce all business permissions. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const yuan = cents => '¥ ' + (Number(cents || 0) / 100).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
const labels = {ADMIN:'管理员',MEMBER:'普通成员',READER:'只读成员',ACTIVE:'在账本中',REMOVED:'已移除',LEFT:'已退出',OPEN:'开启中',CLOSED:'已关闭',PENDING:'待审核',APPROVED:'已通过',REJECTED:'已驳回',VOID:'已作废',CORRECTED:'已更正',CONFIRMED:'已确认',EXPENSE:'支出',INCOME:'收入'};
const reasonLabels = {OVER_BUDGET:'超出分类预算',LARGE_AMOUNT:'大额账单',POTENTIAL_DUPLICATE:'疑似重复'};
const state = {data:null,page:'overview',ledger:localStorage.getItem('hezhang-ledger'),detail:null,editing:null,filtered:null};
let toastTimer;
function toast(message, error=false) { const el=$('#toast'); el.textContent=message; el.className=error?'error':''; el.style.display='block'; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.style.display='none',6000); }
async function api(path, data) {
  const response=await fetch('/api/'+path, data ? {method:'POST',headers:{'Content-Type':'application/json','X-Hezhang-Request':'1'},body:JSON.stringify(data)} : {});
  const result=await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败');
  return data ? result.result : result;
}
const memberName = id => state.data?.members?.find(m=>m.user_id===id)?.username || (state.data?.user.id===id?state.data.user.username:'历史成员');
const categoryName = id => state.data?.categories?.find(c=>c.id===id)?.name || '—';
const badge = status => `<span class="badge ${esc(status)}">${esc(labels[status]||status)}</span>`;
const canAdmin = () => state.data?.membership?.role==='ADMIN' && state.data?.membership?.status==='ACTIVE';
const canWrite = () => state.data?.membership?.role!=='READER' && state.data?.membership?.status==='ACTIVE';
const openLedger = () => state.data?.ledger?.status==='OPEN';
const activeMember = () => state.data?.membership?.status==='ACTIVE';
const time = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '—';
function reasons(value) { let list=value; if(typeof value==='string') {try { list=JSON.parse(value); } catch { list=[value]; }} return (list||[]).map(item=>reasonLabels[item]||item).join('、') || '无需人工审核'; }
function table(heads, rows, empty='暂无记录') { return `<div class="table-wrap"><table><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${heads.length}" class="empty">${empty}</td></tr>`}</tbody></table></div>`; }
const button = (label, action, id='', extra='') => `<button class="small" data-action="${action}" data-id="${esc(id)}" ${extra}>${label}</button>`;
function head(title, subtitle, action='') {return `<header class="page-head"><div><h1>${title}</h1><p class="muted">${subtitle}</p></div>${action}</header>`;}
const card = (title, body) => `<section class="card"><h2>${title}</h2>${body}</section>`;
let fieldIndex=0;
const field = (label, control, cls='') => {
  const id='field-'+(++fieldIndex);
  return `<div class="field ${cls}"><label for="${id}">${label}</label>${control.replace(/<(input|select|textarea)/, '<$1 id="'+id+'"')}</div>`;
};
const input = (name, placeholder='', type='text', value='', required=true) => `<input name="${name}" type="${type}" placeholder="${esc(placeholder)}" value="${esc(value)}" ${required?'required':''}>`;
function select(name, options, value='') {return `<select name="${name}">${options.map(([id,label])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(label)}</option>`).join('')}</select>`;}
function expenseRows(expenses) {return expenses.map(e=>[`${esc(e.title)}<br><span class="muted">${time(e.occurred_at)}</span>`,esc(categoryName(e.category_id)),esc(memberName(e.submitter)),`${e.kind==='INCOME'?'+ ':''}${yuan(e.amount)}`,badge(e.status),button('查看详情','detail',e.id)]);}
async function load() {
  try {
    state.data=await api('state'+(state.ledger?'?ledger_id='+encodeURIComponent(state.ledger):''));
  } catch(error) {
    if(state.ledger) {state.ledger=null;localStorage.removeItem('hezhang-ledger');try {state.data=await api('state');} catch {login();return;}}
    else {login();return;}
  }
  state.ledger=state.data.ledger?.id;
  if(state.ledger) localStorage.setItem('hezhang-ledger',state.ledger);
  if(!activeMember() && state.ledger) state.page='settlement';
  render();
}
function login(register=false) {
  state.data=null;
  $('#app').innerHTML=`<main class="login-page"><section class="login-art"><div class="brand"><span class="logo">合</span>合账</div><h1>一起出发，<br>账目有数。</h1><p>记清每一笔共同开销，让垫付、分摊与结算都清晰可追溯。</p><p class="muted">多人共享记账系统 · 实验二界面实现</p></section><section class="login-panel"><div class="login-box"><h1>${register?'创建你的账号':'欢迎回到合账'}</h1><p class="muted">${register?'注册后可创建账本，或由管理员邀请加入。':'登录后继续管理你的共享账本。'}</p><form id="auth-form">${field('用户名',input('username','请输入用户名'))}${field('密码',input('password',register?'至少 8 位字符':'请输入密码','password'))}${register?field('邮箱（可选）',input('email','用于成员资料','email','',false)):''}<button class="primary" type="submit">${register?'注册账号':'登录'}</button></form><div class="tabs"><button id="toggle-auth">${register?'已有账号，返回登录':'还没有账号？注册'}</button></div><p class="muted" style="margin-top:28px">若以 --demo 启动：用户 林晓 / 陈晨 / 周宁 / 许禾<br>密码均为 demo12345</p></div></section></main>`;
  $('#toggle-auth').onclick=()=>login(!register);
  $('#auth-form').onsubmit=async event=>{
    event.preventDefault();const form=event.target;const submit=$('button[type=submit]',form);submit.disabled=true;
    try {const data=Object.fromEntries(new FormData(form));await api(register?'register':'login',data);if(register){toast('注册成功，请登录');login();}else{state.ledger=null;state.page='overview';await load();}}
    catch(error){toast(error.message,true);}finally{submit.disabled=false;}
  };
}
const navs=[['overview','账本总览'],['expenses','账单明细'],['new','新增账单'],['approval','审核中心'],['settlement','债务结算'],['stats','统计报表'],['members','成员管理'],['settings','账本设置']];
function render() {
  const d=state.data;
  const visible=navs.filter(([page])=>!d.ledger || ((!activeMember()?page==='settlement':true) && (canWrite()||page!=='new') && (canAdmin()||!['approval','settings'].includes(page))));
  $('#app').innerHTML=`<div class="layout"><aside class="sidebar"><div class="brand"><span class="logo">合</span>合账</div><p class="tagline">让每一笔共同开销更清楚</p><div class="ledger-select">${d.ledgers.length?select('ledger-switch',d.ledgers.map(l=>[l.id,l.name]),d.ledger?.id):'<b>还没有账本</b>'}<small>${d.members?d.members.filter(m=>m.status==='ACTIVE').length+' 位成员 · ':''}${labels[d.ledger?.status]||'创建你的第一个账本'}</small></div><nav class="nav">${visible.map(([page,label])=>`<button data-page="${page}" class="${state.page===page?'active':''}"><span class="num">${String(navs.findIndex(n=>n[0]===page)+1).padStart(2,'0')}</span>${label}</button>`).join('')}</nav><div class="profile">${esc(d.user.username)} / ${labels[d.membership?.role]||'用户'}<br><button data-action="logout">退出登录</button></div></aside><main class="workspace"><div class="topbar"><span class="muted">工作空间 / ${esc(d.ledger?.name||'我的账本')}</span><div class="tools"><button class="small" data-action="create">新建账本</button><span>${esc(d.user.username)} · ${labels[d.membership?.role]||'用户'}</span></div></div><div class="content"><div id="page"></div><footer class="footer"><span>合账 / 多人共享记账系统</span><span>本机数据 · 自动保存到 SQLite</span></footer></div></main></div>`;
  if(d.ledger){renderPage();}else{$('#page').innerHTML=head('从一个共享账本开始','创建账本后，邀请朋友一起记清每一笔开销。')+card('还没有加入的账本','<p class="muted">你可以创建账本成为管理员，或请管理员用你的用户名邀请你。</p><button class="primary" data-action="create">创建账本</button>');}
  const switcher=$('[name=ledger-switch]');if(switcher)switcher.onchange=()=>{state.ledger=switcher.value;state.page='overview';state.filtered=null;load();};
}
function renderPage() {
  const pages={overview,expenses:expenseList,new:expenseForm,approval,settlement,stats,members,settings,detail};
  if(!pages[state.page])state.page='overview';
  $('#page').innerHTML=pages[state.page]();
  bindPage();
}
function overview() {
  const d=state.data,s=d.statistics; if(!s)return settlement();
  const me=s.members.find(m=>m.user_id===d.user.id)||{};
  const pending=d.expenses.filter(e=>e.status==='PENDING');
  const receipts=d.payments.filter(p=>p.status==='PENDING'&&p.creditor===d.user.id);
  const metrics=[['有效支出',yuan(s.expense),'仅计入已通过且未作废记录'],['我的垫付',yuan(me.contributed),'我的承担 '+yuan(me.borne)],['我的待收款',yuan(me.receivable),'按原始账单逐笔追溯'],['待处理事项',(canAdmin()?pending.length:0)+receipts.length+' 笔',(canAdmin()?pending.length:0)+' 笔审核 · '+receipts.length+' 笔收款确认']];
  const tasks=[...(canAdmin()?pending.slice(0,3).map(e=>`<div class="task"><div><p>待审核：${esc(e.title)} ${yuan(e.amount)}</p><span class="muted">${esc(reasons(e.reasons))}</span></div>${button('去审核','detail',e.id)}</div>`):[]),...receipts.slice(0,3).map(p=>`<div class="task"><p>${esc(memberName(p.debtor))} 标记已付款 ${yuan(p.amount)}</p>${button('查看收款','settlement')}</div>`)];
  return head('一起出发，账目有数','当前账本累计 · 所有金额实时计算',canWrite()?'<button class="primary" data-page="new" '+(!openLedger()?'disabled':'')+'>＋ 新增账单</button>':'')+`<div class="metrics">${metrics.map(([label,value,sub])=>`<section class="card metric"><p class="muted">${label}</p><div class="value">${value}</div><p class="muted">${sub}</p></section>`).join('')}</div><div class="two-columns">${card('最近账单',table(['账单 / 时间','类别','提交人','金额','状态','操作'],expenseRows(d.expenses.slice(-4).reverse())))}${card('预算概览',budgets())}</div>${card('需要你处理',tasks.join('')||'<p class="muted">暂无待处理事项，可以轻松出发了。</p>')}`;
}
function budgets() {return state.data.statistics.categories.map(c=>`<div class="budget"><div class="budget-head"><span>${esc(c.name)}</span><span class="muted">${yuan(c.expense)} / ${c.budget===null?'未设置':yuan(c.budget)}</span></div><div class="track"><span style="width:${Math.min(100,c.budget?c.expense/c.budget*100:0)}%"></span></div></div>`).join('')||'<p class="muted">请在账本设置中添加消费类别与预算。</p>';}
function overview() {
  const d=state.data, stats=d.statistics;
  const mine=stats.members.find(m=>m.user_id===d.user.id)||{};
  const pending=d.expenses.filter(e=>e.status==='PENDING');
  const receipts=d.payments.filter(p=>p.creditor===d.user.id&&p.status==='PENDING');
  const add=canWrite()?'<button class="primary" data-page="new" '+(!openLedger()?'disabled':'')+'>＋ 新增账单</button>':'';
  const tasks=canAdmin()?pending.length+receipts.length:receipts.length;
  const metric=(title,value,note)=>`<section class="card metric"><p class="muted">${title}</p><div class="value">${value}</div><p class="muted">${note}</p></section>`;
  return head('一起出发，账目有数','当前账本累计 · 每一笔共同开销，都清晰可追溯',add)
    +(!openLedger()?'<div class="notice">账本已关闭，可继续处理审核与结算；新增功能暂停。</div>':'')
    +`<div class="metrics">${metric('有效支出',yuan(stats.expense),'仅计入已通过且未作废记录')}${metric('我的垫付',yuan(mine.contributed),'我的承担 '+yuan(mine.borne))}${metric('我的待收款',yuan(mine.receivable),'按原始账单逐笔追溯')}${metric('待处理事项',tasks+' 笔',(canAdmin()?pending.length+' 笔审核 · ':'')+receipts.length+' 笔收款确认')}</div>`
    +`<div class="two-columns">${card('最近账单',table(['账单 / 类别','提交人','金额','状态','操作'],d.expenses.slice(-5).reverse().map(e=>[esc(e.title)+' · '+esc(categoryName(e.category_id)),esc(memberName(e.submitter)),yuan(e.amount),badge(e.status),button('查看','detail',e.id)])))}${card('预算概览',budgets())}</div>`
    +card('需要你处理',((canAdmin()?pending.slice(0,3).map(e=>`<div class="task"><div><p>待审核：${esc(e.title)} ${yuan(e.amount)}</p><span class="muted">${esc(reasons(e.reasons))}</span></div>${button('去审核','detail',e.id)}</div>`).join(''):'')+receipts.map(p=>`<div class="task"><p>${esc(memberName(p.debtor))} 标记已付款 ${yuan(p.amount)}，确认后才抵扣债务。</p><button data-page="settlement">查看收款</button></div>`).join(''))||'<p class="muted">暂无需要处理的事项，所有账目都井井有条。</p>');
}
function expenseList() {
  const d=state.data;
  return head('账单明细','每笔收支均保留提交、审核和更正记录',canWrite()?'<button class="primary" data-page="new" '+(!openLedger()?'disabled':'')+'>＋ 新增账单</button>':'')+card('筛选账单',`<form id="filter-form"><div class="filters">${field('消费类别',select('category_id',[['','全部类别'],...d.categories.map(c=>[c.id,c.name])]))}${field('相关成员',select('user_id',[['','全部成员'],...d.members.map(m=>[m.user_id,m.username])]))}${field('账单状态',select('status',[['','全部状态'],...['APPROVED','PENDING','REJECTED','VOID','CORRECTED'].map(s=>[s,labels[s]])]))}${field('最低金额（元）',input('minimum','','text','',false))}${field('最高金额（元）',input('maximum','','text','',false))}${field('开始时间',input('start','','datetime-local','',false))}${field('结束时间',input('end','','datetime-local','',false))}<div class="actions"><button type="submit" class="primary">查询</button><button type="button" data-action="reset-filter">重置</button></div></div></form>`)+card('全部记录',`<div id="expense-results">${table(['账单 / 时间','类别','提交人','金额','状态','操作'],expenseRows(state.filtered||d.expenses.slice().reverse()))}</div><p class="muted" style="margin-top:18px">待审核、已驳回、已作废和历史更正版本不计入正式统计。</p>`);
}
function expenseForm() {
  const d=state.data,e=state.editing;
  if(!canWrite())return head('无记账权限','当前角色仅可查看账目。');
  if(!d.categories.length)return head('先设置消费类别','请联系管理员在账本设置中添加类别和预算。');
  const people=d.members.filter(m=>m.status==='ACTIVE');
  const contributions=e?.contributions||{[d.user.id]:0};
  const shares=e?.shares||{};
  return head(e?'更正账单':'新增账单','填写消费信息，分别确认垫付人与费用承担人')+(!openLedger()&&!e?'<div class="notice">账本已关闭，无法新增普通收支。</div>':'')+`<form id="expense-form"><div class="equal-columns">${card('01 消费与垫付',`<div class="form-grid">${field('账单名称',input('title','例如：旅行晚餐','text',e?.title||''),'full')}${field('金额（元）',input('amount','0.00','text',e?(e.amount/100).toFixed(2):''))}${field('消费类别',select('category_id',d.categories.map(c=>[c.id,c.name]),e?.category_id))}${field('收支类型',select('kind',[['EXPENSE','支出'],['INCOME','收入']],e?.kind||'EXPENSE'))}${field('消费时间',input('occurred_at','','datetime-local',localTime(e?.occurred_at)))}<div class="full"><h3>垫付明细</h3><p class="muted">填写实际支付金额；合计必须等于总额。收入时为实际收款人。</p>${people.map(m=>`<div class="split-row"><span>¥</span><label for="paid-${m.user_id}">${esc(m.username)}</label><input id="paid-${m.user_id}" data-payer="${m.user_id}" aria-label="${esc(m.username)}垫付金额" placeholder="0.00" inputmode="decimal" value="${contributions[m.user_id]?(contributions[m.user_id]/100).toFixed(2):''}"></div>`).join('')}</div></div>`)}${card('02 费用分摊',`${field('分摊方式',select('mode',[['equal','平均分摊'],['ratio','按比例分摊（%）'],['custom','自定义金额（元）']],e?'custom':'equal'))}<p class="muted">选择参与成员。付款人和承担人可不同；尾差按成员显示顺序分配。</p>${people.map(m=>`<div class="split-row"><input type="checkbox" id="share-${m.user_id}" data-person="${m.user_id}" ${!e||m.user_id in shares?'checked':''}><label for="share-${m.user_id}">${esc(m.username)}</label><input data-share="${m.user_id}" aria-label="${esc(m.username)}分摊值" placeholder="自动计算" value="${e&&m.user_id in shares?(shares[m.user_id]/100).toFixed(2):''}"></div>`).join('')}<div class="notice" id="split-preview">输入金额后可预览分摊。</div>${e?field('更正原因',input('reason','请说明更正原因')):''}<p class="muted">未结算账单保留历史版本；已有付款记录时，更正进入管理员审核并生成差额调整。</p>`)}</div><div class="card form-footer"><button type="button" data-page="expenses">取消</button><button type="submit" class="primary" ${!openLedger()&&!e?'disabled':''}>${e?'提交更正':'提交账单'}</button></div></form>`;
}
function localTime(value) {const date=value?new Date(value):new Date();return new Date(date-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function approval() {
  if(!canAdmin())return head('审核中心','只有管理员可以处理审核。');
  const d=state.data,pending=d.expenses.filter(e=>e.status==='PENDING');
  return head('审核中心','查看全部触发原因，核实后再通过或驳回')+card('待审核账单',table(['账单','提交人','金额','审核原因','操作'],pending.map(e=>[esc(e.title),esc(memberName(e.submitter)),yuan(e.amount),esc(reasons(e.reasons)),button('查看并审核','detail',e.id)]),'暂无待审核账单'))+card('结算后更正申请',table(['原账单','申请人','更正原因','目标金额','状态','操作'],(d.adjustments||[]).map(a=>[esc(a.title),esc(memberName(a.requester)),esc(a.reason),yuan(a.replacement.amount),badge(a.status),a.status==='PENDING'?button('通过','approve-adjustment',a.id)+ ' '+button('驳回','reject-adjustment',a.id):'已处理'])));
}
function settlement() {
  const d=state.data,canPay=d.membership?.role!=='READER';
  return head('债务结算','保留原始债务关系 · 付款标记后由收款人确认，才抵扣债务')+(!activeMember()?'<div class="notice">成员关系已终止，仅展示和处理与你相关的结算。</div>':'')+card('原始债务明细',table(['来源账单','付款人','收款人','原金额','已确认','剩余','待确认','操作'],(d.debts||[]).map(row=>[esc(row.title)+(row.adjustment_id?'<br><span class="muted">关联更正调整</span>':''),esc(memberName(row.debtor)),esc(memberName(row.creditor)),yuan(row.amount),yuan(row.paid),yuan(row.remaining),yuan(row.reserved),row.debtor===d.user.id&&canPay&&row.remaining>row.reserved?button('标记付款','pay',row.id):row.remaining===0?'<span class="badge">已结清</span>':'—'])))+card('付款与收款确认',table(['付款人','收款人','金额','标记时间','状态','操作'],(d.payments||[]).map(p=>[esc(memberName(p.debtor)),esc(memberName(p.creditor)),yuan(p.amount),time(p.created_at),p.status==='PENDING'?'<span class="badge PENDING">待收款确认</span>':badge(p.status),p.status==='PENDING'&&p.creditor===d.user.id&&canPay?button('确认收到','confirm',p.id)+' '+button('未收到','reject-payment',p.id):'—'])));
}
function stats() {
  const s=state.data.statistics;if(!s)return settlement();
  const total=s.expense||1;
  return head('统计报表','仅统计已审核通过的当前有效版本；收入与支出分别展示')+`<div class="metrics">${[['有效支出',s.expense],['有效收入',s.income],['待收款总额',s.members.reduce((n,m)=>n+m.receivable,0)],['待付款总额',s.members.reduce((n,m)=>n+m.payable,0)]].map(([label,value])=>`<div class="card metric"><p class="muted">${label}</p><div class="value">${yuan(value)}</div></div>`).join('')}</div><div class="equal-columns">${card('分类支出',s.categories.map(c=>`<div class="chart-row"><span>${esc(c.name)}</span><div class="track"><span style="width:${c.expense/total*100}%"></span></div><span>${yuan(c.expense)}</span></div>`).join(''))}${card('分类预算使用',budgets())}</div>`+card('成员统计',table(['成员','垫付','承担','实际收款','收入受益','应收','应付'],s.members.map(m=>[esc(m.username),yuan(m.contributed),yuan(m.borne),yuan(m.contributed_income),yuan(m.borne_income),yuan(m.receivable),yuan(m.payable)])));
}
function members() {
  const d=state.data;
  return head('成员管理','三类角色协作，历史账单与债务在退出后继续保留',canAdmin()?'<button class="primary" data-action="invite" '+(!openLedger()?'disabled':'')+'>＋ 邀请成员</button>':'')+card('账本成员',table(['成员','角色','状态','加入时间','操作'],d.members.map(m=>[esc(m.username)+(m.user_id===d.user.id?'（我）':''),labels[m.role],labels[m.status],time(m.joined_at),canAdmin()&&m.status==='ACTIVE'?button('设置角色','role',m.user_id)+' '+button('移除','remove',m.user_id):'—'])))+card('退出账本','<p class="muted">待审核记录及应收应付全部完成后可退出。唯一管理员需要先指定其他管理员。</p><button data-action="leave" class="danger">退出当前账本</button>');
}
function settings() {
  const d=state.data;if(!canAdmin())return head('账本设置','只有管理员可以修改设置。');
  return head('账本设置与日志','管理预算、账本状态，追溯每一次关键操作')+`<div class="equal-columns">${card('分类预算与大额阈值',table(['类别','预算','大额阈值','操作'],d.categories.map(c=>[esc(c.name),c.budget===null?'未设置':yuan(c.budget),c.threshold===null?'未设置':yuan(c.threshold),button('编辑','category',c.id)]))+'<button data-action="category" style="margin-top:20px" '+(!openLedger()?'disabled':'')+'>添加消费类别</button>')}${card('账本状态',`<p>当前状态：${badge(d.ledger.status)}</p><p class="muted">关闭后禁止新增普通收支和邀请成员，但允许审核及结算。重新开启后恢复记账。</p><button data-action="status" class="${openLedger()?'':'primary'}">${openLedger()?'关闭账本':'重新开启账本'}</button>`)}</div>`+card('关键操作日志',table(['时间','操作人','动作','对象','原因'],(d.logs||[]).slice().reverse().map(log=>[time(log.created_at),esc(memberName(log.actor)),esc(log.action),`<span title="${esc(log.object_id)}">${esc(log.object_id.slice(0,10))}…</span>`,esc(log.reason)||'—'])));
}
function detail() {
  const e=state.detail;if(!e)return '<p class="empty">正在读取账单…</p>';
  const own=canWrite()&&(e.submitter===state.data.user.id||canAdmin());
  return head('账单详情与更正','消费、分摊、审核和历史版本均可追溯','<button data-page="expenses">返回明细</button>')+`<div class="two-columns">${card(esc(e.title),`<dl class="details"><dt>金额</dt><dd class="preview-total">${yuan(e.amount)}</dd><dt>状态</dt><dd>${badge(e.status)}</dd><dt>收支类型</dt><dd>${labels[e.kind]}</dd><dt>类别</dt><dd>${esc(categoryName(e.category_id))}</dd><dt>提交人</dt><dd>${esc(memberName(e.submitter))}</dd><dt>消费时间</dt><dd>${time(e.occurred_at)}</dd><dt>审核原因</dt><dd>${esc(reasons(e.reasons))}</dd><dt>当前版本</dt><dd>${e.version}</dd><dt>账单 ID</dt><dd>${esc(e.id)}</dd>${e.parent_id?`<dt>原账单</dt><dd>${button('查看原版本','detail',e.parent_id)}</dd>`:''}</dl><div class="actions">${canAdmin()&&e.status==='PENDING'?button('审核通过','approve',e.id)+' '+button('驳回','reject',e.id):''}${own&&e.status==='APPROVED'?button('修改 / 申请更正','amend',e.id):''}${own&&['APPROVED','PENDING'].includes(e.status)?button(e.status==='PENDING'?'撤回账单':'作废账单','void',e.id):''}</div>`)}<div>${card('垫付明细',table(['成员','垫付金额'],Object.entries(e.contributions).map(([id,amount])=>[esc(memberName(id)),yuan(amount)])))}${card('承担明细',table(['成员','分摊金额'],Object.entries(e.shares).map(([id,amount])=>[esc(memberName(id)),yuan(amount)])))}</div></div>`+card('审核记录',table(['时间','审核人','结果','意见'],e.reviews.map(r=>[time(r.created_at),r.reviewer?esc(memberName(r.reviewer)):'系统自动审核',badge(r.decision),esc(r.reason)])))+card('历史与调整',`<p class="muted">保留 ${e.history.length} 个历史快照，${e.adjustments.length} 个调整申请。已结算更正由差额债务体现，原付款不变。</p>${table(['时间','原因','状态'],e.adjustments.map(a=>[time(a.created_at),esc(a.reason),badge(a.status)]))}`);
}
function dialog(title, body, submit) {
  const box=$('#dialog');
  $('#dialog-content').innerHTML=`<h2>${title}</h2><form id="dialog-form">${body}<div class="actions"><button type="button" id="dialog-cancel">取消</button><button type="submit" class="primary">确认</button></div></form>`;
  $('#dialog-cancel').onclick=()=>box.close();
  const requestId=crypto.randomUUID();
  $('#dialog-form').onsubmit=async event=>{event.preventDefault();const form=event.target;const control=$('[type=submit]',form);control.disabled=true;try{await submit(Object.fromEntries(new FormData(form)),requestId);box.close();toast('操作成功');await load();}catch(error){toast(error.message,true);}finally{control.disabled=false;}};
  box.showModal();
}
async function act(action,id) {
  const d=state.data;
  const mutate=(operation,data,requestId)=>api(operation,{...data,ledger_id:state.ledger,request_id:requestId});
  const reasonField=()=>field('原因 / 意见',input('reason','请填写操作原因'));
  const simple=(title,operation,data)=>dialog(title,reasonField(),(values,key)=>mutate(operation,{...data,...values},key));
  if(action==='logout'){await api('logout',{});state.ledger=null;login();return;}
  if(action==='detail'){state.detail=await api('detail?id='+encodeURIComponent(id));state.page='detail';render();return;}
  if(action==='settlement'){state.page='settlement';render();return;}
  if(action==='reset-filter'){state.filtered=null;render();return;}
  if(action==='create'){dialog('创建新账本',field('账本名称',input('name','例如：秋日旅行账本')),async(values,key)=>{state.ledger=await mutate('create',values,key);state.page='overview';});return;}
  if(action==='invite'){dialog('邀请已有账号',field('用户名',input('username','输入对方注册时的用户名'))+field('成员角色',select('role',[['MEMBER','普通成员'],['READER','只读成员']])),(values,key)=>mutate('invite',values,key));return;}
  if(action==='category'){
    const category=d.categories.find(c=>c.id===id);
    dialog(category?'编辑分类预算':'添加消费类别',field('类别名称',input('name','例如：餐饮','text',category?.name||''))+field('预算（元，留空为未设置）',input('budget','如 1000.00','text',category?.budget!=null?(category.budget/100).toFixed(2):'',false))+field('大额审核阈值（元，留空不启用）',input('threshold','如 500.00','text',category?.threshold!=null?(category.threshold/100).toFixed(2):'',false))+'<p class="muted">预算按整个账本累计支出计算，修改类别名称会新增另一类别。</p>',(values,key)=>mutate('category',values,key));return;
  }
  if(action==='role'){const member=d.members.find(m=>m.user_id===id);dialog('设置 '+esc(member.username)+' 的角色',field('角色',select('role',['ADMIN','MEMBER','READER'].map(role=>[role,labels[role]]),member.role)),(values,key)=>mutate('role',{id,...values},key));return;}
  if(action==='remove'){simple('移除成员（债务和历史继续保留）','remove',{id});return;}
  if(action==='leave'){dialog('退出当前账本','<p>退出后无法继续查看全部账目。未结事项会阻止退出。</p>',(_,key)=>mutate('leave',{},key));return;}
  if(action==='status'){simple(openLedger()?'关闭账本':'重新开启账本','status',{status:openLedger()?'CLOSED':'OPEN'});return;}
  if(action==='pay'){
    const debt=d.debts.find(row=>row.id===id);
    dialog('标记付款',`<p>收款人：${esc(memberName(debt.creditor))} · 可支付 ${yuan(debt.remaining-debt.reserved)}</p>`+field('本次付款金额（元）',input('amount','0.00','text',((debt.remaining-debt.reserved)/100).toFixed(2))), (values,key)=>mutate('pay',{id,...values},key));return;
  }
  if(['confirm','reject-payment'].includes(action)){simple(action==='confirm'?'确认收到付款':'拒绝收款确认','confirm',{id,accept:action==='confirm'});return;}
  if(['approve-adjustment','reject-adjustment'].includes(action)){simple('审核结算后更正','adjustment',{id,approve:action==='approve-adjustment'});return;}
  if(['approve','reject'].includes(action)){dialog(action==='approve'?'通过此账单':'驳回此账单',reasonField(),async(values,key)=>{await mutate('review',{id,approve:action==='approve',version:state.detail.version,...values},key);state.detail=await api('detail?id='+id);});return;}
  if(action==='void'){dialog('作废 / 撤回账单',reasonField(),async(values,key)=>{await mutate('void',{id,version:state.detail.version,...values},key);state.detail=await api('detail?id='+id);});return;}
  if(action==='amend'){state.editing=state.detail;state.page='new';render();}
}
function bindPage() {
  const filter=$('#filter-form');
  if(filter)filter.onsubmit=async event=>{
    event.preventDefault();const values=Object.fromEntries(new FormData(filter));const params=new URLSearchParams({ledger_id:state.ledger});
    for(const [key,value]of Object.entries(values)){if(value)params.set(key,['start','end'].includes(key)?new Date(value).toISOString():value);}
    const control=$('[type=submit]',filter);control.disabled=true;
    try{state.filtered=await api('expenses?'+params);$('#expense-results').innerHTML=table(['账单 / 时间','类别','提交人','金额','状态','操作'],expenseRows(state.filtered));}catch(error){toast(error.message,true);}finally{control.disabled=false;}
  };
  const form=$('#expense-form');if(!form)return;
  const requestId=crypto.randomUUID();
  function preview(){
    const mode=form.elements.mode.value,amount=form.elements.amount.value;
    const people=[...form.querySelectorAll('[data-person]:checked')].map(el=>el.dataset.person);
    for(const el of form.querySelectorAll('[data-share]'))el.disabled=mode==='equal'||!people.includes(el.dataset.share);
    if(!/^\d+(\.\d{1,2})?$/.test(amount)||!people.length){$('#split-preview').textContent='输入合法金额并选择参与成员后，可预览分摊。';return;}
    const total=Math.round(Number(amount)*100),base=Math.floor(total/people.length),rest=total%people.length;
    if(mode==='equal')$('#split-preview').textContent=people.map((id,index)=>memberName(id)+' '+yuan(base+(index<rest?1:0))).join(' / ');
    else {const sum=people.reduce((n,id)=>n+Number(form.querySelector(`[data-share="${id}"]`).value||0),0);$('#split-preview').textContent=mode==='ratio'?`比例合计 ${sum}%（应为 100%）`:`自定义金额合计 ${yuan(Math.round(sum*100))}（应为 ${yuan(total)}）`;}
  }
  form.oninput=preview;form.onchange=preview;preview();
  form.onsubmit=async event=>{
    event.preventDefault();const control=$('[type=submit]',form);control.disabled=true;
    const values=Object.fromEntries(new FormData(form));
    const contributions={};for(const el of form.querySelectorAll('[data-payer]')){if(el.value.trim()&&Number(el.value)!==0)contributions[el.dataset.payer]=el.value.trim();}
    const participants=[...form.querySelectorAll('[data-person]:checked')].map(el=>el.dataset.person);
    const splitValues={};for(const id of participants)splitValues[id]=form.querySelector(`[data-share="${id}"]`).value;
    try {
      const expense={title:values.title,amount:values.amount,category_id:values.category_id,kind:values.kind,occurred_at:new Date(values.occurred_at).toISOString(),contributions,participants,mode:values.mode};
      if(values.mode!=='equal')expense.values=splitValues;
      await api(state.editing?'amend':'submit',{ledger_id:state.ledger,expense,request_id:requestId,...(state.editing?{id:state.editing.id,version:state.editing.version,reason:values.reason}:{})});
      state.editing=null;state.filtered=null;state.page='expenses';toast('已保存，审核状态请在明细中查看');await load();
    }catch(error){toast(error.message,true);}finally{control.disabled=false;}
  };
}
document.addEventListener('click',async event=>{
  const pageButton=event.target.closest('[data-page]');
  if(pageButton&&!pageButton.disabled){state.page=pageButton.dataset.page;state.editing=null;state.filtered=null;render();return;}
  const actionButton=event.target.closest('[data-action]');if(!actionButton||actionButton.disabled)return;
  actionButton.disabled=true;try{await act(actionButton.dataset.action,actionButton.dataset.id);}catch(error){toast(error.message,true);}finally{actionButton.disabled=false;}
});
load();
