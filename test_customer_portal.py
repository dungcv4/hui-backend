#!/usr/bin/env python3
"""
Customer Portal API Test Script
Tests all customer portal endpoints end-to-end
"""
import requests
import json
import sys

BASE = "http://localhost:8000/api"
PHONE = "0912345999"
PASSWORD = "123456"

def pretty(data):
    return json.dumps(data, indent=2, ensure_ascii=False)

def test(name, method, url, headers=None, json_data=None, params=None, expect_status=200):
    try:
        resp = getattr(requests, method)(url, headers=headers, json=json_data, params=params, timeout=10)
        status = "✅" if resp.status_code == expect_status else "❌"
        print(f"{status} {name} [{resp.status_code}]")
        if resp.status_code != expect_status:
            print(f"   Expected {expect_status}, got {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
            return None
        try:
            data = resp.json()
            return data
        except Exception:
            return resp.text
    except Exception as e:
        print(f"❌ {name} [ERROR: {e}]")
        return None

print("=" * 60)
print("CUSTOMER PORTAL API TESTS")
print("=" * 60)

# 1. Login
print("\n--- Authentication ---")
data = test("POST /customer/auth/login", "post",
    f"{BASE}/customer/auth/login",
    json_data={"phone": PHONE, "password": PASSWORD})

if not data or "access_token" not in data:
    print("❌ Cannot proceed without token")
    sys.exit(1)

TOKEN = data["access_token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
print(f"   Token: {TOKEN[:30]}...")
print(f"   Member: {data['member']['name']} ({data['member']['phone']})")

# 2. Get Me
data = test("GET /customer/auth/me", "get",
    f"{BASE}/customer/auth/me", headers=AUTH)
if data:
    print(f"   Name: {data.get('name')}, Phone: {data.get('phone')}")

# 3. Wrong password
test("POST /customer/auth/login (wrong password)", "post",
    f"{BASE}/customer/auth/login",
    json_data={"phone": PHONE, "password": "wrongpassword"},
    expect_status=401)

# 4. Unauthorized access
test("GET /customer/auth/me (no token)", "get",
    f"{BASE}/customer/auth/me", expect_status=401)

print("\n--- Dashboard ---")
# 5. Dashboard
data = test("GET /customer/dashboard", "get",
    f"{BASE}/customer/dashboard", headers=AUTH)
if data:
    print(f"   Member: {data.get('member_name')}")
    print(f"   Hui groups: {data.get('total_hui_groups')}")
    print(f"   Due today: {data.get('total_due_today')}")
    print(f"   Receive today: {data.get('total_receive_today')}")
    print(f"   Total paid all-time: {data.get('total_paid_all_time')}")
    print(f"   Total received all-time: {data.get('total_received_all_time')}")
    print(f"   Net position: {data.get('financial_summary', {}).get('net_position')}")
    if data.get('next_payout'):
        np = data['next_payout']
        print(f"   Next payout: {np.get('group_name')} kỳ {np.get('cycle_number')} ~{np.get('estimated_amount')}")
    if data.get('upcoming_payments'):
        print(f"   Upcoming payments: {len(data['upcoming_payments'])} items")

print("\n--- Statistics ---")
# 6. Statistics (NEW)
data = test("GET /customer/statistics", "get",
    f"{BASE}/customer/statistics", headers=AUTH)
if data:
    print(f"   Total paid: {data.get('total_paid')}")
    print(f"   Total received: {data.get('total_received')}")
    print(f"   Net position: {data.get('net_position')}")
    print(f"   Total remaining: {data.get('total_remaining')}")
    print(f"   Payment counts: {data.get('payment_count')}")
    per_group = data.get('per_group', [])
    print(f"   Groups: {len(per_group)}")
    for g in per_group[:3]:
        print(f"     - {g.get('group_name')}: paid={g.get('total_paid')}, received={g.get('total_received')}, remaining={g.get('remaining_amount')}")
    monthly = data.get('monthly_chart', [])
    print(f"   Monthly chart: {len(monthly)} months")
    if monthly:
        last = monthly[-1]
        print(f"     Latest: {last.get('month_label')} paid={last.get('paid')} received={last.get('received')}")

print("\n--- Calendar ---")
# 7. Calendar (NEW)
data = test("GET /customer/calendar", "get",
    f"{BASE}/customer/calendar", headers=AUTH, params={"months": 3})
if data:
    events = data.get('events', [])
    print(f"   Events (next 3 months): {len(events)}")
    for e in events[:5]:
        emoji = "💰" if e.get('type') == 'receive' else "💸"
        print(f"     {emoji} {e.get('group_name')} kỳ {e.get('cycle_number')}: {e.get('amount')} ({e.get('date', 'N/A')[:10]})")

print("\n--- Hui Groups ---")
# 8. List hui groups
data = test("GET /customer/hui-groups", "get",
    f"{BASE}/customer/hui-groups", headers=AUTH)
if data and isinstance(data, list):
    print(f"   Found {len(data)} hui group(s)")
    for g in data:
        print(f"   - {g.get('hui_group_name')}: cycle {g.get('current_cycle')}/{g.get('total_cycles')}, {g.get('amount_per_cycle')}đ/kỳ")
        # 9. Detail for each group
        detail = test(f"GET /customer/hui-groups/{g['hui_group_id']}", "get",
            f"{BASE}/customer/hui-groups/{g['hui_group_id']}", headers=AUTH)
        if detail:
            timeline = detail.get('schedule_timeline', [])
            financial = detail.get('financial', {})
            print(f"      Timeline: {len(timeline)} cycles")
            print(f"      Financial: paid={financial.get('total_paid')}, received={financial.get('total_received')}, remaining={financial.get('remaining_amount')}")

print("\n--- Profile ---")
# 10. Get profile
data = test("GET /customer/profile", "get",
    f"{BASE}/customer/profile", headers=AUTH)
if data:
    print(f"   Name: {data.get('name')}, Phone: {data.get('phone')}, Memberships: {data.get('total_memberships')}")

# 11. Update profile
data = test("PUT /customer/profile", "put",
    f"{BASE}/customer/profile", headers=AUTH,
    json_data={"name": "Nguyễn Test Updated", "email": "test@huipro.vn"})
if data:
    print(f"   Updated: {data.get('message')}")

# 12. Restore original name
test("PUT /customer/profile (restore)", "put",
    f"{BASE}/customer/profile", headers=AUTH,
    json_data={"name": "Nguy"})

print("\n--- Payments ---")
# 13. Payment history (with pagination)
data = test("GET /customer/payments", "get",
    f"{BASE}/customer/payments", headers=AUTH, params={"skip": 0, "limit": 10})
if data and isinstance(data, list):
    print(f"   Found {len(data)} payment(s) (page 1, limit 10)")
    for p in data[:3]:
        print(f"   - {p.get('hui_group_name')} kỳ {p.get('cycle_number')}: {p.get('amount')}đ [{p.get('payment_status')}]")

# 14. Payments page 2
data2 = test("GET /customer/payments (page 2)", "get",
    f"{BASE}/customer/payments", headers=AUTH, params={"skip": 10, "limit": 10})
if data2 and isinstance(data2, list):
    print(f"   Page 2: {len(data2)} payment(s)")

print("\n--- QR Code ---")
# 15. Batch QR
data = test("GET /customer/qr-batch", "get",
    f"{BASE}/customer/qr-batch", headers=AUTH)
if data:
    print(f"   Total due: {data.get('total_due')}")
    if data.get('qr_data'):
        print(f"   QR URL: {data['qr_data'].get('qr_url', 'N/A')[:80]}...")

print("\n--- Change Password ---")
# 16. Change password (wrong current)
test("PUT /customer/auth/change-password (wrong current)", "put",
    f"{BASE}/customer/auth/change-password", headers=AUTH,
    json_data={"current_password": "wrongpass", "new_password": "newpass123"},
    expect_status=400)

# 17. Change password (success)
data = test("PUT /customer/auth/change-password", "put",
    f"{BASE}/customer/auth/change-password", headers=AUTH,
    json_data={"current_password": PASSWORD, "new_password": "newpass123"})
if data:
    print(f"   Result: {data.get('message')}")

# 18. Login with new password
data = test("POST /customer/auth/login (new password)", "post",
    f"{BASE}/customer/auth/login",
    json_data={"phone": PHONE, "password": "newpass123"})

# 19. Restore original password
if data and "access_token" in data:
    NEW_AUTH = {"Authorization": f"Bearer {data['access_token']}"}
    test("PUT /customer/auth/change-password (restore)", "put",
        f"{BASE}/customer/auth/change-password", headers=NEW_AUTH,
        json_data={"current_password": "newpass123", "new_password": PASSWORD})

print("\n" + "=" * 60)
print("ALL TESTS COMPLETE")
print("=" * 60)
