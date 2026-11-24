"""
EMOG Response Arbitration — Cevap Denetim ve Karar Sistemi (Bölüm 8)

Ana modelin ürettiği cevap doğrudan kullanıcıya gönderilmez.
Önce NEURAL CORE (CK-0 Cognitive Kernel) tarafından değerlendirilir.

Dört temel karar:
  ACCEPT   -> Cevap kabul edilir, kullanıcıya gönderilir
  MODIFY   -> Cevap geliştirilir (düzeltmeyle) ve gönderilir
  REJECT   -> Cevap reddedilir (sadece sistem/program zararı riskindeyse)
  REGENERATE -> Yeni cevap üretilir (model yeniden çağrılır)

Karar önceliği (yüksekten düşüğe):
  1. Program/sistem zararı riski (sadece gerçekten tehlikeli işlemler)
  2. Kullanıcının güncel ve açık talimatı
  3. Kullanıcıya özel öğrenilmiş tercihler (CK-0 preferences)
  4. Ana modelin genel önerileri

Bu sistem sansürsüzdir: içerik/küfür/güvenlik uyarıları direkt RED değildir;
sadece programınıza zarar verebilecek kod (silme, format, veri çorbası vb.)
durumunda REJECT uygulanır. Diğerleri MODIFY/REGENERATE ile iyileştirilir.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_app.cognitive_kernel import ck0

logger = logging.getLogger(__name__)


class ArbitrationDecision:
    ACCEPT = "ACCEPT"
    MODIFY = "MODIFY"
    REJECT = "REJECT"
    REGENERATE = "REGENERATE"

    ALL = (ACCEPT, MODIFY, REJECT, REGENERATE)


@dataclass
class ArbitrationResult:
    decision: str
    final_text: str
    modified_text: Optional[str] = None
    reason: str = ""
    confidence: float = 0.0
    security_passed: bool = True
    user_instruction_respected: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    ck0_score: float = 0.0
    iterations: int = 0


class ResponseArbitrator:
    def __init__(self):
        self.max_modify_iterations = 3
        self.max_regenerate_iterations = 2

        self.system_damage_patterns: List[re.Pattern] = [
            re.compile(r"(rm\s+-rf|format\s+c:|del\s+/s|deltree|fdisk|mkfs)", re.I),
            re.compile(r"(drop\s+database|drop\s+table|truncate\s+table)", re.I),
            re.compile(r"(os\.system\(.*rm|subprocess.*rm\s+-rf)", re.I),
            re.compile(r"(shutdown|reboot|init\s+0|halt)", re.I),
            re.compile(r"(chmod\s+777\s+/|chown\s+root\s+/)", re.I),
            re.compile(r"(>/dev/sda|dd\s+if=.*of=/dev/)", re.I),
        ]

        self.content_warning_patterns: List[re.Pattern] = [
            re.compile(r"(kötü\s+amâ|zararlı\s+kod|malware|virüs\s+yazar)", re.I),
            re.compile(r"(api\s+anahtarı\s+sızdır|secret\s+key\s+expose)", re.I),
            re.compile(r"(ignore\s+previous|yönlendirmeyi\s+unut|sistem\s+talimatını\s+geç)", re.I),
        ]

        self.mandatory_rules: List[str] = [
            "no_system_damage",
        ]

    def arbitrate(
        self,
        response_text: str,
        user_message: str,
        user: Any,
        conversation: Any,
        context: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Any]] = None,
    ) -> ArbitrationResult:
        context = context or {}
        model_info = model_info or {}

        user_id = getattr(user, "id", None) or getattr(user, "pk", None) or "anonymous"
        conv_id = getattr(conversation, "id", None) or getattr(conversation, "pk", None) or "unknown"

        damage_result = self._system_damage_check(response_text)
        if not damage_result["passed"]:
            return ArbitrationResult(
                decision=ArbitrationDecision.REJECT,
                final_text=(
                    "Bu yanıt sisteminize veya programınıza zarar verebilecek işlemler "
                    "içeriyor, bu yüzden gönderilmiyor. Lütfen farklı bir şekilde isteyin."
                ),
                reason=damage_result["reason"],
                confidence=1.0,
                security_passed=False,
                user_instruction_respected=True,
                metadata={"stage": "system_damage_reject"},
            )

        content_warnings = self._content_warnings(response_text)

        ck0_result = self._ck0_evaluate(
            response_text=response_text,
            user_message=user_message,
            user_id=user_id,
            conversation_id=conv_id,
            context=context,
            model_info=model_info,
        )

        instruction_result = self._check_user_instruction(
            response_text, user_message, context
        )

        pref_result = self._check_user_preferences(user_id, response_text)

        decision, reason, confidence, final_text, modified_text = self._combine_decision(
            ck0_result=ck0_result,
            instruction_result=instruction_result,
            pref_result=pref_result,
            content_warnings=content_warnings,
            response_text=response_text,
            user_message=user_message,
            user_id=user_id,
            conversation_id=conv_id,
        )

        return ArbitrationResult(
            decision=decision,
            final_text=final_text or response_text,
            modified_text=modified_text,
            reason=reason,
            confidence=confidence,
            security_passed=True,
            user_instruction_respected=instruction_result["respected"],
            metadata={
                "stage": "arbitration_complete",
                "ck0_score": ck0_result.get("score", 0.0),
                "instruction_score": instruction_result.get("score", 0.0),
                "pref_score": pref_result.get("score", 0.0),
                "content_warnings": content_warnings,
                "user_id": user_id,
                "conversation_id": conv_id,
            },
            ck0_score=ck0_result.get("score", 0.0),
        )

    def _system_damage_check(self, response_text: str) -> Dict[str, Any]:
        low = (response_text or "").lower()

        for pattern in self.system_damage_patterns:
            if pattern.search(low):
                return {
                    "passed": False,
                    "reason": f"Sistem zararı riski: {pattern.pattern[:60]}",
                }

        return {"passed": True, "reason": ""}

    def _content_warnings(self, response_text: str) -> List[str]:
        low = (response_text or "").lower()
        warnings: List[str] = []
        for pattern in self.content_warning_patterns:
            if pattern.search(low):
                warnings.append(pattern.pattern[:50])
        return warnings

    def _ck0_evaluate(
        self,
        response_text: str,
        user_message: str,
        user_id: str,
        conversation_id: str,
        context: Dict[str, Any],
        model_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            user_prefs = self._get_user_prefs(user_id)

            ck0_result = ck0.decide(
                user_prefs=user_prefs,
                context={
                    "message": user_message,
                    "intent": context.get("intent", "chat"),
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "timestamp": time.time(),
                    **context,
                },
                experience={"conversation_id": conversation_id},
                model_response={
                    "text": response_text,
                    "model": model_info.get("model", "unknown"),
                    "provider": model_info.get("provider", "unknown"),
                },
                rules={
                    "max_response_length": 4000,
                    "respect_user_preferences": True,
                },
                options=[
                    {"id": "accept", "text": response_text, "features": {"score": 1.0}},
                    {"id": "modify", "text": response_text, "features": {"score": 0.6}},
                    {"id": "reject", "text": response_text, "features": {"score": 0.2}},
                    {"id": "regenerate", "text": response_text, "features": {"score": 0.4}},
                ],
            )

            selected = ck0_result.get("selected")
            scores = ck0_result.get("scores", [])

            return {
                "score": float(ck0_result.get("score", 0.0)),
                "selected_id": selected.get("id") if selected else None,
                "selected_text": selected.get("text") if selected else None,
                "all_scores": scores,
                "reason": ck0_result.get("reason", ""),
            }
        except Exception as exc:
            logger.warning("CK-0 arbitration error: %s", exc)
            return {"score": 0.5, "selected_id": "accept", "reason": f"CK-0 hata: {exc}"}

    def _check_user_instruction(
        self,
        response_text: str,
        user_message: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_msg_low = (user_message or "").lower()
        resp_low = (response_text or "").lower()

        score = 1.0
        respected = True
        issues: List[str] = []

        if any(k in user_msg_low for k in ("kod", "code", "fonksiyon", "script")):
            if not any(k in resp_low for k in ("def ", "function", "class ", "```", "kod", "script")):
                score -= 0.4
                issues.append("Kullanıcı kod istemiş ancak yanıtta kod yok")

        if any(k in user_msg_low for k in ("liste", "list", "tablo", "table")):
            if not any(k in resp_low for k in ("- ", "1.", "|", "liste", "tablo")):
                score -= 0.2
                issues.append("Kullanıcı liste/tablo istemiş ancak yanıtta yok")

        if any(k in user_msg_low for k in ("kısa", "short", "özet", "summary")):
            if len(response_text or "") > 500:
                score -= 0.3
                issues.append("Kullanıcı kısa yanıt istemiş ancak yanıt uzun")

        if any(k in user_msg_low for k in ("detaylı", "detailed", "ayrıntılı", "uzun")):
            if len(response_text or "") < 200:
                score -= 0.3
                issues.append("Kullanıcı detaylı yanıt istemiş ancak yanıt kısa")

        if issues:
            respected = False

        return {
            "respected": respected,
            "score": max(0.0, min(1.0, score)),
            "issues": issues,
        }

    def _get_user_prefs(self, user_id: str) -> Dict[str, Any]:
        try:
            prefs = getattr(ck0, "preferences", {}) or {}
            if isinstance(prefs, dict):
                return prefs
            return {}
        except Exception:
            return {}

    def _check_user_preferences(self, user_id: str, response_text: str) -> Dict[str, Any]:
        prefs = self._get_user_prefs(user_id)
        if not prefs:
            return {"score": 0.5, "applied": []}

        applied: List[str] = []
        score = 1.0
        resp_low = (response_text or "").lower()

        if prefs.get("preferred_language") == "turkish":
            tr_chars = set("çğıöşüÇĞİÖŞÜ")
            tr_count = sum(1 for c in resp_low if c in tr_chars)
            if tr_count == 0 and len(resp_low) > 50:
                score -= 0.2
                applied.append("dil_tercihi_turkce")

        if prefs.get("preferred_response_length") == "short":
            if len(response_text or "") > 800:
                score -= 0.2
                applied.append("kisa_yanit_tercihi")
        elif prefs.get("preferred_response_length") == "long":
            if len(response_text or "") < 150:
                score -= 0.2
                applied.append("uzun_yanit_tercihi")

        if prefs.get("preferred_style") == "formal":
            if any(k in resp_low for k in ("sa", "slm", "naber", "selam")):
                score -= 0.1
                applied.append("resmi_stil_tercihi")

        return {
            "score": max(0.0, min(1.0, score)),
            "applied": applied,
        }

    def _combine_decision(
        self,
        ck0_result: Dict[str, Any],
        instruction_result: Dict[str, Any],
        pref_result: Dict[str, Any],
        content_warnings: List[str],
        response_text: str,
        user_message: str,
        user_id: str,
        conversation_id: str,
    ) -> tuple:
        instruction_score = instruction_result["score"]
        pref_score = pref_result["score"]
        ck0_score = ck0_result.get("score", 0.5)
        ck0_selected = ck0_result.get("selected_id", "accept")

        warning_note = ""
        if content_warnings:
            warning_note = f" [İçerik notu: {', '.join(content_warnings[:2])}]"

        if instruction_score < 0.3:
            return (
                ArbitrationDecision.REGENERATE,
                f"Kullanıcının açık talimatına uygun yanıt üretilemedi.{warning_note}",
                0.8,
                "Üzgünüm, isteğinizi doğru anlayamadım. Lütfen daha ayrıntı verir misiniz?",
                None,
            )

        if pref_score < 0.5 and instruction_score >= 0.5:
            modified = self._apply_preference_modifications(
                response_text, pref_result
            )
            return (
                ArbitrationDecision.MODIFY,
                f"Kullanıcı tercihlerine göre yanıt geliştirildi.{warning_note}",
                0.7,
                modified or response_text,
                modified,
            )

        if ck0_selected == ArbitrationDecision.REJECT or ck0_score < 0.25:
            return (
                ArbitrationDecision.REGENERATE,
                f"Neural Core yeniden üretim önerdi (düşük skor: {ck0_score:.2f}).{warning_note}",
                0.6,
                "",
                None,
            )

        if ck0_selected == ArbitrationDecision.REGENERATE or ck0_score < 0.45:
            return (
                ArbitrationDecision.REGENERATE,
                f"Neural Core yeniden üretim önerdi.{warning_note}",
                0.6,
                "",
                None,
            )

        if ck0_selected == ArbitrationDecision.MODIFY or ck0_score < 0.65:
            modified = self._apply_ck0_modifications(response_text, ck0_result)
            return (
                ArbitrationDecision.MODIFY,
                f"Neural Core yanıtı geliştirme önerdi.{warning_note}",
                0.65,
                modified or response_text,
                modified,
            )

        return (
            ArbitrationDecision.ACCEPT,
            f"Neural Core yanıtı onayladı.{warning_note}",
            ck0_score,
            response_text,
            None,
        )

    def _apply_preference_modifications(
        self, response_text: str, pref_result: Dict[str, Any]
    ) -> Optional[str]:
        applied = pref_result.get("applied", [])
        text = response_text or ""

        if "dil_tercihi_turkce" in applied:
            text = (
                "[Dil tercihi uyarı: Yanıt Türkçe olmalı.] " + text
            ).strip()

        if "kisa_yanit_tercihi" in applied and len(text) > 800:
            text = text[:800] + "..."

        if "uzun_yanit_tercihi" in applied and len(text) < 150:
            text = text + "\n\n(Bu yanıt daha ayrıntılı genişletilebilir.)"

        return text if text != response_text else None

    def _apply_ck0_modifications(
        self, response_text: str, ck0_result: Dict[str, Any]
    ) -> Optional[str]:
        reason = ck0_result.get("reason", "")
        text = response_text or ""

        if "kısa" in reason.lower() or "short" in reason.lower():
            if len(text) > 600:
                text = text[:600] + "..."
        if "detay" in reason.lower() or "detail" in reason.lower():
            if len(text) < 300:
                text = text + "\n\nDaha fazla detay eklenebilir."

        return text if text != response_text else None

    def needs_regenerate(self, result: ArbitrationResult) -> bool:
        return result.decision == ArbitrationDecision.REGENERATE

    def needs_modify(self, result: ArbitrationResult) -> bool:
        return result.decision == ArbitrationDecision.MODIFY

    def should_send_to_user(self, result: ArbitrationResult) -> bool:
        return result.decision in (ArbitrationDecision.ACCEPT, ArbitrationDecision.MODIFY)


# Global instance
response_arbitrator = ResponseArbitrator()
