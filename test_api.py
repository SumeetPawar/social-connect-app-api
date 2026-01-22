import requests
import json

base_url = "http://127.0.0.1:8001"

print("Testing FastAPI endpoints with database...\n")

# Test 1: Health Check
print("1. Testing health endpoint...")
response = requests.get(f"{base_url}/health")
print(f"   Status: {response.status_code}")
print(f"   Response: {response.json()}\n")

# Test 2: Register a new user (database INSERT)
print("2. Testing user registration (database INSERT)...")
user_data = {
    "email": "test@example.com",
    "password": "TestPassword123",
    "name": "Test User"
}
response = requests.post(f"{base_url}/auth/signup", json=user_data)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    print(f"   ✅ User registered successfully!")
    print(f"   Response: {json.dumps(response.json(), indent=2)}\n")
elif response.status_code == 400:
    print(f"   ℹ️  User already exists (expected if running multiple times)")
    print(f"   Response: {response.json()}\n")
else:
    print(f"   ❌ Error: {response.json()}\n")

# Test 3: Login (database SELECT)
print("3. Testing user login (database SELECT)...")
login_data = {
    "email": "test@example.com",
    "password": "TestPassword123"
}
response = requests.post(f"{base_url}/auth/login", json=login_data)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   ✅ Login successful!")
    print(f"   Access Token: {data.get('access_token', '')[:50]}...")
    print(f"   User ID: {data.get('user', {}).get('id')}")
    print(f"   User Email: {data.get('user', {}).get('email')}\n")
    
    access_token = data.get('access_token')
    
    # Test 4: Get user profile (authenticated request)
    print("4. Testing authenticated endpoint (database SELECT with auth)...")
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{base_url}/me", headers=headers)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print(f"   ✅ Profile retrieved successfully!")
        print(f"   Response: {json.dumps(response.json(), indent=2)}\n")
    else:
        print(f"   ❌ Error: {response.json()}\n")
else:
    print(f"   ❌ Login failed: {response.json()}\n")

print("✅ All endpoint tests completed!")
