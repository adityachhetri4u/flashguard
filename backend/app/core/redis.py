import redis
from app.core.config import settings

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# LUA Script for atomic reservation
# Keys: 1. inventory_key, 2. idempotency_key
# Args: 1. quantity, 2. reservation_id
# Returns:
# 1 if successful
# 0 if out of stock
# -1 if idempotency key exists
RESERVE_SCRIPT = """
local inventory_key = KEYS[1]
local idempotency_key = KEYS[2]

local qty = tonumber(ARGV[1])
local res_id = ARGV[2]

-- Check Idempotency
local exists = redis.call("GET", idempotency_key)
if exists then
    return -1
end

-- Check Inventory
local current_inventory = tonumber(redis.call("GET", inventory_key))
if current_inventory == nil then
    return 0
end

if current_inventory >= qty then
    redis.call("DECRBY", inventory_key, qty)
    redis.call("SET", idempotency_key, res_id, "EX", 3600) -- store idempotency key for 1 hour
    return 1
else
    return 0
end
"""

# Register Lua script
reserve_lua = redis_client.register_script(RESERVE_SCRIPT)

def get_redis():
    return redis_client
