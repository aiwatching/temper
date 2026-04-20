package com.fortinet.nac.ingestion.consumer.handler;

import com.fortinet.nac.common.Constants;
import com.fortinet.nac.common.dto.IngestionRequest;
import com.fortinet.nac.common.util.StatusOr;
import com.fortinet.nac.ingestion.consumer.statemachine.EntityStateMachine;
import com.fortinet.nac.ingestion.consumer.handler.interfaces.FingerprintHandler;
import com.fortinet.nac.ingestion.entity.Entity;
import com.fortinet.nac.common.enums.EntityStage;
import com.fortinet.nac.ingestion.service.EntityService;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class DifferentAttributesWithLockAcquiredHandler implements FingerprintHandler {

    private final EntityService entityService;
    private final EntityStateMachine stateMachine;

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

        return stateMachine.start(req.getRequestId(), req.isAsync(), e);
    }
}
