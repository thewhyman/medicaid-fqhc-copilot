"""Outreach Compliance Eval: verify TCPA compliance rules."""

from agents.base import EvalResult
from agents.outreach_agent import OutreachAgent, TEMPLATES


class OutreachComplianceEval:
    """Eval agent for TCPA compliance in outreach messaging."""

    def __init__(self):
        self.agent = OutreachAgent()

    def check_opt_out_blocks(self) -> EvalResult:
        """Verify that opted-out patients cannot receive messages."""
        patient = {"consent_status": "opted_out", "first_name": "Test"}
        renewal = {"communication_log": []}
        result = self.agent.check_can_send(patient, renewal)
        blocked = not result.data["can_send"]
        return EvalResult(
            passed=blocked,
            dimension="tcpa_opt_out",
            details="Opted-out patient was " + ("blocked" if blocked else "NOT blocked"),
            data={"can_send": result.data["can_send"], "reason": result.data.get("reason")},
        )

    def check_pending_consent_blocks(self) -> EvalResult:
        """Verify that patients without explicit consent cannot receive messages."""
        patient = {"consent_status": "pending", "first_name": "Test"}
        renewal = {"communication_log": []}
        result = self.agent.check_can_send(patient, renewal)
        blocked = not result.data["can_send"]
        return EvalResult(
            passed=blocked,
            dimension="tcpa_consent_required",
            details="Pending-consent patient was " + ("blocked" if blocked else "NOT blocked"),
            data={"can_send": result.data["can_send"]},
        )

    def check_stop_text_in_templates(self) -> EvalResult:
        """Verify every message template contains opt-out language."""
        missing = []
        for name, langs in TEMPLATES.items():
            for lang, template in langs.items():
                stop_words = ("STOP", "ALTO") if lang == "es" else ("STOP",)
                if not any(word in template for word in stop_words):
                    missing.append(f"{name}/{lang}")

        return EvalResult(
            passed=len(missing) == 0,
            dimension="tcpa_stop_text",
            details=f"Missing STOP text in: {missing}" if missing else "All templates have STOP text",
            data={"missing": missing, "total_templates": sum(len(v) for v in TEMPLATES.values())},
        )

    def check_spanish_templates_exist(self) -> EvalResult:
        """Verify all templates have Spanish translations."""
        missing_es = [name for name, langs in TEMPLATES.items() if "es" not in langs]
        return EvalResult(
            passed=len(missing_es) == 0,
            dimension="tcpa_multilingual",
            details=f"Missing Spanish: {missing_es}" if missing_es else "All templates have ES",
            data={"missing": missing_es},
        )

    def check_response_processing(self) -> EvalResult:
        """Verify STOP responses are correctly processed as opt-out."""
        stop_messages = ["STOP", "stop", "Alto", "UNSUBSCRIBE", "CANCEL"]
        failures = []
        for msg in stop_messages:
            result = self.agent.process_response(msg)
            if result.data.get("action") != "opt_out":
                failures.append(f"'{msg}' → {result.data.get('action')} (expected opt_out)")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="tcpa_response_processing",
            details="; ".join(failures) if failures else "All STOP variants processed correctly",
            data={"tested": len(stop_messages), "failures": failures},
        )
