"""
production/agent/customer_success_agent.py
Agent definition for the Customer Success FTE.

Assembles the production agent from its components:
  - System prompt  : production/agent/prompts.py
  - 5 tools        : production/agent/tools.py
  - Formatter      : production/agent/formatters.py  (called inside send_response)

Usage:
    from production.agent import customer_success_agent, db_context, openai_context

    # Inject context before running:
    db_context.set(db_pool)
    openai_context.set(openai_client)

    result = await Runner.run(
        customer_success_agent,
        input=agent_context_block,   # built by message_processor
    )
"""

from agents import Agent

from production.agent.prompts import CUSTOMER_SUCCESS_SYSTEM_PROMPT
from production.agent.tools import (
    create_ticket,
    escalate_to_human,
    get_customer_history,
    search_knowledge_base,
    send_response,
)

customer_success_agent = Agent(
    name="Customer Success FTE",
    model="gpt-4o-mini",
    instructions=CUSTOMER_SUCCESS_SYSTEM_PROMPT,
    tools=[
        search_knowledge_base,
        create_ticket,
        get_customer_history,
        escalate_to_human,
        send_response,
    ],
)
