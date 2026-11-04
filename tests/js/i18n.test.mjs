import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createI18n, messages, languages, normalizeLanguage, readLanguage, writeLanguage, preferenceKey} from '../../ticketing/web/i18n.mjs';

test('简、繁、英词典完整，参数占位符一致',()=>{
  const placeholders=text=>[...text.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort();
  for (const [source,translations] of Object.entries(messages)) {
    assert.equal(translations.length,2,source);
    for(const language of languages) {
      const text=createI18n(language).t(source);
      assert.ok(text.trim(),source);
      assert.deepEqual(placeholders(text),placeholders(source),source);
      if(language==='en') assert.doesNotMatch(text,/[\u3400-\u9fff]/,source);
    }
  }
});

test('页面及动态中文界面原文都已登记翻译',()=>{
  const html=readFileSync(new URL('../../ticketing/web/index.html',import.meta.url),'utf8');
  for(const match of html.matchAll(/data-i18n(?:-aria|-placeholder|-content)?="([^"]+)"/g)) {
    assert.ok(messages[match[1]],match[1]);
  }
  const app=readFileSync(new URL('../../ticketing/web/app.js',import.meta.url),'utf8');
  for(const match of app.matchAll(/'((?:\\.|[^'\\])*)'/g)) {
    if(/[\u3400-\u9fff]/.test(match[1])) assert.ok(messages[match[1]],match[1]);
  }
});

test('切换语言只改变界面表达，不改动用户提供的名称',()=>{
  const i18n=createI18n();
  const name='用户 <A> & B';
  assert.equal(i18n.t('你好，{name}',{name}),`你好，${name}`);
  i18n.setLanguage('zh-Hant');
  assert.equal(i18n.t('取消订单'),'取消訂單');
  i18n.setLanguage('en');
  assert.equal(i18n.t('你好，{name}',{name}),`Hello, ${name}`);
  assert.equal(i18n.t('订单号 {number}',{number:'O123'}),'Order O123');
});

test('只保存有效语言偏好，刷新后可以恢复',()=>{
  const saved=new Map(); const storage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  assert.equal(readLanguage(storage),'zh-Hans');
  writeLanguage(storage,'zh-Hant');assert.equal(saved.get(preferenceKey),'zh-Hant');
  assert.equal(readLanguage(storage),'zh-Hant');
  writeLanguage(storage,'en');assert.equal(readLanguage(storage),'en');
  saved.set(preferenceKey,'unsupported');assert.equal(readLanguage(storage),'zh-Hans');
  assert.deepEqual([...saved.keys()],[preferenceKey]);
  assert.equal(normalizeLanguage(null),'zh-Hans');
});

test('浏览器禁用存储时仍可切换，不会导致页面启动失败',()=>{
  const blocked={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}};
  assert.equal(readLanguage(blocked),'zh-Hans');
  assert.doesNotThrow(()=>writeLanguage(blocked,'en'));
  const i18n=createI18n(readLanguage(blocked));i18n.setLanguage('en');
  assert.equal(i18n.t('我的订单'),'My orders');
});

test('日期随语言格式化，始终保持北京时间和真实时刻',()=>{
  const time='2026-11-09T11:00:00Z';
  const simplified=createI18n('zh-Hans').date(time,true);
  const traditional=createI18n('zh-Hant').date(time,true);
  const english=createI18n('en').date(time,true);
  for(const text of [simplified,traditional,english]) assert.match(text,/19:00/);
  assert.match(simplified,/11月/);assert.match(traditional,/11月/);assert.match(english,/November/);
  assert.notEqual(english,simplified);
});

test('错误状态和业务冲突按所选语言解释，不泄漏未知服务端原文',()=>{
  const en=createI18n('en');
  assert.match(en.apiError({status:401,code:'invalid_credentials'}),/email or password/);
  assert.match(en.apiError({status:429,code:'login_throttled'}),/one minute/);
  assert.match(en.apiError({status:409,code:'business_conflict',sourceMessage:'支付金额与订单价格快照总额不一致'}),/amount/);
  assert.match(en.apiError({status:409,code:'business_conflict',sourceMessage:'场次已经开始，当前规则不支持退款'}),/started/);
  assert.doesNotMatch(en.apiError({status:503,sourceMessage:'内部配置和中文数据库细节'}),/[\u3400-\u9fff]/);
  assert.match(createI18n('zh-Hant').apiError({status:401,code:'invalid_token'}),/登入/);
});


test('英文数量使用正确的单复数',()=>{
  const en=createI18n('en');
  assert.equal(en.t('{count} 个活动',{count:1}),'1 event');
  assert.equal(en.t('{count} 个活动',{count:2}),'2 events');
  assert.equal(en.t('{count} 个可选场次',{count:1}),'1 session');
  assert.equal(en.t('{count} 张',{count:2}),'2 tickets');
});
