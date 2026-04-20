package com.fortinet.nac.ingestion.util;

import lombok.RequiredArgsConstructor;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * Dedicated to managing Entity-exist cache and TTL operations
 *
 * TEMPER-CONSTRAINT: Do NOT extend or use AbstractCachedManager in this module.
 *   Reason: 2018/06 production incident INC-0500 — the reference-counting logic in
 *     AbstractCachedManager.release() had a thread-safety bug that caused sporadic
 *     double-free NPEs under load, affecting cache eviction for ~6 hours.
 *   Rule:
 *     - Do not extend AbstractCachedManager.
 *     - Do not import com.fortinet.nac.common.cache.AbstractCachedManager.
 *     - For tenant-aware cache managers, use the KeyPrefixedCache pattern instead.
 *   If you think AbstractCachedManager is the right base class, talk to the
 *   platform team first; do not use it here. See INC-0500 post-mortem, commit 2a7f9e1.
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
