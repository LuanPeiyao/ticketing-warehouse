import test from 'node:test';
import assert from 'node:assert/strict';
import {cents, decimal, money, total, escapeHTML, countdown, RequestKeys} from '../../ticketing/web/lib.mjs';

test('按分精确汇总票价，避免 0.1 + 0.2 的浮点误差',()=>{
  assert.equal(total(['0.10','0.20','100.00']),'100.30');
  assert.equal(cents('0.01'),1n);
  assert.equal(decimal(1n),'0.01');
  assert.equal(total([]),'0.00');
});

test('多座位大金额仍然精确，不经过 Number 转换',()=>{
  assert.equal(total(['9999999999.99','9999999999.99']),'19999999999.98');
  assert.equal(money('999999999999.99'),'¥999,999,999,999.99');
});

test('金额格式拒绝负数、特殊值和超过分的精度',()=>{
  for(const value of ['NaN','Infinity','-1','1.001','1e3','', '01.00']) assert.throws(()=>cents(value));
  assert.throws(()=>decimal(-1n));
});

test('网络结果未确认时复用原请求编号，确认后新操作获得新编号',()=>{
  let serial=0; const keys=new RequestKeys(()=>`request-${++serial}`);
  const payload={amount:'220.00'};
  assert.equal(keys.get('pay:1',payload),'request-1');
  assert.equal(keys.get('pay:1',payload),'request-1');
  assert.notEqual(keys.get('pay:2',payload),'request-1');
  keys.forget('pay:1',payload);
  assert.equal(keys.get('pay:1',payload),'request-3');
});

test('不同选择或退款原因使用独立请求编号，退出清除缓存',()=>{
  let serial=0;const keys=new RequestKeys(()=>String(++serial));
  assert.notEqual(keys.get('reserve',{ids:[1]}),keys.get('reserve',{ids:[2]}));
  assert.notEqual(keys.get('refund:1',{reason:'行程变化'}),keys.get('refund:1',{reason:'其他原因'}));
  const first=keys.get('pay:1',{}); keys.clear(); assert.notEqual(keys.get('pay:1',{}),first);
});

test('来自活动名称、显示名称和退款原因的 HTML 特殊字符会被转义',()=>{
  assert.equal(escapeHTML('<img src=x onerror="bad()"> & \'test\''),'&lt;img src=x onerror=&quot;bad()&quot;&gt; &amp; &#39;test&#39;');
  assert.equal(escapeHTML(undefined),'');
});

test('截止时间采用绝对时刻计算，跨时区且到期后不会出现负倒计时',()=>{
  const now=Date.parse('2026-11-02T10:00:00Z');
  assert.equal(countdown('2026-11-02T18:10:00+08:00',now),'10:00');
  assert.equal(countdown('2026-11-02T09:59:59Z',now),'00:00');
  assert.equal(countdown('2026-11-02T10:00:00.001Z',now),'00:01');
});
