MemoryNode
{
    id,
    category_id,
    content,

    type: [
        FACT,
        OBSERVATION,
        HYPOTHESIS,
        CONSTRAINT,
        ACTION,
        ERROR,
        DECISION,
        STATE,
        RESULT
    ],

    metadata: {
        title,
        description,

        status: [
            ACTIVE,
            TENTATIVE,
            CONFIRMED,
            REJECTED,
            SUPERSEDED,
            RESOLVED,
            FAILED,
            BLOCKED,
            VALIDATED
        ],

        avoid: [
            REPEAT,
            REREAD,
            REWRITE,
            RETHINK
        ],

        confidence,
        source,
        created_at,
        updated_at
    }
}


MemoryEdge
{
    id,
    source,
    target,

    relation: [
        SUPPORTS,
        CONTRADICTS,
        TESTED_BY,
        PRODUCED,
        SUCCEEDED_WITH,
        FAILED_BECAUSE,
        BLOCKED_BY,
        DEPENDS_ON,
        SUPERSEDES,
        VALIDATES
    ],

    metadata: {
        confidence,

        evidence_strength: [
            WEAK,
            MEDIUM,
            STRONG
        ],

        direct,
        source,
        created_at
    }
}