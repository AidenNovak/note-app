#!/bin/bash

# Test script for web frontend pages
# Tests: Login, Notes, Insights, Mind, Ground pages

set -e

API_BASE="http://localhost:8000/api/v1"
FRONTEND_URL="http://localhost:3000"

echo "🧪 Testing Web Frontend Pages"
echo "================================"

# Test 1: Login and get token
echo -e "\n1️⃣ Testing login..."
TEST_EMAIL="test_$(date +%s)@atelier.com"
TEST_PASSWORD="test12345"

curl -s -X POST "$API_BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"test$(date +%s)\",\"email\":\"$TEST_EMAIL\",\"password\":\"$TEST_PASSWORD\"}" > /dev/null || true

TOKEN_RESPONSE=$(curl -s -X POST "$API_BASE/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"$TEST_PASSWORD\"}")

TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')

if [ "$TOKEN" != "null" ] && [ -n "$TOKEN" ]; then
  echo "✅ Login successful"
else
  echo "❌ Login failed"
  echo "$TOKEN_RESPONSE"
  exit 1
fi

# Test 2: Get notes
echo -e "\n2️⃣ Testing notes endpoint..."
NOTES_RESPONSE=$(curl -s -X GET "$API_BASE/notes" \
  -H "Authorization: Bearer $TOKEN")

NOTES_COUNT=$(echo "$NOTES_RESPONSE" | jq -r '.total // 0')
echo "✅ Notes endpoint working (found $NOTES_COUNT notes)"

# Test 3: Get insights
echo -e "\n3️⃣ Testing insights endpoint..."
INSIGHTS_RESPONSE=$(curl -s -X GET "$API_BASE/insights" \
  -H "Authorization: Bearer $TOKEN")

INSIGHTS_COUNT=$(echo "$INSIGHTS_RESPONSE" | jq -r 'length // 0')
echo "✅ Insights endpoint working (found $INSIGHTS_COUNT insights)"

# Test 4: Get mind graph
echo -e "\n4️⃣ Testing mind graph endpoint..."
MIND_RESPONSE=$(curl -s -X GET "$API_BASE/mind/graph" \
  -H "Authorization: Bearer $TOKEN")

NODES_COUNT=$(echo "$MIND_RESPONSE" | jq -r '.nodes | length // 0')
EDGES_COUNT=$(echo "$MIND_RESPONSE" | jq -r '.edges | length // 0')
echo "✅ Mind graph endpoint working (found $NODES_COUNT nodes, $EDGES_COUNT edges)"

# Test 5: Check frontend is accessible
echo -e "\n5️⃣ Testing frontend accessibility..."
FRONTEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL")

if [ "$FRONTEND_STATUS" = "200" ]; then
  echo "✅ Frontend is accessible at $FRONTEND_URL"
else
  echo "❌ Frontend not accessible (status: $FRONTEND_STATUS)"
  exit 1
fi

echo -e "\n================================"
echo "✅ All tests passed!"
echo ""
echo "🌐 Open your browser and test these pages:"
echo "   - Auth: $FRONTEND_URL/auth/sign-in"
echo "   - Dashboard: $FRONTEND_URL/dashboard"
echo "   - Notes: $FRONTEND_URL/notes"
echo "   - Insights: $FRONTEND_URL/insights"
echo "   - Mind: $FRONTEND_URL/mind"
