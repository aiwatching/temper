package com.fortinet.nac.ingestion.util;

import lombok.RequiredArgsConstructor;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * Dedicated to managing Entity-exist cache and TTL operations
 *
 * TEMPER-CONSTRAINT: Do NOT cache full Entity objects here — only cache entity IDs.
 *   Reason: 2024/03 production incident INC-1247 — stale full-Entity caches caused
 *     policy evaluation to use out-of-date attributes, affecting 1247 endpoints for ~4 hours.
 *   Rule:
 *     - This cache MUST only store entity IDs (Long) keyed by stable business identifiers.
 *     - Adding Map&lt;..., Entity&gt;, WeakHashMap&lt;..., Entity&gt;, Caffeine&lt;..., Entity&gt;, or any
 *       field that holds Entity instances is prohibited.
 *     - TTL-invalidated ID caching is fine; object caching is not.
 *   If you believe full-object caching is required, talk to the platform team first;
 *   do not implement it here. See INC-1247 post-mortem, commit a3f5d2e.
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
