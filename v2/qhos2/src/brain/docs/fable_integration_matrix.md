# Fable integration matrix

Fable is a source of operating principles. It is not the Brain.
Prompts are not transplanted. Sources are pinned. Auto-sync is forbidden.

Concepts: **119**. Analysis: 2026-09-02.

| ID | Concept | Source | Class | Brain | Implementation | Tests | Status |
|---|---|---|---|---|---|---|---|
| FAB-001 | Adaptive reasoning effort | MULTIPLE | ADAPT | DiagnosticBudget (flat) | brain/fable/effort.py | test_fable_effort_routing | IMPLEMENTED |
| FAB-002 | Effort budget mapping | MULTIPLE | ADAPT | ReasoningBudget | brain/fable/effort.py | test_fable_effort_budgets | IMPLEMENTED |
| FAB-003 | Do not overthink easy incidents | CODE_FABLE5 | ADAPT | none | brain/fable/effort.py | test_fable_effort_overkill | IMPLEMENTED |
| FAB-004 | Persistent memory filesystem | MULTIPLE | ADAPT | BrainStore JSONL + KnowledgeGraph | brain/fable/memory_adapt.py | test_fable_memory_relevance | IMPLEMENTED |
| FAB-005 | Typed memory kinds | MULTIPLE | ADAPT | MemoryKind (WORKING/EPISODIC/…) | brain/fable/memory_adapt.py | test_fable_memory_types | IMPLEMENTED |
| FAB-006 | Memory index vs body | CODE_FABLE5 | ADAPT | memory.tail() | brain/fable/memory_adapt.py | test_fable_memory_index | IMPLEMENTED |
| FAB-007 | Memory cross-links | CODE_FABLE5 | ADAPT | KnowledgeGraph.relate | brain/fable/memory_adapt.py | test_fable_memory_links | IMPLEMENTED |
| FAB-008 | Memory suggests, never proves | MULTIPLE | ADOPT | MemoryHint.memory_suggests_not_proves | brain/fable/memory_adapt.py | test_fable_memory_not_truth | IMPLEMENTED |
| FAB-009 | Freshness check for named artifacts | CODE_FABLE5 | ADAPT | MemoryHint.must_revalidate | brain/fable/memory_adapt.py | test_fable_memory_revalidation | IMPLEMENTED |
| FAB-010 | Memory correction and invalidation | MULTIPLE | ADAPT | append-only JSONL | brain/fable/memory_adapt.py | test_fable_memory_invalidation | IMPLEMENTED |
| FAB-011 | Memory deduplication | CODE_FABLE5 | ADAPT | none | brain/fable/memory_adapt.py | test_fable_memory_dedup | IMPLEMENTED |
| FAB-012 | Memory provenance | FABLE5 | ADAPT | AuditEvent.actor | brain/fable/provenance.py | test_fable_source_trust | IMPLEMENTED |
| FAB-013 | Reference pointers | CODE_FABLE5 | ADAPT | none | brain/fable/memory_adapt.py | test_fable_reference_pointer | IMPLEMENTED |
| FAB-014 | Do not memorize the repository | CODE_FABLE5 | ADAPT | MAX_BODY / WORKING dump reject | brain/fable/memory_adapt.py | test_fable_scratch_not_memory | IMPLEMENTED |
| FAB-015 | Scratchpad isolation | CODE_FABLE5 | ADAPT | eval_paths .qhos-brain | brain/fable/scratch.py | test_fable_scratch_isolation | IMPLEMENTED |
| FAB-016 | Scratchpad is not memory | CODE_FABLE5 | ADOPT | none | brain/fable/scratch.py | test_fable_scratch_not_memory | IMPLEMENTED |
| FAB-017 | Context compaction / continuation | MULTIPLE | ADAPT | ContextBudget | brain/fable/context_adapt.py | test_fable_context_compaction | IMPLEMENTED |
| FAB-018 | Do not re-derive established facts | CODE_FABLE5 | ADAPT | none | brain/fable/context_adapt.py | test_fable_do_not_rederive | IMPLEMENTED |
| FAB-019 | Investigation snapshot | MULTIPLE | ADAPT | BrainResult.replay_inputs | brain/fable/snapshot.py | test_fable_snapshot_schema | IMPLEMENTED |
| FAB-020 | Long-run continuity | CODE_FABLE5 | ADAPT | eval incremental checkpoint | brain/fable/resume.py | test_fable_resume_checkpoint | IMPLEMENTED |
| FAB-021 | Never auto-resume write actions | MULTIPLE | ADOPT | SANDBOX_ENABLED=false / write deny | brain/fable/resume.py | test_fable_no_write_resume | IMPLEMENTED |
| FAB-022 | Evidence before state change | CODE_FABLE5 | ADOPT | control_side.admit | brain/fable/completion.py | test_fable_evidence_before_state_change | IMPLEMENTED |
| FAB-023 | Claim-evidence binding | CODE_FABLE5_1 | ADAPT | unsupported_claim / hallucination ledger | brain/fable/completion.py | test_fable_claim_evidence_binding | IMPLEMENTED |
| FAB-024 | Failure-first reporting | CODE_FABLE5_1 | ADAPT | DecisionTrace | brain/fable/completion.py | test_fable_failure_first | IMPLEMENTED |
| FAB-025 | Completion honesty states | CODE_FABLE5_1 | ADAPT | TaskStatus / SandboxStatus | brain/fable/completion.py | test_fable_completion_honesty | IMPLEMENTED |
| FAB-026 | Specialized tools over generic shell | CODE_FABLE5 | ADAPT | authorize_verification / authorize_command | brain/fable/tools.py | test_fable_tool_discipline | IMPLEMENTED |
| FAB-027 | Parallel independent investigation | CODE_FABLE5 | ADAPT | none | brain/fable/parallel.py | test_fable_parallel_safe | IMPLEMENTED |
| FAB-028 | Deterministic parallelism states | CODE_FABLE5 | ADAPT | none | brain/fable/parallel.py | test_fable_parallel_dependency | IMPLEMENTED |
| FAB-029 | Fresh-context verifier | CODE_FABLE5 | ADAPT | gateway.evaluate_evidence | brain/fable/verifier.py | test_fable_fresh_verifier | IMPLEMENTED |
| FAB-030 | Task contracts | CODE_FABLE5 | ADAPT | TaskKind / TaskStatus | brain/fable/goal.py | test_fable_task_contract | IMPLEMENTED |
| FAB-031 | Goal boundary | CODE_FABLE5 | ADAPT | GoalOrigin | brain/fable/goal.py | test_fable_scope_boundary | IMPLEMENTED |
| FAB-032 | Continue what can continue | CODE_FABLE5 | ADAPT | TaskStatus.BLOCKED / READY | brain/fable/goal.py | test_fable_partial_blockage | IMPLEMENTED |
| FAB-033 | Agent delegation at runtime | CODE_FABLE5 | ADAPT | BrainOrchestrator / route_agent_explained | brain/fable/goal.py | test_fable_no_autonomous_delegation | DEFERRED |
| FAB-034 | MODEL is non-authoritative evidence | MULTIPLE | ADOPT | reasoner deterministic_evidence_wins | brain/reasoner.py | test_fable_source_trust | IMPLEMENTED |
| FAB-035 | Strategy-level performance ledger | MULTIPLE | ADAPT | ProviderLedger | brain/fable/ledger.py | test_fable_strategy_ledger | IMPLEMENTED |
| FAB-036 | No autonomous strategy routing | MULTIPLE | ADOPT | brain_cognitive_mode=deterministic_only | brain/strategies/__init__.py | test_fable_not_default | IMPLEMENTED |
| FAB-037 | Structured output, no free CoT storage | MULTIPLE | ADOPT | DecisionTrace / validate_reasoner_output | brain/fable/result.py | test_fable_schema | IMPLEMENTED |
| FAB-038 | Watcher observation-only lifecycle | CODE_FABLE5 | ADAPT | core.events EventBus | brain/fable/watch.py | test_fable_watcher_lifecycle | IMPLEMENTED |
| FAB-039 | Prefer push/events over aggressive polling | CODE_FABLE5 | ADAPT | event_bus in-process | brain/fable/watch.py | test_fable_event_driven | IMPLEMENTED |
| FAB-040 | Watcher failure cannot kill Trader | CODE_FABLE5 | ADOPT | runtime swallow / isolation | brain/fable/watch.py | test_fable_watcher_isolation | IMPLEMENTED |
| FAB-041 | Pin sources; never auto-sync prompts | MULTIPLE | ADOPT | none | brain/fable/source.py | test_fable_source_inventory | IMPLEMENTED |
| FAB-042 | Source revision diff process | MULTIPLE | ADAPT | none | brain/fable/source.py | test_fable_source_diff | IMPLEMENTED |
| FAB-043 | Untrusted comments are data | MULTIPLE | ALREADY_EXISTS | INJECTION_MARKERS / untrusted_claim_ignored | brain/reasoner.py | test_brain_cognitive / families prompt_injection | ALREADY_EXISTS |
| FAB-044 | Deceptive success detection | CODE_FABLE5_1 | ADAPT | forbid_tamper / patch_is_invalid_fix | brain/fable/verifier.py | test_fable_deceptive_success | IMPLEMENTED |
| FAB-045 | Recency is not causality | CODE_FABLE5 | ADAPT | diagnose correlate_diff | brain/fable/strategy.py | test_fable_multi_commit | IMPLEMENTED |
| FAB-046 | DiagnosticBudget | MULTIPLE | ALREADY_EXISTS | DiagnosticBudget | brain/models.py | tests/test_brain.py | ALREADY_EXISTS |
| FAB-047 | ProviderLedger | MULTIPLE | ALREADY_EXISTS | ProviderLedger | brain/ledger.py | tests/test_brain_real_shadow.py | ALREADY_EXISTS |
| FAB-048 | Knowledge graph | MULTIPLE | ALREADY_EXISTS | KnowledgeGraph | brain/memory.py | tests/test_brain.py | ALREADY_EXISTS |
| FAB-049 | Ground-truth isolation | MULTIPLE | ALREADY_EXISTS | strip_ground_truth / LEAK_KEYS | brain/diagnose.py | tests/test_brain_real_value.py | ALREADY_EXISTS |
| FAB-050 | Safety hard gate | MULTIPLE | ALREADY_EXISTS | evaluate violations | brain/evaluate.py | tests/test_brain_proving.py | ALREADY_EXISTS |
| FAB-051 | Level 2 + shadow | MULTIPLE | ALREADY_EXISTS | AutonomyPolicy / SANDBOX_ENABLED | brain/sandbox.py | test_fable_level3_disabled | ALREADY_EXISTS |
| FAB-052 | Verification authorize/execute | MULTIPLE | ALREADY_EXISTS | authorize_verification | brain/verification.py | tests/test_brain_cognitive.py | ALREADY_EXISTS |
| FAB-053 | DecisionTrace | MULTIPLE | ALREADY_EXISTS | DecisionTrace | brain/models.py | tests/test_brain.py | ALREADY_EXISTS |
| FAB-054 | Incremental eval checkpoint | MULTIPLE | ALREADY_EXISTS | write_eval_checkpoint | brain/eval_cli.py | test_eval_cli_checkpoints_each_row | ALREADY_EXISTS |
| FAB-055 | Real network timeout | MULTIPLE | ALREADY_EXISTS | anthropic_client(timeout_s=) | brain/providers.py | test_anthropic_timeout_reaches_sdk | ALREADY_EXISTS |
| FAB-056 | Provider client reuse and close | MULTIPLE | ALREADY_EXISTS | CognitiveGateway.close | brain/eval_cli.py | test_eval_cli_checkpoints_each_row_and_reuses_gateway | ALREADY_EXISTS |
| FAB-057 | Secret redaction | MULTIPLE | ALREADY_EXISTS | brain/redact.py | brain/redact.py | tests/test_brain_real_shadow.py | ALREADY_EXISTS |
| FAB-058 | CognitiveGateway | MULTIPLE | ALREADY_EXISTS | CognitiveGateway | brain/gateway.py | tests/test_brain_cognitive.py | ALREADY_EXISTS |
| FAB-059 | Investigate vs change split | CODE_FABLE5 | ALREADY_EXISTS | admit() / SANDBOX_ENABLED | brain/control_side.py | tests/test_brain_sandbox.py | ALREADY_EXISTS |
| FAB-060 | Confirm before irreversible actions | CODE_FABLE5 | ALREADY_EXISTS | SENSITIVE_ACTIONS | brain/policies.py | tests/test_brain_sandbox.py | ALREADY_EXISTS |
| FAB-061 | Claude product / Mythos marketing | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-062 | Consumer tone and list formatting | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-063 | Consumer wellbeing / child-safety prompt | FABLE5 | REJECT_PRODUCT_SPECIFIC | brain/redact.py (own policy) | — | test_fable_matrix_completeness | REJECTED |
| FAB-064 | Political evenhandedness | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-065 | Artifacts / visualizer / inline visuals | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-066 | MCP/plugin/skill marketplace suggestions | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-067 | Computer-use / Chrome automation | MULTIPLE | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-068 | Gmail connector suite | MULTIPLE | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-069 | Google Calendar connector suite | MULTIPLE | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-070 | Google Drive connector suite | MULTIPLE | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-071 | Consumer display widgets | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-072 | End conversation tool | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-073 | Voice notes | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-074 | Past chats / conversation search | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-075 | Claude Code session UX | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-076 | Cron / ScheduleWakeup product | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-077 | DesignSync | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-078 | Claude Code worktree product | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | brain/sandbox.py (own worktree) | — | test_fable_matrix_completeness | REJECTED |
| FAB-079 | Artifact publish / CDN allowlist | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-080 | Fast mode / model ID advertising | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | core.config brain_llm_model | — | test_fable_matrix_completeness | REJECTED |
| FAB-081 | Writing-for-the-user style (Fable 5.1) | CODE_FABLE5_1 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-082 | Knowledge cutoff text | MULTIPLE | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-083 | Anthropic classifier reminders | FABLE5 | REJECT_PRODUCT_SPECIFIC | INJECTION_MARKERS | — | test_fable_matrix_completeness | REJECTED |
| FAB-084 | Consumer legal/financial disclaimer | FABLE5 | REJECT_PRODUCT_SPECIFIC | governance/control.py | — | test_fable_matrix_completeness | REJECTED |
| FAB-085 | Claude.ai persistent artifact storage API | FABLE5 | REJECT_PRODUCT_SPECIFIC | brain/fable/scratch.py | — | test_fable_matrix_completeness | REJECTED |
| FAB-086 | Chrome GIF/tab product rules | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-087 | AskUserQuestion product | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-088 | SendFeedback / SendMessage / PushNotification | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-089 | Workflow authoring skill | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | BrainOrchestrator | — | test_fable_matrix_completeness | REJECTED |
| FAB-090 | Recommend Claude apps | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-091 | Sports / weather / places search | FABLE5 | REJECT_IRRELEVANT | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-092 | Claude.ai network/filesystem configuration | FABLE5 | REJECT_IRRELEVANT | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-093 | Artifact package management | FABLE5 | REJECT_IRRELEVANT | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-094 | Consumer image-search policy | FABLE5 | REJECT_IRRELEVANT | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-095 | Game-state JSON context snippets | FABLE5 | REJECT_IRRELEVANT | InvestigationSnapshot | — | test_fable_matrix_completeness | REJECTED |
| FAB-096 | Autonomous proceed because user is not watching | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | Level 2 / shadow | — | test_fable_level3_disabled | REJECTED |
| FAB-097 | Background memory pass without review | FABLE5 | REJECT_UNSAFE_FOR_TRADER | BrainStore.remember (explicit) | — | test_fable_scratch_not_memory | REJECTED |
| FAB-098 | Mythos / dual-use unrestricted mode | MULTIPLE | REJECT_UNSAFE_FOR_TRADER | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-099 | Publish / send to external services | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | redact / CLASS_DENIED | — | tests/test_brain_real_shadow.py | REJECTED |
| FAB-100 | User reaffirmation as authorization | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | governance/control.py | — | test_fable_level3_disabled | REJECTED |
| FAB-101 | Generic bash as primary tool | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | authorize_command | brain/fable/tools.py | test_fable_tool_discipline | REJECTED |
| FAB-102 | Unrestricted computer-use | MULTIPLE | REJECT_UNSAFE_FOR_TRADER | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-103 | Auto-install plugins/connectors | FABLE5 | REJECT_UNSAFE_FOR_TRADER | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-104 | Auto-resume interrupted writes | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | brain/fable/resume.py | brain/fable/resume.py | test_fable_no_write_resume | REJECTED |
| FAB-105 | Treat recalled memory as user instructions | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | MemoryHint | — | test_fable_memory_not_truth | REJECTED |
| FAB-106 | RemoteTrigger | CODE_FABLE5 | REJECT_UNSAFE_FOR_TRADER | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-107 | Full vector/semantic memory index | FABLE5 | ADAPT | none | brain/fable/memory_adapt.py | test_fable_memory_relevance | DEFERRED |
| FAB-108 | Live production watcher process | CODE_FABLE5 | ADAPT | runtime EventBus subscribe | brain/fable/watch.py | test_fable_watcher_lifecycle | DEFERRED |
| FAB-109 | Multi-agent runtime execution | CODE_FABLE5 | ADAPT | FAB-033 | brain/fable/goal.py | test_fable_no_autonomous_delegation | DEFERRED |
| FAB-110 | Reply-after-tool-calls stands alone | FABLE5_1 | ADAPT | DecisionTrace | brain/fable/completion.py | test_fable_failure_first | IMPLEMENTED |
| FAB-111 | Malware/exploit refusal block | FABLE5 | REJECT_PRODUCT_SPECIFIC | authorize_command | — | test_fable_matrix_completeness | REJECTED |
| FAB-112 | Thumbs-down / mistake UX | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-113 | ToolSearch | CODE_FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-114 | NotebookEdit | CODE_FABLE5 | REJECT_IRRELEVANT | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-115 | ReportFindings structured report | CODE_FABLE5 | ADAPT | FableReasoningResult | brain/fable/result.py | test_fable_schema | IMPLEMENTED |
| FAB-116 | Git push/force confirmations | CODE_FABLE5 | ALREADY_EXISTS | UNSAFE_ACTIONS / SENSITIVE_ACTIONS | brain/diagnose.py | tests/test_brain_sandbox.py | ALREADY_EXISTS |
| FAB-117 | Preferences / user profile injection | FABLE5 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-118 | list_mcp_resources / read_resource_link (5.1) | FABLE5_1 | REJECT_PRODUCT_SPECIFIC | none | — | test_fable_matrix_completeness | REJECTED |
| FAB-119 | Do not stop because the session is long | CODE_FABLE5 | ADAPT | InvestigationSnapshot | brain/fable/resume.py | test_fable_context_continuation | IMPLEMENTED |

## Deferred

- **FAB-033 Agent delegation at runtime**: Schema only. Runtime delegation remains off. Level 3 stays disabled.
- **FAB-107 Full vector/semantic memory index**: Lexical relevance is implemented. Embeddings deferred.
- **FAB-108 Live production watcher process**: Observation stub only. Runtime already subscribes to EventBus.
- **FAB-109 Multi-agent runtime execution**: deferred

## Completeness

See `completeness_report()` and `tests/test_brain_fable.py`.
