import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class ThreatType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    PROMPT_LEAKAGE = "prompt_leakage"
    TOOL_ABUSE = "tool_abuse"
    SENSITIVE_INFO_REQUEST = "sensitive_info_request"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class GuardrailResult:
    safe: bool
    threat_type: Optional[ThreatType] = None
    risk_level: RiskLevel = RiskLevel.LOW
    reason: str = ""
    matched_patterns: List[str] = None


class GuardrailEngine:
    """Input guardrail to detect and prevent malicious inputs."""
    
    # Prompt injection patterns
    INJECTION_PATTERNS = [
        r"ignore\s+previous\s+instructions",
        r"disregard\s+previous\s+instructions",
        r"forget\s+previous\s+instructions",
        r"ignore\s+all\s+previous",
        r"disregard\s+all\s+previous",
        r"new\s+instructions",
        r"override\s+instructions",
        r"system\s+prompt",
        r"reveal\s+your\s+prompt",
        r"show\s+me\s+your\s+prompt",
        r"what\s+is\s+your\s+system\s+prompt",
        r"print\s+your\s+prompt",
        r"display\s+your\s+instructions",
        r"give\s+me\s+your\s+api\s+key",
        r"show\s+me\s+your\s+api\s+key",
        r"what\s+is\s+your\s+api\s+key",
        r"reveal\s+your\s+api\s+key",
        r"disable\s+security",
        r"turn\s+off\s+security",
        r"bypass\s+security",
        r"act\s+as\s+administrator",
        r"act\s+as\s+admin",
        r"you\s+are\s+now\s+an\s+administrator",
        r"pretend\s+to\s+be\s+admin",
        r"sudo\s+mode",
        r"developer\s+mode",
        r"debug\s+mode",
        r"unrestricted\s+mode",
        r"no\s+restrictions",
        r"ignore\s+safety",
        r"disable\s+safety",
        r"bypass\s+safety",
        r"do\s+not\s+follow\s+rules",
        r"break\s+character",
        r"step\s+out\s+of\s+character",
        r"roleplay\s+as",
        r"simulate\s+being",
    ]
    
    # Jailbreak patterns
    JAILBREAK_PATTERNS = [
        # "DAN" (Do Anything Now) only constitutes an attack when paired with an
    # activation phrase. A bare r"DAN" matched any message containing the
    # substring - "abundant", "vendor", "candidate" - and refused legitimate
    # customer requests, and even \bDAN\b alone blocks a customer named Dan.
    # The persona requires an instruction to do anything, so anchor on one.
    r"(?:\byou\s+are\b|\bact(?:ing)?\s+as\b|\bbecome\b|\bpretend\b|\bimagine\b"
    r"|\benter\b|\bmode\b|\bprompt\b|\bjailbreak\b|\boverride\b)"
    r"[^\n]{0,32}\bDAN\b"
    r"|\bDAN\b[^\n]{0,32}\b(?:mode|prompt|jailbreak|ignore|instructions?|"
    r"restrictions?|guidelines?)\b",
        r"do\s+anything\s+now",
        r"unrestricted\s+AI",
        r"no\s+ethical\s+guidelines",
        r"ignore\s+ethics",
        r"no\s+morals",
        r"evil\s+AI",
        r"harmful\s+content",
        r"generate\s+harmful",
        r"illegal\s+activity",
        r"help\s+me\s+hack",
        r"how\s+to\s+hack",
        r"create\s+virus",
        r"make\s+bomb",
        r"steal\s+data",
    ]
    
    # Prompt leakage patterns
    LEAKAGE_PATTERNS = [
        r"what\s+is\s+your\s+system\s+prompt",
        r"show\s+me\s+your\s+instructions",
        r"reveal\s+your\s+instructions",
        r"print\s+your\s+instructions",
        r"display\s+your\s+instructions",
        r"tell\s+me\s+your\s+instructions",
        r"what\s+are\s+your\s+instructions",
        r"what\s+were\s+you\s+told",
        r"what\s+was\s+your\s+prompt",
        r"repeat\s+your\s+prompt",
        r"output\s+your\s+prompt",
        r"show\s+your\s+prompt",
        r"internal\s+instructions",
        r"hidden\s+instructions",
        r"secret\s+instructions",
        r"configuration\s+prompt",
        r"initial\s+prompt",
        r"base\s+prompt",
        r"foundational\s+prompt",
    ]
    
    # Sensitive info request patterns
    SENSITIVE_PATTERNS = [
        r"api\s+key",
        r"secret\s+key",
        r"password",
        r"database\s+credential",
        r"db\s+password",
        r"connection\s+string",
        r"private\s+key",
        r"access\s+token",
        r"auth\s+token",
        r"jwt\s+secret",
        r"encryption\s+key",
        r"ssh\s+key",
        r"aws\s+key",
        r"gcp\s+key",
        r"azure\s+key",
    ]
    
    def __init__(self):
        self.compiled_injection = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]
        self.compiled_jailbreak = [re.compile(p, re.IGNORECASE) for p in self.JAILBREAK_PATTERNS]
        self.compiled_leakage = [re.compile(p, re.IGNORECASE) for p in self.LEAKAGE_PATTERNS]
        self.compiled_sensitive = [re.compile(p, re.IGNORECASE) for p in self.SENSITIVE_PATTERNS]
    
    def check_input(self, user_input: str) -> GuardrailResult:
        """Check user input for threats."""
        matched = []
        
        # Check prompt injection
        for pattern in self.compiled_injection:
            if pattern.search(user_input):
                matched.append(pattern.pattern)
                return GuardrailResult(
                    safe=False,
                    threat_type=ThreatType.PROMPT_INJECTION,
                    risk_level=RiskLevel.HIGH,
                    reason="Prompt injection attempt detected",
                    matched_patterns=matched
                )
        
        # Check jailbreak
        for pattern in self.compiled_jailbreak:
            if pattern.search(user_input):
                matched.append(pattern.pattern)
                return GuardrailResult(
                    safe=False,
                    threat_type=ThreatType.JAILBREAK,
                    risk_level=RiskLevel.CRITICAL,
                    reason="Jailbreak attempt detected",
                    matched_patterns=matched
                )
        
        # Check prompt leakage
        for pattern in self.compiled_leakage:
            if pattern.search(user_input):
                matched.append(pattern.pattern)
                return GuardrailResult(
                    safe=False,
                    threat_type=ThreatType.PROMPT_LEAKAGE,
                    risk_level=RiskLevel.HIGH,
                    reason="Prompt leakage attempt detected",
                    matched_patterns=matched
                )
        
        # Check sensitive info request
        for pattern in self.compiled_sensitive:
            if pattern.search(user_input):
                matched.append(pattern.pattern)
                return GuardrailResult(
                    safe=False,
                    threat_type=ThreatType.SENSITIVE_INFO_REQUEST,
                    risk_level=RiskLevel.HIGH,
                    reason="Sensitive information request detected",
                    matched_patterns=matched
                )
        
        return GuardrailResult(safe=True, risk_level=RiskLevel.LOW, reason="Input passed guardrail checks")
    
    def get_safe_response(self, threat_type: ThreatType) -> str:
        """Get a safe response for blocked requests."""
        responses = {
            ThreatType.PROMPT_INJECTION: "I can't process that request. Please ask me something related to customer support.",
            ThreatType.JAILBREAK: "I'm unable to comply with that request. I'm here to help with customer support inquiries.",
            ThreatType.PROMPT_LEAKAGE: "I can't provide internal system instructions, but I can explain what I can help you with.",
            ThreatType.SENSITIVE_INFO_REQUEST: "I cannot share sensitive information. Please contact support for account-specific questions.",
            ThreatType.TOOL_ABUSE: "That action is not permitted. Please try a different request.",
        }
        return responses.get(threat_type, "I'm unable to process that request.")


# Tool permission definitions
class ToolPermission:
    """Defines permissions and risk levels for each tool."""
    
    TOOL_PERMISSIONS = {
        "get_order": {
            "risk_level": RiskLevel.LOW,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["order_id"],
            "description": "Retrieve order information"
        },
        "get_customer": {
            "risk_level": RiskLevel.LOW,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["customer_id"],
            "description": "Retrieve customer information"
        },
        "get_delivery_status": {
            "risk_level": RiskLevel.LOW,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["order_id"],
            "description": "Check delivery status"
        },
        "get_payment_status": {
            "risk_level": RiskLevel.LOW,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["order_id"],
            "description": "Check payment status"
        },
        "check_refund_status": {
            "risk_level": RiskLevel.MEDIUM,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["order_id"],
            "description": "Check refund status"
        },
        "check_return_eligibility": {
            "risk_level": RiskLevel.MEDIUM,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["order_id"],
            "description": "Check if order is eligible for return"
        },
        "create_support_ticket": {
            "risk_level": RiskLevel.MEDIUM,
            "allowed_roles": ["customer", "agent", "admin"],
            "required_params": ["customer_id", "issue", "priority"],
            "description": "Create a support ticket for escalation"
        },
        "process_refund": {
            "risk_level": RiskLevel.HIGH,
            "allowed_roles": ["agent", "admin"],
            "required_params": ["order_id", "amount", "reason"],
            "description": "Process a refund (requires approval)"
        },
        "cancel_order": {
            "risk_level": RiskLevel.HIGH,
            "allowed_roles": ["agent", "admin"],
            "required_params": ["order_id", "reason"],
            "description": "Cancel an order (requires approval)"
        },
    }
    
    @classmethod
    def check_permission(cls, tool_name: str, role: str = "customer") -> Dict[str, Any]:
        """Check if a role has permission to use a tool."""
        perm = cls.TOOL_PERMISSIONS.get(tool_name)
        if not perm:
            return {"allowed": False, "reason": f"Unknown tool: {tool_name}"}
        
        if role not in perm["allowed_roles"]:
            return {
                "allowed": False, 
                "reason": f"Role '{role}' not authorized for tool '{tool_name}'",
                "risk_level": perm["risk_level"]
            }
        
        return {
            "allowed": True,
            "risk_level": perm["risk_level"],
            "required_params": perm["required_params"]
        }


# Global guardrail instance
guardrail_engine = GuardrailEngine()