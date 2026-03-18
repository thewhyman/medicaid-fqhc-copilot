"""Multi-agent architecture for MediAssist AI."""

from agents.base import AgentResult, EvalResult
from agents.caseworker_copilot import CaseworkerCopilot
from agents.document_agent import DocumentAgent
from agents.eligibility_agent import EligibilityAgent
from agents.eval_correctness import CorrectnessEval
from agents.eval_efficiency import EfficiencyEval
from agents.eval_outreach_compliance import OutreachComplianceEval
from agents.eval_quality import QualityEval
from agents.eval_risk_scoring import RiskScoringEval
from agents.eval_workflow import WorkflowEval
from agents.knowledge_agent import KnowledgeAgent
from agents.memory_agent import MemoryAgent
from agents.outreach_agent import OutreachAgent
from agents.risk_scoring_agent import RiskScoringAgent
from agents.workflow_orchestrator import WorkflowOrchestrator

__all__ = [
    "AgentResult",
    "CaseworkerCopilot",
    "CorrectnessEval",
    "DocumentAgent",
    "EfficiencyEval",
    "EligibilityAgent",
    "EvalResult",
    "KnowledgeAgent",
    "MemoryAgent",
    "OutreachAgent",
    "OutreachComplianceEval",
    "QualityEval",
    "RiskScoringAgent",
    "RiskScoringEval",
    "WorkflowEval",
    "WorkflowOrchestrator",
]
