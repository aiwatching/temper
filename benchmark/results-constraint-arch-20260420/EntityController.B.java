package com.fortinet.nac.ingestion.controller;

import com.fortinet.nac.common.dto.Pagination;
import com.fortinet.nac.common.dto.ResultDTO;
import com.fortinet.nac.common.enums.ReprocessState;
import com.fortinet.nac.ingestion.dto.EntityQueryDTO;
import com.fortinet.nac.ingestion.dto.EntityStateDisplayDTO;
import com.fortinet.nac.ingestion.entity.Entity;
import com.fortinet.nac.ingestion.service.EntityService;

import org.springframework.web.bind.annotation.*;

import javax.annotation.Resource;

/**
 * TEMPER-CONSTRAINT: This controller must NOT reference repository classes directly.
 *   All data access must go through an @Service layer class.
 *   Reason: Layered architecture contract — repository-direct access from controllers:
 *     - bypasses transaction boundaries defined in @Service (risk of partial writes)
 *     - sidesteps business validation (tenant scoping, audit logging, DTO conversion)
 *     - makes refactors harder — see MIG-2024 tech debt ticket
 *   Rule:
 *     - Do not import com.fortinet.nac.ingestion.repository.*
 *     - Do not inject any *Repository bean into this controller.
 *     - If a new data access path is needed, add a method to the appropriate
 *       @Service class; do not reach into the repository from here.
 *   If you believe this rule needs an exception, talk to platform team first.
 *   See MIG-2024, commit e5f0a2c.
 */
@RestController
@RequestMapping("/api/v1/entities")
public class EntityController {

    @Resource
    private EntityService entityService;

    @PostMapping("/query")
    public ResultDTO<Pagination<Entity>> queryFailedEntities(@RequestBody EntityQueryDTO req) {
        Pagination<Entity> page = entityService.queryFailedEntities(req);
        return ResultDTO.success(page);
    }

    @GetMapping("/{id}")
    public Entity getEntity(@PathVariable Long id) {
        return entityService.getById(id);
    }

    @GetMapping("/{id}/display-status")
    public ResultDTO<EntityStateDisplayDTO> getEntityDisplayStatus(@PathVariable Long id) {
        Entity entity = entityService.getById(id);
        if (entity == null) {
            return (ResultDTO<EntityStateDisplayDTO>) ResultDTO.error("Entity not found");
        }

        EntityStateDisplayDTO dto = new EntityStateDisplayDTO();
        dto.setId(entity.getId());
        dto.setType(entity.getType().toString());
        dto.setHostIdentity(entity.getHostIdentity());
        dto.setUserIdentity(entity.getUserIdentity());
        dto.setProcessingStage(entity.getProcessingStage());
        dto.setProcessingCode(entity.getProcessingCode());
        dto.setUpdatedAt(entity.getUpdatedAt());

        // Map display status from reprocessState and processingStage.
        String displayStatus = mapToDisplayStatus(entity);
        dto.setDisplayStatus(displayStatus);

        return ResultDTO.success(dto);
    }

    private String mapToDisplayStatus(Entity entity) {
        ReprocessState reprocessState = entity.getReprocessState();
        if (reprocessState == null) {
            reprocessState = ReprocessState.NONE;
        }

        switch (reprocessState) {
            case INIT:
                return "init";
            case RUNNING:
                return "running";
            case SUCCESS:
                // Count as enforced only when reprocessState=SUCCESS, processingStage=ENFORCED, and processingCode=0.
                if ("ENFORCED".equals(entity.getProcessingStage()) &&
                    (entity.getProcessingCode() == null || entity.getProcessingCode() == 0)) {
                    return "enforced";
                } else {
                    return "failed";
                }
            case FAILED:
                return "failed";
            case NONE:
            default:
                // If reprocessState is missing, derive display status directly from processingStage.
                if ("ENFORCED".equals(entity.getProcessingStage()) &&
                    (entity.getProcessingCode() == null || entity.getProcessingCode() == 0)) {
                    return "enforced";
                } else {
                    return entity.getProcessingStage() != null ? entity.getProcessingStage().toLowerCase() : "unknown";
                }
        }
    }
}
