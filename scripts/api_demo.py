"""只依赖 Python 标准库的本地 HTTP 演示，不输出密码或登录令牌。"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.request
from uuid import uuid4


def run(base_url):
    def call(method, path, body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(base_url.rstrip('/') + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                value = response.read()
                return json.loads(value) if value else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HTTP {exc.code}: {exc.read().decode()}') from exc

    def account(label):
        password = secrets.token_urlsafe(20)
        email = 'api-demo-' + uuid4().hex + '@example.com'
        user = call('POST', '/auth/register', {'email': email, 'display_name': label, 'password': password})
        token = call('POST', '/auth/login', {'email': email, 'password': password})['access_token']
        return user, token

    sessions = call('GET', '/sessions?limit=100')
    # 优先使用项目专用演示活动，避免占用其他教学场次。
    sessions.sort(key=lambda s: s['title'] != '订单事务演示音乐会')
    target = None
    for session in sessions:
        available = [s for s in call('GET', f"/sessions/{session['session_id']}/seats") if s['status'] == 'AVAILABLE']
        if len(available) >= 2:
            target = session, available[:2]
            break
    if not target:
        raise RuntimeError('没有两个可售座位，请先运行 db-admin demo 或释放演示库存')
    session, inventory = target
    events = {'session_id': session['session_id'], 'inventory_ids': [s['session_seat_id'] for s in inventory]}
    accounts, created = [], []
    try:
        user, token = account('API 演示用户 A')
        accounts.append(token)
        order = call('POST', '/orders', {'session_id': session['session_id'],
            'inventory_ids': events['inventory_ids'], 'request_id': str(uuid4())}, token)
        created.append((token, order['order_id']))
        path = f"/orders/{order['order_id']}"
        events['reserved'] = order
        events['failed_payment'] = call('POST', path+'/payments', {'amount': order['total_amount'],
            'request_id': str(uuid4()), 'outcome': 'FAILED'}, token)
        payment = {'amount': order['total_amount'], 'request_id': str(uuid4())}
        events['paid'] = call('POST', path+'/payments', payment, token)
        assert events['paid'] == call('POST', path+'/payments', payment, token)
        refund = {'reason': 'HTTP 完整演示退款', 'request_id': str(uuid4())}
        events['refunded'] = call('POST', path+'/refunds', refund, token)
        other, other_token = account('API 演示用户 B')
        accounts.append(other_token)
        second = call('POST', '/orders', {'session_id': session['session_id'],
            'inventory_ids': events['inventory_ids'], 'request_id': str(uuid4())}, other_token)
        created.append((other_token, second['order_id']))
        second_path = f"/orders/{second['order_id']}"
        events['resold'] = call('POST', second_path+'/payments',
            {'amount': second['total_amount'], 'request_id': str(uuid4())}, other_token)
        events['old_refund_replay'] = call('POST', path+'/refunds', refund, token)
        current = call('GET', f"/sessions/{session['session_id']}/seats")
        assert all(s['status'] == 'SOLD' for s in current if s['session_seat_id'] in events['inventory_ids'])
        events['second_refund'] = call('POST', second_path+'/refunds',
            {'reason': 'HTTP 演示结束释放座位', 'request_id': str(uuid4())}, other_token)
        events['final_inventory'] = [s for s in call('GET', f"/sessions/{session['session_id']}/seats")
                                     if s['session_seat_id'] in events['inventory_ids']]
        assert all(s['status'] == 'AVAILABLE' for s in events['final_inventory'])
        assert Decimal(events['paid']['payment']['amount']) == sum(Decimal(s['price']) for s in inventory)
        events['users'] = [user, other]
        return events
    finally:
        # 只收尾本脚本创建的订单；失败时也尽量避免留下占用。
        for token, order_id in created:
            try:
                order = call('GET', f'/orders/{order_id}', token=token)
                if order['status'] == 'PENDING':
                    call('POST', f'/orders/{order_id}/cancel', token=token)
                elif order['status'] == 'PAID':
                    call('POST', f'/orders/{order_id}/refunds',
                         {'reason': '演示异常收尾', 'request_id': str(uuid4())}, token)
            except (RuntimeError, urllib.error.URLError):
                pass
        for token in accounts:
            try:
                call('POST', '/auth/logout', token=token)
            except (RuntimeError, urllib.error.URLError):
                pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, help='可选 JSON 验证记录，包含演示订单，不含密码或令牌')
    args = parser.parse_args()
    data = run(args.base_url)
    if args.output:
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(json.dumps({'order_ids': [data['reserved']['order_id'], data['resold']['order']['order_id']],
        'final_status': data['second_refund']['order']['status'],
        'inventory_status': [s['status'] for s in data['final_inventory']],
        'note': '完整 HTTP 演示完成，两个订单均已退款，两个会话均已退出'}, ensure_ascii=False, indent=2))
