import {escapeHTML as e, money, total, countdown, RequestKeys} from './lib.mjs';

const $ = selector => document.querySelector(selector);
const state = {token:null, user:null, sessions:[], activities:new Map(), orders:[], orderDetails:new Map(),
  view:'discover', filter:'ALL', group:[], session:null, seats:[], selected:new Set(), authMode:'login',
  busy:false, authBusy:false, catalogBusy:false, orderBusy:false, seatVersion:0, orderVersion:0, detailVersion:0,
  keys:new RequestKeys(), action:null, toastTimer:null, catalogMore:false, ordersMore:false, expiredChecked:new Map()};
const labels = {PENDING:'待支付', PAID:'已支付', REFUNDED:'已退款', CANCELLED:'已取消', EXPIRED:'已过期'};
const historyLabels = {PENDING:'创建订单，座位已保留', PAID:'模拟支付成功，电子票已签发', REFUNDED:'整单退款成功，电子票已失效', CANCELLED:'订单已取消，座位已释放', EXPIRED:'订单超时，座位已释放'};
const badge = status => `<span class="status-badge status-${status==='PENDING'?'pending':status==='PAID'?'paid':status==='REFUNDED'?'refunded':'closed'}">${e(labels[status] || status)}</span>`;
const date = (value, withYear=false) => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', ...(withYear?{year:'numeric'}:{}), month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));

class APIError extends Error {
  constructor(message, status=0, code='') { super(message); this.status=status; this.code=code; }
}

function notify(message) {
  $('#toast').textContent = message; $('#toast').hidden=false;
  clearTimeout(state.toastTimer); state.toastTimer=setTimeout(()=>{$('#toast').hidden=true;}, 4500);
}

function showError(selector, message) {
  const element=$(selector); element.textContent=message || ''; element.hidden=!message;
}

function feedback(selector, message='', isError=false) {
  $(selector).innerHTML = message ? `<div class="feedback ${isError?'error':''}">${e(message)}</div>` : '';
}

function empty(title, description, button='') {
  return `<div class="empty-state"><div class="empty-symbol" aria-hidden="true">✳</div><h3>${e(title)}</h3><p>${e(description)}</p>${button}</div>`;
}

async function api(path, {method='GET', body, authenticated=false}={}) {
  const token=state.token;
  const headers={Accept:'application/json'};
  if (body !== undefined) headers['Content-Type']='application/json';
  if (authenticated) {
    if (!token) throw new APIError('请先登录，再继续操作。',401);
    headers.Authorization='Bearer '+token;
  }
  let response;
  try { response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'}); }
  catch { throw new APIError(method==='GET'?'暂时无法连接服务，请检查网络后重试。':'未能确认操作结果，请先查看我的订单，或使用相同操作重试。'); }
  let data=null;
  if (response.status!==204) {
    try { data=await response.json(); }
    catch { throw new APIError('服务返回了无法识别的结果，请刷新订单确认后重试。'); }
  }
  if (!response.ok) {
    if (response.status===401 && authenticated && state.token===token) {
      clearIdentity(); notify('登录已失效，请重新登录。');
    }
    const code=data?.error?.code;
    const message=response.status===422?'请检查填写内容：邮箱格式、密码长度、金额和选座需要符合要求。':data?.error?.message || '操作未完成，请稍后重试。';
    throw new APIError(message,response.status,code);
  }
  return data;
}

function updateIdentity() {
  $('#login-button').hidden=Boolean(state.user);
  $('#logout-button').hidden=!state.user;
  $('#account-name').hidden=!state.user;
  $('#account-name').textContent=state.user ? `你好，${state.user.display_name}` : '';
  $('#logout-button').disabled=state.busy;
  renderSelection();
}

function clearIdentity() {
  state.token=null; state.user=null; state.orders=[]; state.orderDetails.clear(); state.keys.clear();
  state.ordersMore=false; state.orderVersion++; state.orderBusy=false; state.action=null;
  for (const id of ['detail-dialog','action-dialog']) if ($('#'+id).open) $('#'+id).close();
  updateIdentity(); if (state.view==='orders') renderOrders();
}

function openAuth(mode='login', context='登录后，开启你的下一场期待。') {
  setAuthMode(mode); $('#auth-context').textContent=context;
  if (!$('#auth-dialog').open) $('#auth-dialog').showModal();
  $('#auth-email').focus();
}

function setAuthMode(mode, clearError=true) {
  if (state.authBusy) return;
  state.authMode=mode; const registration=mode==='register';
  $('#auth-login-tab').setAttribute('aria-pressed',String(!registration));
  $('#auth-register-tab').setAttribute('aria-pressed',String(registration));
  $('#name-field').hidden=!registration; $('#auth-name').required=registration;
  $('#password-hint').hidden=!registration;
  $('#auth-password').minLength=registration?10:1;
  $('#auth-password').autocomplete=registration?'new-password':'current-password';
  $('#auth-password').placeholder=registration?'设置 10～128 字符的密码':'请输入密码';
  $('#auth-submit').textContent=registration?'注册并登录':'登录'; if (clearError) showError('#auth-error','');
}

$('#auth-form').addEventListener('submit',async event=>{
  event.preventDefault(); if (state.authBusy) return;
  state.authBusy=true; $('#auth-submit').disabled=true; $('#auth-submit').textContent='正在处理…'; showError('#auth-error','');
  const email=$('#auth-email').value.trim(), password=$('#auth-password').value;
  try {
    if (state.authMode==='register') {
      await api('/auth/register',{method:'POST',body:{email,display_name:$('#auth-name').value.trim(),password}});
      // 注册成功后改为登录模式；即使接下来的网络请求中断，也不会重复注册。
      state.authMode='login';
    }
    const result=await api('/auth/login',{method:'POST',body:{email,password}});
    state.token=result.access_token;
    try { state.user=await api('/auth/me',{authenticated:true}); }
    catch (error) { state.token=null; throw error; }
    $('#auth-password').value=''; $('#auth-dialog').close(); updateIdentity();
    notify(`欢迎，${state.user.display_name}。`);
    if (state.view==='orders') await loadOrders();
  } catch (error) { showError('#auth-error',error.message); }
  finally {
    state.authBusy=false; $('#auth-submit').disabled=false; setAuthMode(state.authMode,false);
  }
});

$('#login-button').addEventListener('click',()=>openAuth());
$('#auth-login-tab').addEventListener('click',()=>setAuthMode('login'));
$('#auth-register-tab').addEventListener('click',()=>setAuthMode('register'));
$('#logout-button').addEventListener('click',async()=>{
  if (state.busy) return;
  $('#logout-button').disabled=true;
  try { await api('/auth/logout',{method:'POST',authenticated:true}); clearIdentity(); notify('已退出登录。'); }
  catch (error) { notify(error.message); }
  finally { $('#logout-button').disabled=false; }
});

function navigate() {
  const view=location.hash==='#orders'?'orders':'discover'; const changed=state.view!==view; state.view=view;
  if (changed) window.scrollTo(0,0);
  $('#discover-view').hidden=view!=='discover'; $('#orders-view').hidden=view!=='orders';
  document.querySelectorAll('[data-nav]').forEach(a=>{
    if (a.dataset.nav===view) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  if (view==='orders') { renderOrders(); if (state.token) loadOrders(); }
}
window.addEventListener('hashchange',navigate);

async function loadCatalog(append=false) {
  if (state.catalogBusy) return;
  state.catalogBusy=true; $('#refresh-catalog').disabled=true; $('#more-events').disabled=true;
  if (!state.sessions.length) feedback('#catalog-feedback','正在寻找即将开场的活动…');
  try {
    const offset=append?state.sessions.length:0;
    const [sessions, activities]=await Promise.all([api(`/sessions?limit=100&offset=${offset}`),api('/activities?limit=100')]);
    activities.forEach(activity=>state.activities.set(activity.activity_id,activity));
    state.sessions=append?[...state.sessions,...sessions]:sessions;
    state.catalogMore=sessions.length===100;
    feedback('#catalog-feedback',''); renderCatalog();
  } catch (error) { feedback('#catalog-feedback',error.message,true); }
  finally { state.catalogBusy=false; $('#refresh-catalog').disabled=false; $('#more-events').disabled=false; }
}

function renderCatalog() {
  const query=$('#event-search').value.trim().toLowerCase(); const groups=new Map();
  state.sessions.filter(s=>`${s.title} ${s.venue_name}`.toLowerCase().includes(query)).forEach(session=>{
    if (!groups.has(session.activity_id)) groups.set(session.activity_id,[]);
    groups.get(session.activity_id).push(session);
  });
  $('#activity-count').textContent=`${groups.size} 个活动`;
  $('#more-events').hidden=!state.catalogMore;
  if (!groups.size) {
    $('#events').innerHTML=empty(query?'暂时没有找到相关活动':'下一场精彩，正在准备中',query?'试试其他活动名称或场馆关键词。':'目前没有可售的未来场次，请稍后刷新查看。');
    return;
  }
  $('#events').innerHTML=[...groups.entries()].map(([id,sessions],index)=>{
    const first=sessions[0], description=state.activities.get(id)?.description;
    const venues=[...new Set(sessions.map(s=>s.venue_name))];
    return `<article class="event-card"><div class="event-cover cover-${index%3}" aria-hidden="true"><span class="cover-label">LIVE EXPERIENCE</span><span class="cover-symbol">${['✳','◒','✦'][index%3]}</span><span class="cover-bottom">LIVE MOMENTS</span></div><div class="event-info"><span class="event-type">即将开场</span><h3>${e(first.title)}</h3><p>${e(description || '选择适合你的场次，为下一份期待预留一个座位。')}</p><div class="event-meta"><span>◷ ${sessions.length} 个可选场次</span><span>⌖ ${e(venues.join(' / '))}</span></div></div><div class="event-sessions">${sessions.map(s=>`<button class="session-choice" data-session="${s.session_id}" aria-label="选择 ${e(s.title)} ${e(date(s.start_time))} 场次"><strong>${e(date(s.start_time))}</strong><small>查看座位与票价</small><span class="choice-arrow" aria-hidden="true">↗</span></button>`).join('')}</div></article>`;
  }).join('');
}

$('#event-search').addEventListener('input',renderCatalog);
$('#refresh-catalog').addEventListener('click',()=>loadCatalog());
$('#more-events').addEventListener('click',()=>loadCatalog(true));
$('#events').addEventListener('click',event=>{
  const target=event.target.closest('[data-session]'); if (target) openSeats(Number(target.dataset.session));
});

function openSeats(sessionId) {
  const session=state.sessions.find(s=>s.session_id===sessionId); if (!session) return;
  state.group=state.sessions.filter(s=>s.activity_id===session.activity_id);
  $('#session-select').innerHTML=state.group.map(s=>`<option value="${s.session_id}">${e(date(s.start_time,true))} · ${e(s.venue_name)}</option>`).join('');
  $('#session-select').value=String(sessionId); state.selected.clear(); state.seats=[];
  showError('#reserve-error',''); $('#seat-dialog').showModal(); loadSeats(sessionId);
}

async function loadSeats(sessionId=state.session?.session_id, quiet=false) {
  if (!sessionId || state.busy) return;
  const version=++state.seatVersion;
  const previous=state.session?.session_id; state.session=state.group.find(s=>s.session_id===sessionId);
  if (!state.session) return;
  if (previous!==sessionId) { state.selected.clear(); state.seats=[]; showError('#reserve-error',''); }
  $('#seat-title').textContent=state.session.title;
  $('#seat-venue').textContent=state.session.venue_name;
  $('#refresh-seats').disabled=true;
  if (!quiet) { feedback('#seat-feedback','正在加载座位…'); $('#seat-map').innerHTML=''; }
  renderSelection();
  try {
    const seats=await api(`/sessions/${sessionId}/seats?limit=100`);
    if (version!==state.seatVersion) return;
    // 库存接口分页；大场馆也完整取回，选座不会遗漏后续页面。
    let all=seats, page=seats;
    while (page.length===100) {
      page=await api(`/sessions/${sessionId}/seats?limit=100&offset=${all.length}`);
      if (version!==state.seatVersion) return;
      all=all.concat(page);
    }
    const kept=new Set([...state.selected].filter(id=>all.some(s=>s.session_seat_id===id&&s.status==='AVAILABLE')));
    if (kept.size!==state.selected.size) notify('部分已选座位状态发生变化，请重新确认。');
    state.selected=kept; state.seats=all; feedback('#seat-feedback',''); renderSeatMap(); renderSelection();
  } catch (error) { if (version===state.seatVersion) feedback('#seat-feedback',error.message,true); }
  finally { if (version===state.seatVersion) $('#refresh-seats').disabled=false; }
}

function renderSeatMap() {
  if (!state.seats.length) { $('#seat-map').innerHTML='<p class="muted">本场暂无已配置的可选座位。</p>'; return; }
  const rows=new Map();
  state.seats.forEach(seat=>{ if (!rows.has(seat.row_label)) rows.set(seat.row_label,[]); rows.get(seat.row_label).push(seat); });
  $('#seat-map').innerHTML=[...rows.entries()].map(([row,seats])=>`<div class="seat-row"><span class="row-label">${e(row)} 排</span>${seats.sort((a,b)=>a.seat_number-b.seat_number).map(seat=>`<button class="seat-button" data-seat="${seat.session_seat_id}" ${seat.status==='AVAILABLE'?'':'disabled'} aria-pressed="${state.selected.has(seat.session_seat_id)}" aria-label="座位 ${e(seat.seat_code)}，${e(money(seat.price))}，${seat.status==='AVAILABLE'?'可选':seat.status==='SOLD'?'已售出':'暂时占用'}"><span>${e(seat.seat_code)}</span><small>${seat.status==='AVAILABLE'?e(money(seat.price)):seat.status==='SOLD'?'已售':'占用'}</small></button>`).join('')}</div>`).join('');
}

function renderSelection() {
  const selected=state.seats.filter(s=>state.selected.has(s.session_seat_id));
  $('#selected-seats').innerHTML=selected.length?selected.map(s=>`<div class="selection-line"><span>${e(s.seat_code)} 座</span><span>${e(money(s.price))}</span></div>`).join(''):'<p class="selection-empty">点击左侧可选座位，<br>找到属于你的现场视角。</p>';
  $('#selection-total').textContent=money(total(selected.map(s=>s.price)));
  $('#reserve-button').disabled=!selected.length || state.busy;
  $('#reserve-button').textContent=state.busy?'正在处理…':!selected.length?'请先选择座位':state.user?'确认下单 →':'登录后继续下单 →';
  $('#session-select').disabled=state.busy;
}

$('#seat-map').addEventListener('click',event=>{
  const button=event.target.closest('[data-seat]'); if (!button || button.disabled || state.busy) return;
  const id=Number(button.dataset.seat);
  if (state.selected.has(id)) state.selected.delete(id);
  else if (state.selected.size<50) state.selected.add(id);
  else { notify('每笔订单最多选择 50 个座位。'); return; }
  showError('#reserve-error',''); renderSeatMap(); renderSelection();
});
$('#session-select').addEventListener('change',()=>loadSeats(Number($('#session-select').value)));
$('#refresh-seats').addEventListener('click',()=>loadSeats());
$('#reserve-button').addEventListener('click',async()=>{
  if (state.busy || !state.selected.size) return;
  if (!state.token) { openAuth('login','登录后返回选座，继续确认你的订单。'); return; }
  const token=state.token;
  const payload={session_id:state.session.session_id,inventory_ids:[...state.selected].sort((a,b)=>a-b)};
  const request_id=state.keys.get('reserve',payload);
  state.busy=true; updateIdentity(); showError('#reserve-error','');
  try {
    const order=await api('/orders',{method:'POST',body:{...payload,request_id},authenticated:true});
    if (state.token!==token) return;
    state.keys.forget('reserve',payload); state.selected.clear(); $('#seat-dialog').close();
    state.orderDetails.set(order.order_id,order); state.orders=[];
    location.hash='orders'; await loadOrders();
    notify(order.status==='PENDING'?'座位已为你保留，请在倒计时结束前完成模拟支付。':`原请求已处理，订单${labels[order.status]||''}，请查看订单状态。`);
  } catch (error) {
    showError('#reserve-error',error.message);
    if (error.status===409) { state.keys.forget('reserve',payload); }
  } finally { state.busy=false; updateIdentity(); renderOrders(); if ($('#seat-dialog').open) loadSeats(undefined,true); }
});

function renderOrders() {
  $('#refresh-orders').disabled=!state.token||state.orderBusy;
  $('#more-orders').hidden=!state.token||!state.ordersMore;
  if (!state.token) { $('#orders-list').innerHTML=empty('让每一份期待，都有归处','登录后即可查看自己的订单、电子票和退款进度。','<button class="button button-dark" data-open-login>登录查看订单 ↗</button>'); return; }
  const filtered=state.orders.filter(o=>state.filter==='ALL'||(state.filter==='CLOSED'?['CANCELLED','EXPIRED'].includes(o.status):o.status===state.filter));
  if (!filtered.length) {
    $('#orders-list').innerHTML=empty(state.orderBusy?'正在加载订单…':state.filter==='ALL'?'还没有订单，先去发现精彩':'这个分类下暂时没有订单',state.orderBusy?'请稍候。':'去挑选一场活动，留一份期待给自己。','<a class="button button-outline" href="#discover">发现活动 →</a>'); return;
  }
  $('#orders-list').innerHTML=filtered.map(order=>{
    const start=order.session_start_time, refundable=Date.parse(start)>Date.now();
    return `<article class="order-card"><div class="order-card-top"><span class="order-created">${e(date(order.created_at,true))} 创建</span>${badge(order.status)}</div><div class="order-card-main"><div><h3>${e(order.activity_title)}</h3><div class="order-meta"><span>◷ ${e(date(start,true))}</span><span>⌖ ${e(order.venue_name)}</span></div></div><div class="order-amount">${e(money(order.total_amount))}<small>订单总额</small></div></div><div class="order-bottom"><div>${order.status==='PENDING'?`<p class="countdown" data-countdown="${e(order.expires_at)}" data-order="${order.order_id}"></p>`:`<p class="order-number">订单号 ${e(order.order_no)}</p>`}</div><div class="order-actions"><button class="text-button" data-detail="${order.order_id}">${order.status==='PAID'?'查看电子票':'订单详情'}</button>${order.status==='PENDING'?`<button class="button button-outline" data-order-action="cancel" data-order="${order.order_id}" ${state.busy?'disabled':''}>取消订单</button><button class="button button-dark" data-order-action="pay" data-order="${order.order_id}" ${state.busy||Date.parse(order.expires_at)<=Date.now()?'disabled':''}>去支付</button>`:''}${order.status==='PAID'?`<button class="button button-outline" data-order-action="refund" data-order="${order.order_id}" ${state.busy||!refundable?'disabled':''}>${refundable?'申请退款':'场次已开始'}</button>`:''}</div></div></article>`;
  }).join('');
  updateCountdowns();
}

async function loadOrders(append=false, quiet=false) {
  if (!state.token || state.orderBusy) return;
  const token=state.token, version=++state.orderVersion;
  state.orderBusy=true; $('#refresh-orders').disabled=true; $('#more-orders').disabled=true;
  if (!quiet && !state.orders.length) { feedback('#orders-feedback','正在整理你的订单…'); renderOrders(); }
  try {
    const orders=await api(`/orders?limit=20&offset=${append?state.orders.length:0}`,{authenticated:true});
    if (state.token!==token || version!==state.orderVersion) return;
    state.orders=append?[...state.orders,...orders]:orders; state.ordersMore=orders.length===20;
    feedback('#orders-feedback',''); renderOrders();
  } catch (error) { if (state.token===token) feedback('#orders-feedback',error.message,true); }
  finally { if (version===state.orderVersion) { state.orderBusy=false; $('#refresh-orders').disabled=!state.token; $('#more-orders').disabled=false; renderOrders(); } }
}

function updateCountdowns() {
  const now=Date.now();
  document.querySelectorAll('[data-countdown]').forEach(element=>{
    const expired=Date.parse(element.dataset.countdown)<=now;
    element.textContent=expired?'支付时间已到，等待系统确认状态…':`请在 ${countdown(element.dataset.countdown,now)} 内完成支付`;
    if (expired) {
      const pay=$(`[data-order-action="pay"][data-order="${element.dataset.order}"]`); if(pay) pay.disabled=true;
      const last=state.expiredChecked.get(element.dataset.order)||0;
      if (now-last>6000 && !state.busy && !state.orderBusy) { state.expiredChecked.set(element.dataset.order,now); loadOrders(false,true); }
    }
  });
}

$('#refresh-orders').addEventListener('click',()=>loadOrders());
$('#more-orders').addEventListener('click',()=>loadOrders(true));
$('#order-filters').addEventListener('click',event=>{
  const target=event.target.closest('[data-filter]'); if (!target) return;
  state.filter=target.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===target)));
  renderOrders();
});
$('#orders-list').addEventListener('click',event=>{
  if (event.target.closest('[data-open-login]')) { openAuth(); return; }
  const detail=event.target.closest('[data-detail]'); if (detail) { openDetail(Number(detail.dataset.detail)); return; }
  const action=event.target.closest('[data-order-action]');
  if (action && !action.disabled) openAction(action.dataset.orderAction,Number(action.dataset.order));
});

async function openDetail(orderId) {
  const token=state.token, version=++state.detailVersion;
  $('#order-detail').innerHTML='<h2 id="detail-title">正在加载订单…</h2>'; $('#detail-dialog').showModal();
  try {
    const order=await api(`/orders/${orderId}`,{authenticated:true}); if (state.token!==token || version!==state.detailVersion || !$('#detail-dialog').open) return;
    state.orderDetails.set(orderId,order); renderDetail(order);
  } catch (error) { if ($('#detail-dialog').open && version===state.detailVersion && state.token===token) $('#order-detail').innerHTML=`<h2 id="detail-title">订单暂时无法显示</h2><p class="form-error">${e(error.message)}</p>`; }
}

function renderDetail(order) {
  $('#order-detail').innerHTML=`<p class="eyebrow">YOUR TICKET TO A GOOD TIME</p><h2 id="detail-title">${e(order.activity_title)}</h2>${badge(order.status)}<div class="detail-meta"><span>${e(date(order.session_start_time,true))}</span><span>${e(order.venue_name)}</span><span>座位：${order.items.map(i=>e(i.seat_code)).join('、')}</span><span class="order-number">订单号 ${e(order.order_no)}</span></div><div class="detail-heading"><h3>订单金额</h3><span class="detail-amount">${e(money(order.total_amount))}</span></div><hr class="detail-divider"><div class="detail-heading"><h3>电子票</h3><span class="muted">${order.tickets.length} 张</span></div><div class="ticket-list">${order.tickets.length?order.tickets.map(ticket=>{
    const item=order.items.find(i=>i.item_id===ticket.item_id);
    return `<article class="electronic-ticket ${ticket.status==='REFUNDED'?'is-refunded':''}"><div><h4>${e(item?.seat_code || '')} 座</h4><p>${e(order.venue_name)} · ${e(date(order.session_start_time))}</p><p>票号 ${e(ticket.ticket_no)}</p></div><div class="ticket-validity">${ticket.status==='VALID'?'电子票有效':'已退款失效'}</div></article>`;
  }).join(''):'<p class="muted">支付成功后，电子票会出现在这里。</p>'}</div><hr class="detail-divider"><h3>订单动态</h3><ol class="timeline">${order.history.map(h=>`<li>${e(historyLabels[h.to_status]||h.to_status)}<small>${e(date(h.changed_at,true))}</small></li>`).join('')}</ol>`;
}

function openAction(kind,orderId) {
  if (state.busy) return;
  const order=state.orders.find(o=>o.order_id===orderId); if (!order) return;
  state.action={kind,order}; showError('#action-error','');
  $('#refund-reason-field').hidden=kind!=='refund'; $('#refund-reason').required=kind==='refund'; $('#refund-reason').value='';
  $('#action-title').textContent=kind==='pay'?'确认这份期待':kind==='refund'?'申请整单退款':'取消这笔订单？';
  $('#action-eyebrow').textContent=kind==='pay'?'READY FOR YOUR NEXT MOMENT':kind==='refund'?'PLANS CAN CHANGE':'BEFORE YOU GO';
  $('#action-description').innerHTML=`<p class="action-description">${e(order.activity_title)}<br>${e(date(order.session_start_time,true))}</p>${kind!=='cancel'?`<p class="action-amount">${e(money(order.total_amount))}</p>`:''}<p class="action-notice">${kind==='pay'?'本次为模拟支付，不会产生真实扣款。支付成功后即可查看电子票。':kind==='refund'?'成功后，本订单的全部电子票将失效，座位恢复可售。退款为模拟操作。':'取消后，已保留的座位将立即释放，需要时可以重新选座。'}</p>`;
  $('#action-submit').textContent=kind==='pay'?'确认模拟支付':kind==='refund'?'确认全额退款':'确认取消';
  $('#action-submit').disabled=false; $('#action-dialog').showModal();
}

$('#action-form').addEventListener('submit',async event=>{
  event.preventDefault(); if (state.busy || !state.action) return;
  const {kind,order}=state.action, token=state.token;
  const reason=$('#refund-reason').value.trim();
  if (kind==='refund' && !reason) { showError('#action-error','请填写退款原因。'); return; }
  const payload=kind==='pay'?{amount:order.total_amount}:kind==='refund'?{reason}:null;
  const key=`${kind}:${order.order_id}`;
  const body=payload?{...payload,request_id:state.keys.get(key,payload)}:undefined;
  state.busy=true; $('#action-submit').disabled=true; $('#action-submit').textContent='正在处理…'; updateIdentity(); renderOrders(); showError('#action-error','');
  let completed=false;
  try {
    const result=await api(`/orders/${order.order_id}/${kind==='pay'?'payments':kind==='refund'?'refunds':'cancel'}`,{method:'POST',body,authenticated:true});
    if (state.token!==token) return;
    if (payload) state.keys.forget(key,payload);
    const next=result.order || result;
    state.orders=state.orders.map(o=>o.order_id===next.order_id?next:o); state.orderDetails.set(next.order_id,next);
    $('#action-dialog').close(); state.action=null; completed=true;
    notify(kind==='cancel'&&next.status==='PAID'?'订单已支付，不能取消；如有需要可申请退款。':next.status==='PAID'?'模拟支付成功，电子票已签发。':next.status==='REFUNDED'?'全额退款成功，电子票已失效。':next.status==='EXPIRED'?'订单已过期，座位已释放。':'订单已取消，座位已释放。');
    if (kind==='pay' && next.status==='PAID') {
      $('#order-detail').innerHTML=''; if (!$('#detail-dialog').open) $('#detail-dialog').showModal(); renderDetail(next);
    }
  } catch (error) { showError('#action-error',error.message); }
  finally {
    state.busy=false; updateIdentity(); renderOrders(); $('#action-submit').disabled=false;
    $('#action-submit').textContent=kind==='pay'?'重试模拟支付':kind==='refund'?'重试全额退款':'重试取消订单';
    if (completed) loadOrders(false,true);
  }
});

document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>{
  if (state.busy || state.authBusy) return; $('#'+button.dataset.close).close();
}));
document.querySelectorAll('dialog').forEach(dialog=>{
  dialog.addEventListener('cancel',event=>{if (state.busy || state.authBusy) event.preventDefault();});
  dialog.addEventListener('close',()=>{
    if (dialog.id==='auth-dialog') $('#auth-password').value='';
    if (dialog.id==='seat-dialog') { state.seatVersion++; state.selected.clear(); }
    if (dialog.id==='action-dialog') state.action=null;
    if (dialog.id==='detail-dialog') state.detailVersion++;
  });
});
window.setInterval(updateCountdowns,1000);
window.setInterval(()=>{
  if (document.hidden || state.busy) return;
  if (state.view==='orders' && state.token) loadOrders(false,true);
  if ($('#seat-dialog').open && !$('#auth-dialog').open) loadSeats(undefined,true);
},15000);
window.addEventListener('online',()=>{loadCatalog(); if (state.view==='orders'&&state.token) loadOrders();});
navigate(); updateIdentity(); loadCatalog();
