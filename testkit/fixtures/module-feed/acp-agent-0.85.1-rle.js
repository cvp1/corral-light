// Excerpt of @agentclientprotocol/claude-agent-acp 0.85.1 dist/acp-agent.js
// (Apache-2.0): the rate_limit_event case, unpatched, for adapter_patches tests.
switch (message.type) {
                    case "rate_limit_event": {
                        if (lastAssistantTotalUsage !== null) {
                            await sendUpdate({
                                sessionId: params.sessionId,
                                update: attachUsageModel({
                                    sessionUpdate: "usage_update",
                                    used: lastAssistantTotalUsage,
                                    size: session.contextWindowSize,
                                    _meta: { "_claude/rateLimit": message.rate_limit_info },
                                }),
                            });
                        }
                        break;
                    }
}
