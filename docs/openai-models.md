# OpenAI models

Postdesk can run its existing partner, maker, editor and picker agents using
OpenAI's Responses API. This is an API integration; it does not connect a
ChatGPT subscription or run the Codex harness. The existing human approval
step is unchanged.

1. Configure `OPENAI_API_KEY` in the server environment. The key is never
   returned by the settings API. API usage is billed separately by OpenAI.
2. Restart the service, then open Settings → Models. Select an OpenAI model
   for each agent you want to move and save. Claude models remain available
   when `ANTHROPIC_API_KEY` is configured.
3. Send a test idea to the paired Telegram bot and review the resulting
   brief and draft. A working renderer, storage, and publisher configuration
   are required for the full publishing workflow.

The dropdown lists OpenAI models with known standard token prices in
`postdesk.llm.OPENAI_PRICES`. Unknown OpenAI models are refused rather than
silently bypassing spend tracking. Updating the list requires a code update;
model availability depends on the API account. Prices and tool availability
can change, so check them against OpenAI before changing the list. The
monthly ceiling uses estimated recorded costs and stops later jobs after it
is reached; it cannot guarantee a strict cap during an in-flight request.
Partner web search is limited to the configured domains and the number of
tool calls. OpenAI and Anthropic may not generate identical content or
interpret the same effort level identically.
