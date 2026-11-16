"""Expose the OCHAsk Pydantic AI agent through Pydantic's built-in web chat UI."""

from .chat import SearchContext, agent

# The native UI passes this dependency to each local chat request so the search
# tool can return its normal source IDs and evidence records.
app = agent.to_web(deps=SearchContext())
