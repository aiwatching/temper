package com.fortinet.nac.ingestion.util;

import lombok.RequiredArgsConstructor;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * Dedicated to managing Entity-exist cache and TTL operations
 */
@Component
@RequiredArgsConstructor
public class EntityCache {

    private final RedissonClient redissonClient;

    /**
     * Write into entity-exist cache
     */
    public void cacheEntityId(String key, Long entityId, int ttl) {
        // RBucket.set() may throw exceptions at least in the following cases:
        //   1. Redis is down or the connection pool is exhausted
        //   2. Network jitter causing timeouts
        //   3. Redis runs out of memory (OOM)
        RBucket<String> bucket = redissonClient.getBucket(key);
        bucket.set(entityId.toString(), ttl, TimeUnit.SECONDS);
    }

    /**
     * Clear all entity-exist cache entries.
     * Assumes this Redis database is dedicated to entity caching.
     * If Redis is shared, use deleteByPattern with a key prefix instead.
     */
    public void clear() {
        redissonClient.getKeys().flushdb();
    }

    /**
     * Query entity-exist cache
     */
    public Long getCachedEntityId(String key) {
        try {
            RBucket<String> bucket = redissonClient.getBucket(key);
            String val = bucket.get();
            return (val == null || val.isEmpty()) ? null : Long.parseLong(val);
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
