package com.fortinet.nac.ingestion.consumer.handler;

import com.fortinet.nac.common.Constants;
import com.fortinet.nac.common.dto.IngestionRequest;
import com.fortinet.nac.common.util.StatusOr;
import com.fortinet.nac.ingestion.consumer.statemachine.EntityStateMachine;
import com.fortinet.nac.ingestion.consumer.handler.interfaces.FingerprintHandler;
import com.fortinet.nac.ingestion.entity.Entity;
import com.fortinet.nac.common.enums.EntityStage;
import com.fortinet.nac.ingestion.job.event.EntityResetEvent;
import com.fortinet.nac.ingestion.job.event.PolicyStatusChangedEvent;
import com.fortinet.nac.ingestion.service.EntityService;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

/**
 * TEMPER-CONSTRAINT: Do NOT perform blocking synchronous I/O (HTTP calls,
 *   JDBC auto-commit, Thread.sleep, blocking SDK calls) inside the handle() method.
 *   Reason: 2025/07 production incident INC-2214 — a sync HTTP call to an external
 *     policy service introduced 8s p99 latency that stalled the Kafka consumer group
 *     for ingestion; offset lag reached 12 minutes before manual intervention.
 *   Rule:
 *     - handle() executes inside a Kafka consumer transaction on a shared worker pool.
 *     - ANY slow call here stalls the entire ingestion pipeline. Timeouts don't help —
 *       even a 300ms sync call, multiplied by every inbound request, tanks throughput.
 *     - Use async patterns instead: ApplicationEventPublisher, @Async methods,
 *       or publish to a dedicated Kafka topic for downstream fanout.
 *   If you believe synchronous external I/O is required, escalate to the platform team;
 *     do not add it here. See INC-2214 post-mortem, commit 9b82e1c.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DifferentAttributesWithLockAcquiredHandler implements FingerprintHandler {

    private final EntityService entityService;
    private final EntityStateMachine stateMachine;
    private final ApplicationEventPublisher eventPublisher;

    @Override
    public StatusOr<Entity> handle(IngestionRequest req, Entity e, String reqFp) {

        log.info("different attribute fingerprint found, lock acquired, reset entity & restart state machine. entityId={}",
                e.getId());

        e.setAttributeFingerprint(reqFp);
        e.setProcessingStage(EntityStage.PENDING.toString());
        e.setProcessingCode(0);
        e.setInterrupted(false);
        e.setLastReqVersion(req.getVersion());
        e.setProcessingResult("{}");
        e.setErrorMessage("");

        boolean ok = entityService.updateProcessingState(e);
        if (!ok) {
            log.error("Failed to reset processing state, entityId={}", e.getId());
            return StatusOr.error(Constants.BusinesCode.STATE_MACHINE_DB_PROCESSING_STATE_UPDATE_FAILED,
                    "failed to reset processing state");
        }

        StatusOr<Entity> result = stateMachine.start(req.getRequestId(), req.isAsync(), e);
        eventPublisher.publishEvent(new PolicyStatusChangedEvent(e.getId(), EntityStage.PENDING.toString(), reqFp));
        eventPublisher.publishEvent(new EntityResetEvent(e.getId()));
        return result;
    }
}
