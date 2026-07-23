# Discovery: integrating Apple Foundation Models into OpenWorker

**Status:** research increment; no production integration is implemented by this document  
**Decision date:** 2026-07-23  
**Decision owner:** OpenWorker maintainers  
**Recommended next step:** run the two time-boxed feasibility spikes in [Implementation plans](#implementation-plans-for-the-leading-two-options) before committing to a release architecture

## Executive summary

Apple Foundation Models is a strong fit for an optional, on-device OpenWorker model, but the
proposal in the discovery brief is more certain than the available evidence warrants. The
important conclusions are:

1. **The best product architecture, if Apple's Python package passes a packaged-app spike, is a
   first-class Python provider that makes structured, single-tool proposals.** This keeps
   authorization and execution in `TurnEngine`, has the smallest OpenWorker-specific change, and
   avoids another process boundary.
2. **A signed Swift helper using length-prefixed JSON over standard I/O is the best fallback and
   the second option to prototype.** It uses the native framework directly and isolates Apple-only
   build requirements. It is preferable to an HTTP proxy because it does not expose a local port
   or pretend that Apple and OpenAI have identical semantics.
3. **Do not make native Apple tools execute OpenWorker tools in the first release.** Apple's tool
   callback is inside the model's response operation, while OpenWorker must propose, authorize,
   audit, possibly wait for a person, and only then execute. Bridging that safely requires a new
   interactive provider protocol, cancellation rules, durable approval handling, and reentrancy
   tests. It is an interesting later platform capability, not a shortcut.
4. **Context management is a release blocker, not an optimization.** OpenWorker currently sends
   `registry.schemas()` in full on every iteration and replays canonical history. A constrained
   on-device model needs deterministic tool filtering, history budgeting, bounded tool results,
   and useful context-overflow recovery.
5. **Several statements in the brief need correction or qualification.** Adding a descriptor is
   not sufficient for capabilities because `ProviderRouter.capabilities()` consults the global
   capability matrix/heuristics rather than the constructed client. Live availability is not
   represented by `ProviderDescriptor`. The context limit and vision support must be probed by OS,
   SDK, hardware, locale, and package build rather than hard-coded from a single documentation
   snapshot. The exact Python distribution/import names and binary-bundling behavior must also be
   proven in CI before design code is merged.

The recommendation is therefore **conditional**, not “the Python SDK is unquestionably the
cleanest route.” Run the direct-Python and Swift-helper spikes against the same fixtures, then use
the exit gates below. If Python survives signing/notarization and clean-machine testing, choose it;
otherwise ship the Swift helper without changing the engine-facing semantics.

## Scope and questions

This increment investigates access to Apple's on-device system language model from the OpenWorker
desktop product. It asks:

- Which boundary should own Apple framework access: Python, Swift helper, HTTP proxy, Tauri/Rust,
  or an in-process foreign-function interface?
- How should Apple output become OpenWorker `AssistantTurn` and `ToolCall` values without weakening
  approval and audit guarantees?
- How should streaming, sessions, attachments, availability, cancellation, packaging, and context
  limits work?
- What can be concluded from this repository today, what is documented externally, and what still
  requires an Apple-hardware experiment?

Out of scope are implementing the provider, changing the engine protocol, and claiming support on
machines that have not passed the compatibility matrix.

## Research method and evidence standard

### Repository inspection

The discovery inspected the provider contract, router, provider registry, model matrix and
capability fallback, engine loop, message conversion, tool authorization/execution, Python
packaging, and Tauri sidecar supervision. Repository conclusions in this document are direct code
observations, not assumptions.

### External sources

Prefer primary sources and pin every implementation spike to a specific SDK tag and Xcode build:

- [Apple's Python Foundation Models repository][apple-python-repo]
- [Python SDK tools documentation][apple-python-tools]
- [Python SDK guided generation documentation][apple-python-guided]
- [Python SDK streaming documentation][apple-python-streaming]
- [Python SDK session API][apple-python-session]
- [Apple Foundation Models documentation][apple-foundation-models]
- [SystemLanguageModel documentation][apple-system-model]
- [Managing the context window][apple-context]
- [Foundation Models updates][apple-updates]
- [Swift `LanguageModelSession` documentation][apple-session]

The execution environment used for this discovery could not retrieve those sites: the web search
service returned HTTP 401 and direct GitHub/Apple requests were rejected by the network proxy with
HTTP 403. Consequently, the external claims supplied in the brief are recorded below as
**source-linked but not independently reproduced in this increment**. This is deliberately not
called validation. The first feasibility spike must capture the SDK tag, package metadata, headers,
sample outputs, and documentation excerpts into its test report. A source URL existing is not proof
that a stated signature, deployment target, or binary behavior is current.

### Confidence labels

- **Verified (repository):** directly observed in the current OpenWorker source.
- **Documented, reproduce in spike:** attributed to the linked primary Apple source, but the source
  was not retrievable in this environment.
- **Hypothesis:** plausible design inference that requires code or hardware measurement.
- **Rejected:** conflicts with current OpenWorker code or violates a required invariant.

## Facts about the current OpenWorker architecture

### Provider boundary

`ProviderClient` is deliberately blocking and exposes `complete()`, `stream()`, and
`capabilities()`. A completion returns provider-neutral text and `ToolCall` objects. The engine
adapts the blocking stream to asyncio by running it in an executor thread and forwarding
`StreamChunk`s through a queue. **Verified (repository).**

This is a favorable seam for Apple, but with one important exception: it is a one-way request /
stream-response contract. It cannot suspend a provider-owned model operation, ask the engine to
authorize a callback, and return a tool result into that same operation.

### Routing is nearly, but not entirely, descriptor-driven

The router recognizes a `provider:model` prefix only when the provider registry contains that
descriptor, lazily constructs a client, caches it, strips the known prefix, and delegates
completion/streaming. Thus an `apple:system` identifier fits the existing routing scheme.
**Verified (repository).**

The brief's claim that adding a descriptor requires “little or no router modification” is correct
for dispatch, but incomplete for capabilities. `ProviderRouter.capabilities()` calls
`capabilities_for(model)` directly; it does **not** call the Apple client. Therefore the
implementation must add `apple:system` to the curated matrix (or change capability dispatch). A
client's runtime/build-dependent vision flag cannot be expressed faithfully by the present static
matrix alone.

### The engine owns the external tool loop

On every iteration, the engine:

1. requests a streamed provider turn;
2. persists the assistant turn;
3. emits `TOOL_PROPOSED` and audits each requested call;
4. applies special interactive-tool behavior or normal authorization;
5. executes approved tools (parallelizing only safe reads);
6. records a canonical `role=tool` result; and
7. calls the provider again with updated history.

This is the central safety invariant:

> **A provider may propose a tool call, but only OpenWorker authorizes, audits, and executes it.**

It also supports stop, steering, durable resume, and a maximum iteration count. Any Apple approach
must preserve those behaviors or explicitly redesign and retest them. **Verified (repository).**

### Every registered schema is currently sent

`TurnEngine._astream()` passes `self.registry.schemas() or None` on every model iteration. There is
no relevance filter or provider-specific schema budget at this seam. Conversation history is also
replayed, after display sidecars are stripped and attachments are adapted to declared capabilities.
**Verified (repository).**

This validates the concern but not the brief's unsubstantiated “25-plus connectors” as a stable
schema count. Connector count, configured accounts, MCP servers, and actual registered tools are
different quantities. The spike must log the actual serialized bytes and estimated tokens for
representative installations rather than using a marketing count.

### Desktop and packaging boundary

The Tauri shell supervises one packaged Python server in an onedir `sidecar/` resource, starts it on
a free loopback port, and kills it at exit. The app currently targets macOS 12.0 as well as Windows,
while the Python package supports Python 3.10+. Apple support must therefore be optional and
runtime-gated; it cannot make the core Python dependency set un-installable on other systems.
**Verified (repository).**

Supervising a second helper is structurally plausible, but it is not free: lifecycle, log capture,
protocol versioning, crash recovery, executable selection, universal binaries, signing, and
notarization all need implementation.

## Validation of the supplied claims

| Claim from brief | Finding | Consequence |
|---|---|---|
| An official `apple-fm-sdk` removes the need for Swift merely to access the model. | **Documented, reproduce in spike.** Verify the distribution name, Python import path, supported architectures, license, deployment target, and exact API against a pinned release. | Direct Python is the preferred spike, not yet a release commitment. |
| The SDK supports inference, streaming, guided JSON, transcripts, and native tools. | **Documented, reproduce in spike.** Each feature has a linked Apple page, but signatures and limitations were not independently retrieved here. | Build one executable fixture per feature; do not infer production composability from separate examples. |
| A descriptor alone makes `apple:system` work. | **Partly false.** Dispatch will work, but capabilities bypass the client and require matrix/probe work. Provider availability is also absent from the descriptor contract. | Add matrix handling and define an availability API/UI path. |
| `tools=True` can initially be advertised. | **False for phase 1.** Capabilities describe behavior now, not a roadmap. Text-only phase 1 must advertise `tools=False`; enable tools only after proposal conformance tests. | Prevent the engine/UI from assuming unimplemented tool support. |
| Vision can be enabled when built with a macOS 27 SDK. | **Documented, reproduce in spike; insufficient criterion.** Build-time symbols, runtime OS, model capability, Python binding support, hardware, and attachment conversion all matter. | Default `vision=False`; use a tested compatibility table before enabling it. |
| Native Apple tools would bypass OpenWorker approval. | **Correct in the naive wrapper design.** If the framework invokes `Tool.call()` and continues internally, executing there bypasses the engine. | Never put connector execution directly in an Apple callback. |
| Guided JSON can preserve the external tool loop. | **Sound hypothesis.** It can represent a proposal rather than execute it, if dynamic schemas work reliably and validation is strict. | This is the recommended initial tool architecture. |
| Use `arguments_json` as a string. | **Needlessly fragile.** It adds a second JSON parse and lets a structurally valid outer response contain invalid arguments. | Prefer a discriminated union with a real `arguments` object constrained by the selected tool's schema; use the string form only if SDK schema limitations force it. |
| Plain text can stream while tool turns return one final chunk. | **Compatible with the engine.** A provider can legally yield only a final `StreamChunk`. The UX trade-off is a longer silent wait. | Phase 1 can stream text; tool mode may be non-streaming initially with a visible “thinking” state and cancellation test. |
| A private event loop is required to bridge Apple async streaming. | **Not necessarily.** The engine already consumes the provider's synchronous iterator in a worker thread. The provider may own an event loop there, but the precise adapter depends on the SDK. | Keep event-loop ownership inside the provider; prohibit leaking SDK tasks across calls. |
| Native bidirectional tools are the strongest long-term design. | **Plausible, not established.** It may improve Apple's native planning, but greatly complicates approval waits, cancellation, restart, and auditing. | Benchmark it only after proposal mode is stable; adopt only for a measured quality gain. |
| An OpenAI-compatible proxy means almost no OpenWorker code. | **Overstated.** It still needs semantic translation, context policy, tool proposal behavior, errors, availability, process security, and tests. Reusing `OpenAIProvider` also loses typed Apple diagnostics. | Treat it as a separate interoperability product, not the default internal route. |
| A Swift helper fits the desktop architecture. | **Directionally correct.** The app already supervises a Python sidecar, but not a generic second service or stdio RPC channel. | It is the leading fallback, with explicit lifecycle work in its estimate. |
| The total session limit is always 4,096 tokens. | **Do not hard-code this as a universal fact.** It is attributed to Apple's context documentation and may vary by framework/OS/model generation; “token” accounting may not be exposed identically. | Discover limits at runtime where possible, use configurable conservative budgets, and regression-test each supported OS. |
| Fresh session per provider call is the safest initial strategy. | **Agreed.** OpenWorker owns canonical history and does not pass a stable conversation ID through `ProviderClient`. | Reconstruct each request deterministically; add session caching only with explicit IDs and invalidation semantics. |
| Flattening all roles into a prompt is an acceptable initial history conversion. | **Only for a throwaway spike.** It weakens role separation, makes tool-result provenance prompt text, and creates injection ambiguity. | Production should use transcript/message APIs if they can faithfully represent history; otherwise define and fuzz a strict serialization envelope. |
| The wheel can probably be bundled because it contains libraries. | **Hypothesis only.** Presence of `.a`/`.dylib` files does not establish redistribution rights, runtime linking, universal-architecture support, signing, or PyInstaller discovery. | Packaging is a go/no-go gate, not phase-3 polish. |
| Availability should show several live states. | **Correct product requirement, API unproven.** Current provider descriptors expose configuration metadata, not live health. | Add a separate provider status probe with typed reason codes; do not overload `capabilities()`. |

## Required semantics independent of transport

### Availability model

The provider needs a typed, non-generating probe, for example:

```python
@dataclass(frozen=True)
class ProviderAvailability:
    state: Literal["available", "unavailable", "not_ready", "unsupported", "error"]
    reason: str | None = None
    retryable: bool = False
    details: dict[str, str] = field(default_factory=dict)
```

The Apple adapter should map native reason codes without guessing from English exception text. The
UI can distinguish unsupported OS/hardware, Apple Intelligence disabled, unsupported language or
region, model assets not ready/downloading, and transient native errors. Do not silently fall back
to a cloud model: that violates the privacy expectation of an explicitly selected on-device model.
Offer an explicit user-controlled fallback instead.

### Canonical request conversion

Build one shared `AppleRequest` conversion layer and use it from both Python and Swift spikes:

- preserve system, user, assistant, and tool-result boundaries;
- strip foreign provider sidecars;
- reject or visibly placeholder unsupported images/PDFs, matching engine capability behavior;
- retain stable OpenWorker tool-call IDs in replay when the native transcript permits it;
- bound individual tool results before they reach the model, while keeping the full audited result
  in OpenWorker storage;
- never interpolate untrusted content into an instruction string without explicit delimiters; and
- make conversion deterministic so golden fixtures can compare both transports.

### Guided proposal envelope

With no tools, request ordinary text and stream it. With tools, request a structured discriminated
union conceptually equivalent to:

```json
{
  "oneOf": [
    {
      "type": "object",
      "properties": {
        "action": {"const": "respond"},
        "text": {"type": "string"}
      },
      "required": ["action", "text"],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "action": {"const": "call_tool"},
        "tool_name": {"enum": ["selected_tool_a", "selected_tool_b"]},
        "arguments": {"type": "object"}
      },
      "required": ["action", "tool_name", "arguments"],
      "additionalProperties": false
    }
  ]
}
```

If guided generation cannot express a union whose argument schema depends on `tool_name`, generate
one variant per selected tool, or perform strict host validation against the matching OpenWorker
JSON Schema. Never execute unknown tools, malformed arguments, or additional properties simply
because the model produced valid JSON. Convert a validated proposal to one `ToolCall` with a host
generated collision-resistant ID. Limit phase 2 to one call per model turn; the existing engine
loop naturally returns the result for another decision.

Treat “respond” and “call_tool” as mutually exclusive. Discarding model text after a tool proposal
should be explicit so unreviewed text is not presented as if execution succeeded.

### Context policy

Do not put model-specific truncation invisibly inside only one transport adapter. Define a reusable
`ContextPlan` so policy can later help Ollama and other small models:

1. Reserve fixed budgets for framework instructions, the current user turn, structured output, and
   safety margin.
2. Always include tools required by OpenWorker's interaction protocol (`ask_user`, plan/directory
   tools when applicable).
3. Select domain tools using deterministic lexical/embedding-free rules first: explicit mentions,
   enabled connector/account, recent tool use, and task vocabulary. Avoid asking the same small
   model to spend context selecting tools from the full schema set.
4. Rank at the toolkit level, then individual tools. Log selected and omitted tool names for
   debugging, but never secret arguments.
5. Include recent complete interaction groups; never retain an assistant tool call without its tool
   result or truncate JSON mid-message.
6. Summarize older history only through an auditable, bounded mechanism. A summary is untrusted
   conversation data, not system policy.
7. Truncate oversized tool results with a visible marker and, where possible, a handle for a
   follow-up paging/search tool.
8. On a context error, retry at most once with a smaller deterministic plan. Report the final error
   rather than entering an unbounded shrink loop.

Measure serialized UTF-8 bytes and the SDK's tokenizer/count API if one exists. A generic token
estimate should remain conservative and be labelled an estimate.

### Cancellation and concurrency

OpenWorker stop currently stops consuming between chunks; it does not prove cancellation of native
generation. Both adapters must expose a way to cancel/close the underlying session, and tests must
show that repeated stop/start does not leave model work running. Serialize calls per native session.
Fresh sessions per request simplify this. Define deadlines for availability, first token, total
generation, helper handshake, and helper shutdown.

## Approaches considered

### A. Direct Python provider with guided tool proposals

**Shape.** Add an optional `AppleFoundationProvider` that lazily imports the pinned Apple binding,
creates a fresh native session per call, converts canonical history, streams ordinary text, and
uses guided structured output when a selected tool set is present.

**Strengths**

- Fits the existing `ProviderClient` boundary and engine-owned tool loop.
- Minimal process/lifecycle surface and best typed error access from Python.
- Dynamic OpenWorker/MCP tool schemas can be selected per request.
- Keeps transport code localized and makes text-only delivery relatively small.

**Weaknesses and risks**

- The unverified package/build/linking story could fail in the signed PyInstaller onedir app.
- Python async/native stream cancellation may be awkward behind a blocking iterator.
- Guided generation quality may be worse than native tool registration.
- Runtime-dependent capabilities do not fit the current static matrix.
- It is easy to accidentally import Apple-only modules at registry import time and break Windows or
  older macOS. The import must occur inside construction/use, and the dependency must be guarded.

**Verdict:** rank 1, conditional on packaging and structured-output spikes.

### B. Signed Swift helper with guided tool proposals

**Shape.** Bundle a universal Swift executable that owns `LanguageModelSession`. The Python
provider launches it as a child and exchanges versioned, length-prefixed JSON frames over stdin and
stdout. Stderr is reserved for logs. Requests and responses use the same canonical conversion and
proposal envelope as approach A.

Use length-prefixing rather than newline-delimited JSON because model text and transcripts may
contain arbitrary newlines and large payloads. Include protocol version, request ID, frame type,
and terminal status. The helper must never execute OpenWorker tools.

**Strengths**

- Uses the native framework directly with the clearest Apple toolchain support.
- Isolates deployment-target and framework-linking concerns from the cross-platform Python server.
- Can adopt new native APIs without waiting for Python binding coverage.
- Stdio is private to the parent/child relationship and avoids an unauthenticated localhost port.

**Weaknesses and risks**

- Adds a second supervised child, protocol, crash/restart state machine, and log stream.
- Requires universal-build, signing, hardened-runtime, notarization, and clean-machine testing.
- Adds serialization overhead and more complex cancellation/backpressure.
- Duplicating history/schema conversion in Swift would cause drift; keep policy fixtures shared and
  generate protocol types where practical.

**Verdict:** rank 2 and the required fallback spike.

### C. Direct Python provider with native bidirectional tool callbacks

**Shape.** Register dynamic native tools whose callbacks emit a provider-tool-request event, wait
for OpenWorker authorization/execution, then resume the still-running native response with the real
result.

This cannot be implemented safely by merely adding a `Future` to `StreamChunk`. Approval may live
for minutes or survive a server restart; the producer is in an executor thread; the engine's inbox
and audit path are async; stop and steering can race the callback; and the model may request nested
or concurrent calls. A real protocol needs request IDs, acknowledgements, cancellation, deadlines,
maximum outstanding calls, durable state, and a rule for rejection results.

**Strengths:** native tool planning, potentially lower prompt/schema adaptation, and a general
interactive-provider capability if other providers need it.

**Weaknesses:** largest safety-sensitive engine redesign, provider-owned loop conflicts with durable
resume, and benefit is entirely unmeasured.

**Verdict:** rank 3 as a post-release experiment, only if evaluation shows proposal quality is the
bottleneck.

### D. OpenAI-compatible local proxy

**Shape.** A local Apple-specific service implements `/v1/models` and
`/v1/chat/completions`, translating OpenAI messages/tools/streaming to Apple semantics.

**Strengths:** reusable by non-OpenWorker clients; process isolation; the existing OpenAI adapter
can connect to a custom URL.

**Weaknesses:** semantic impedance is hidden rather than removed; Apple availability/context errors
become OpenAI-shaped; local-port authentication and origin exposure need design; process lifecycle
remains; proposal validation and context selection still need custom work; and generic clients may
assume unsupported OpenAI behaviors.

**Verdict:** rank 4 only if a separately maintained interoperability service is a product goal.

### E. Tauri/Rust command or Rust-to-Swift FFI

**Shape.** Expose generation through Tauri commands or link a Swift/C ABI shim into the Rust shell,
then route Python requests through the existing HTTP/WebSocket server or a new IPC path.

**Strengths:** model lifecycle can live in the native desktop process and native assets can be
bundled with the app.

**Weaknesses:** reverses the current dependency direction (the Python engine is the model caller),
requires Python-to-Tauri IPC, mixes long-running generation with UI-shell lifecycle, introduces
unsafe ABI/memory/cancellation concerns, and does not serve CLI/server deployments.

**Verdict:** rank 5; complexity without a demonstrated advantage over a Swift helper.

### F. Capture-only native Apple tools

**Shape.** Native tool callbacks record arguments and return a sentinel instead of executing. After
Apple completes its internal loop, the provider returns the captured proposals to OpenWorker.

The model can retry, generate dependent calls based on a fake result, or produce final prose that
assumes success. The transcript OpenWorker sees no longer matches the reasoning interaction.

**Verdict:** rank 6. Useful only as a disposable research fixture; never ship.

### G. Non-system local models through MLX/Core ML

This would run a separately downloaded model on Apple hardware and may be valuable to OpenWorker in
general, but it is not integration with `SystemLanguageModel` or Apple Intelligence. It changes
model weights, licensing, storage, context, quality, and update responsibility.

**Verdict:** excluded from this decision; track as a separate local-model initiative.

## Evaluation framework

Score each criterion from 1 (poor) to 5 (excellent). A weighted score is a decision aid, not a
substitute for a failed go/no-go gate.

| Criterion | Weight | What is measured |
|---|---:|---|
| Safety/invariant preservation | 20 | Engine remains sole authorizer/executor; denials, audit, stop, steering, and resume are correct. |
| Packaging and platform viability | 15 | Signed/notarized universal app works on clean supported Macs without Xcode; non-Apple installs remain healthy. |
| Semantic fidelity and quality | 15 | Correct role/history conversion, structured-output conformance, tool choice/arguments, answer quality. |
| Implementation and maintenance cost | 12 | New code/protocols/languages, upgrade burden, test surface, debugging difficulty. Higher score means lower cost. |
| Context efficiency | 10 | Useful-task tokens after instructions/history/schemas; overflow rate and recovery. |
| Reliability and observability | 10 | Typed diagnostics, crash recovery, deterministic conversion, logs/metrics, no leaked work. |
| Streaming, cancellation, latency | 8 | Time to first token, tokens/second, stop latency, IPC overhead, cold/warm behavior. |
| API reach and forward compatibility | 5 | Access to current/new Apple features without redesign. |
| Security and privacy surface | 5 | No exposed port, no cloud fallback, minimal secret/content boundary, safe logs. |

### Provisional ranking

These scores are architectural estimates. Replace them with measured spike scores; packaging and
safety gates override totals.

| Rank | Approach | Safety 20 | Package 15 | Fidelity 15 | Cost 12 | Context 10 | Reliability 10 | Stream 8 | Reach 5 | Security 5 | Weighted / 100 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | A. Python + guided proposals | 5 | 3 | 4 | 4 | 4 | 4 | 4 | 4 | 5 | **82.0** |
| 2 | B. Swift helper + guided proposals | 5 | 4 | 4 | 3 | 4 | 4 | 3 | 5 | 4 | **81.0** |
| 3 | D. OpenAI-compatible proxy | 4 | 3 | 3 | 3 | 3 | 3 | 3 | 4 | 2 | **64.0** |
| 4 | C. Python + bidirectional native tools | 3 | 3 | 5 | 1 | 4 | 2 | 3 | 4 | 4 | **63.2** |
| 5 | E. Tauri/Rust/Swift FFI | 3 | 3 | 4 | 1 | 4 | 2 | 4 | 5 | 4 | **62.8** |
| 6 | F. Capture-only native tools | 1 | 3 | 1 | 4 | 3 | 2 | 2 | 3 | 4 | **45.8** |

Although D narrowly out-scores C on estimated delivery risk, C is the more interesting later
research path because it could deliver materially better Apple-native agent behavior; D mainly
broadens client compatibility. Neither enters the top-two implementation plan.

### Mandatory go/no-go gates

An option is ineligible to ship if any gate fails:

- engine authorization and audit cannot remain authoritative;
- the signed/notarized app fails on a clean, non-developer supported Mac;
- importing/installing OpenWorker breaks Windows, older macOS, or unsupported Apple hardware;
- selecting on-device Apple silently sends prompts or fallback traffic to a network model;
- tool proposal schema conformance is below 99.5% on the fixed corpus after one bounded retry;
- stop cannot terminate or detach native generation within two seconds in 99% of trials;
- request/response logs contain prompt content or tool results by default; or
- the license/redistribution terms do not permit the intended binary distribution.

### Benchmark matrix

Test at least each supported OS minor and architecture/hardware family, Apple Intelligence on/off,
supported/unsupported system language/region, assets ready/not ready, and both development and
signed release builds. Include upgrade tests across the oldest and newest supported OS because the
system model changes with OS updates.

The behavioral corpus should include:

- 50 text-only prompts: short answer, long answer, Unicode, code, refusal, and injection attempts;
- 100 tool prompts: obvious tool, ambiguous tool, no-tool answer, invalid/extra arguments, denial,
  approval, interactive `ask_user`, sequential dependency, oversized result, and malicious result;
- context sweeps at 25/50/75/90/100/110% of the conservative budget;
- 100 streaming stops before first token, mid-token stream, during structured generation, and
  immediately after completion;
- transcript replay, model switch, retry, steering, durable resume, and max-iteration behavior; and
- attachment behavior for every advertised capability—never infer vision/PDF from successful text.

Record availability code, time to first token, completion latency, peak RSS, CPU/energy where
available, selected tool schemas, estimated/native token count, schema-valid rate, tool name and
argument accuracy, answer rubric, stop latency, and native error category. Store no raw user data;
the benchmark corpus must be synthetic and checked in.

## Implementation plans for the leading two options

### Plan A: direct Python provider with guided proposals

#### A0 — package/API feasibility spike (2–3 days)

1. Pin an exact official SDK tag/commit and record distribution name, import name, license,
   supported Python/macOS/architecture/Xcode versions, public availability enum, session creation,
   streaming, structured schema, transcript, cancellation, and attachment APIs.
2. On Apple hardware, build a minimal wheel and five fixtures: availability, text completion,
   streaming/cancel, guided union JSON, and transcript replay with a historical tool result.
3. Put the dependency in an `apple` optional extra with an OS marker; demonstrate core install and
   import on Linux/Windows and macOS 12 without importing Apple symbols.
4. Build the actual PyInstaller onedir sidecar, then sign/notarize/install it on a clean non-Xcode
   Mac. Inspect bundled libraries, load paths, architectures, signatures, entitlements, and license
   obligations.
5. Publish measurements and decide: pass continues to A1; package failure triggers Plan B.

**Exit:** all mandatory package/platform gates pass and documented API assumptions are replaced by
pinned, executable evidence.

#### A1 — text-only vertical slice (3–5 days)

1. Add `AppleFoundationProvider` with lazy imports, fresh session per call, strict model alias
   (`system` only), typed error translation, and guaranteed session close/cancel.
2. Register the optional descriptor and `apple:system` matrix row with truthful phase-1
   capabilities: tools/vision/PDF off, streaming on only if proven.
3. Add an availability endpoint/status contract and Settings display without marking unsupported
   hosts as “misconfigured.” Do not offer Apple in model pickers when it cannot run, unless shown
   disabled with the exact reason.
4. Implement canonical message-to-transcript conversion. If transcript construction cannot express
   current history safely, stop the slice rather than shipping role-flattened prompt text.
5. Add provider/router/conversion/error/stream cancellation tests with an injected fake Apple
   module so cross-platform CI runs them.

**Exit:** text benchmark passes; stop works; unsupported platforms start normally; no tools are
advertised or sent.

#### A2 — context planner and one-tool proposal (5–8 days)

1. Introduce a provider-neutral `ContextPlan`/tool-selection seam before `_astream()` rather than
   silently dropping schemas inside Apple conversion. Preserve mandatory interaction tools.
2. Add deterministic budgets, complete interaction-group truncation, bounded tool results, metrics,
   and one context-error retry.
3. Build the dynamic guided union from only selected tools. Validate result, selected name,
   arguments, and additional properties in the host; convert exactly one proposal to `ToolCall`.
4. When tools exist, return a final chunk first; add true structured streaming only if the SDK
   exposes safe partial semantics. Ensure Stop cancels generation during the silent interval.
5. Enable `tools=True` and `parallel_tool_calls=False` only after the conformance corpus meets its
   gate. Test approval, denial, audit, retry, steering, and durable resume end to end.

**Exit:** invariant, schema, context, and cancellation gates pass across the OS matrix.

#### A3 — release hardening (3–5 days plus CI provisioning)

1. Add signed release CI on the minimum and newest supported Apple OS/toolchain combinations.
2. Add privacy-safe diagnostics and compatibility metadata (OS build, SDK adapter version,
   availability code; no prompts).
3. Document model variability and explicit fallback behavior. Provide removal/disable controls.
4. Keep vision/PDF off until separate attachment fixtures pass on every advertised combination.

### Plan B: signed Swift helper with guided proposals

Plan B intentionally preserves Plan A's engine semantics and benchmark corpus. Only the native
transport changes.

#### B0 — native and protocol spike (3–4 days)

1. Implement a minimal Swift CLI using the pinned Xcode SDK: availability, completion,
   streaming/cancel, guided JSON, and transcript replay.
2. Define protocol v1 with 32-bit big-endian length + UTF-8 JSON frames, maximum frame size,
   request IDs, `hello`, `request`, `delta`, `complete`, `error`, `cancel`, and `shutdown` frames.
   Reject unknown major versions and oversized frames before allocation.
3. Build a universal binary, sign/notarize it inside the app, and verify launch from the hardened
   runtime on a clean Mac. Confirm it cannot be selected on Windows/unsupported macOS.
4. Compare behavior and latency against A0 fixtures. Choose B if Python packaging fails or Swift
   produces a material support/quality advantage worth the maintenance cost.

#### B1 — supervised helper adapter (5–7 days)

1. Add a Python `AppleFoundationProvider` transport interface and Swift-helper implementation so
   routing, availability, history policy, proposal validation, and error categories remain common.
2. Supervise one helper per Python server (not per turn); capture stderr to the existing state log
   policy, redact content, detect exit, fail in-flight requests, and restart once with backoff.
3. Enforce one active generation initially. Implement cancellation acknowledgement and hard-kill
   fallback within the stop deadline. On shutdown, send `shutdown`, wait briefly, then kill.
4. Bundle helper path resolution explicitly rather than relying on `$PATH`. Verify parent-death
   behavior so no orphan process remains.
5. Run malformed/truncated/oversized frame tests and fuzz the decoder in both languages.

#### B2 — text, context, and tools (5–8 days)

1. Deliver the same A1 text-only behavior through the helper.
2. Reuse the A2 context plan before serialization. Send selected schemas and canonical messages,
   not an already flattened giant prompt.
3. Generate guided output in Swift, but validate again in Python before creating `ToolCall`; the
   process boundary is not a trust boundary that permits execution.
4. Run the identical conformance, safety, context, availability, upgrade, and clean-machine suite.

**Exit:** Plan B ships only if it passes every common gate and its extra process/protocol tests.

## Recommended decision and sequencing

1. Approve **A0 and B0 as discovery spikes**, not parallel production implementations.
2. Start A0 first. Begin B0 immediately if wheel/signing evidence is negative or the Python binding
   lacks required structured/transcript/cancellation APIs.
3. Select one transport at the gate. Share request conversion, context planning, proposal
   validation, fixtures, and error taxonomy so switching transport does not change safety behavior.
4. Ship text-only behind an experimental flag before tools. Advertise only capabilities that are
   actually enabled.
5. Ship guided one-tool proposals after the context and conformance gates.
6. Evaluate bidirectional native tools only if an A/B benchmark shows a meaningful tool-success
   deficit that prompt/schema improvements cannot close. Require a separate architecture decision
   record for the interactive provider protocol.

## Open questions the spikes must close

- What are the exact current package and import names, redistribution license, SDK tag, deployment
  targets, architectures, and toolchain requirements?
- Does the Python binding distribute a wheel, build locally, or require Xcode at end-user install?
  Which libraries must be collected and signed in PyInstaller onedir output?
- Can its guided schema express discriminated unions, enums of runtime tool names, nested argument
  schemas, `$ref`, `additionalProperties`, and useful validation errors?
- Can transcript construction faithfully represent OpenWorker's historical assistant calls and tool
  results without registering executable native tools?
- Is streaming an async iterator, what is the terminal response shape, and how is native generation
  cancelled rather than merely ignored?
- What stable availability reason codes exist? Can model asset download progress be observed, or
  only “not ready”?
- What context-counting API exists, what is the effective limit on each supported OS, and how much
  overhead do schemas/guides/transcripts consume?
- Which image/media APIs exist in each SDK and runtime combination, and can the Python binding load
  OpenWorker's attachment representation without copies or unsafe temporary files?
- Does the framework permit concurrent sessions, and what are the thermal/memory consequences?
- How does behavior change after an OS update, and can diagnostics identify the effective system
  model generation without fingerprinting the user?

## Non-goals and explicit rejections

- No provider callback may directly call `ToolRegistry` or a connector.
- No capture-only sentinel tool implementation may ship.
- No automatic cloud fallback may occur for an explicitly selected Apple on-device model.
- No Apple dependency may become a mandatory core dependency.
- No `vision=True`, `tools=True`, or streaming claim may be set in anticipation of later work.
- No unauthenticated localhost Apple proxy should be introduced solely to reuse OpenAI-shaped code.
- No persistent native session cache should precede stable conversation IDs, invalidation semantics,
  and transcript equivalence tests.

## References

Primary links are intentionally retained as verification targets for the hardware spikes. Remove
tracking query parameters and record the accessed SDK tag/date in the resulting test report.

[apple-python-repo]: https://github.com/apple/python-apple-fm-sdk
[apple-python-tools]: https://apple.github.io/python-apple-fm-sdk/tools.html
[apple-python-guided]: https://apple.github.io/python-apple-fm-sdk/guided_generation.html
[apple-python-streaming]: https://apple.github.io/python-apple-fm-sdk/streaming.html
[apple-python-session]: https://apple.github.io/python-apple-fm-sdk/api/session.html
[apple-foundation-models]: https://developer.apple.com/documentation/foundationmodels
[apple-system-model]: https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel
[apple-context]: https://developer.apple.com/documentation/foundationmodels/managing-the-context-window
[apple-updates]: https://developer.apple.com/documentation/updates/foundationmodels
[apple-session]: https://developer.apple.com/documentation/foundationmodels/languagemodelsession
