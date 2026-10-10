"""Agentic AI layer for the customer support platform.

Exposes the agent entry points used by the API layer:

    from app.agents import run_support_agent, SupportAgent

The agent core is intentionally independent of FastAPI and of any single
LLM provider, so it can be unit tested or driven from a script.
"""

from app.agents.support_agent import (
    SupportAgent,
    AgentResult,
    AgentOutcome,
    AgentMemory,
    IntentClassifier,
    EntityExtractor,
    LLMClient,
    ResponseBuilder,
    run_support_agent,
)

__all__ = [
    "SupportAgent",
    "AgentResult",
    "AgentOutcome",
    "AgentMemory",
    "IntentClassifier",
    "EntityExtractor",
    "LLMClient",
    "ResponseBuilder",
    "run_support_agent",
]