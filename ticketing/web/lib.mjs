export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
}

export function cents(value) {
  const text = String(value);
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(text)) throw new Error('金额格式不正确');
  const [integer, decimal = ''] = text.split('.');
  return BigInt(integer) * 100n + BigInt(decimal.padEnd(2, '0'));
}

export function decimal(value) {
  if (typeof value !== 'bigint' || value < 0n) throw new Error('金额不能为负');
  return `${value / 100n}.${String(value % 100n).padStart(2, '0')}`;
}

export function money(value) {
  const amount = cents(value);
  return `¥${(amount / 100n).toLocaleString('zh-CN')}.${String(amount % 100n).padStart(2, '0')}`;
}

export function total(prices) {
  return decimal(prices.reduce((sum, value) => sum + cents(value), 0n));
}

export function countdown(expires, now = Date.now()) {
  const seconds = Math.max(0, Math.ceil((Date.parse(expires) - now) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

// 未确认的请求在当前页面保留编号；相同动作和内容的重试使用同一个 UUID。
export class RequestKeys {
  constructor(make = () => crypto.randomUUID()) { this.keys = new Map(); this.make = make; }
  get(action, payload) {
    const key = action + ':' + JSON.stringify(payload);
    if (!this.keys.has(key)) this.keys.set(key, this.make());
    return this.keys.get(key);
  }
  forget(action, payload) { this.keys.delete(action + ':' + JSON.stringify(payload)); }
  clear() { this.keys.clear(); }
}
