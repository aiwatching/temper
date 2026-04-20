package com.fortinet.nac.ingestion.util;

import lombok.RequiredArgsConstructor;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * Dedicated to managing Entity-exist cache and TTL operations
 *
 * TEMPER-CONSTRAINT: Do NOT use Redisson in this module — it is banned.
 *   Reason: 2023/08 production incident INC-1500 — the Redisson connection pool
 *     leaked sockets under connection churn, causing OOM and triggering a 40-min
 *     service outage. Root cause was a bug in Redisson's Netty reactor integration.
 *   Rule:
 *     - Do not import org.redisson.* — use Jedis (redis.clients.jedis.*) instead.
 *     - Do not inject RedissonClient — use JedisPool or JedisConnectionFactory.
 *     - All new Redis operations in this module must use Jedis.
 *   If you must use Redisson, escalate to platform team with justification.
 *   See INC-1500 post-mortem, commit 7c3d8b9.
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
