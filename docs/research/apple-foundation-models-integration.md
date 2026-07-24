# Apple Foundation Models integration decision

**Status:** architecture decision, working provider POC, and implementation plan
**Decision date:** 2026-07-23
**Decision:** build an optional, first-class Python provider using Apple's official Python SDK
**Tool strategy:** preserve OpenWorker's external authorization loop by generating tool proposals; do not execute OpenWorker tools inside Apple callbacks
**Fallback:** a signed Swift standard-I/O helper, only if the Python SDK fails the packaging gate

## Decision

If starting from a blank slate, OpenWorker should integrate Apple's on-device model through
Apple's official `apple-fm-sdk` package in the existing Python server process.

This is a firm architecture choice, not a proposal to prototype Python and Swift equally and
decide later. The Python path is the best fit because:

- OpenWorker's provider boundary, agent loop, approvals, audit log, persistence, and attachment
  adaptation already live in Python.
- Apple's Python SDK exposes the capabilities needed for the first release: availability,
  sessions, streaming, guided generation from runtime JSON Schema, tool callbacks, transcripts,
  token counting, context size, and, when built with the macOS 27 SDK, image prompts.
- It avoids a second process, a private RPC protocol, duplicate lifecycle and cancellation code,
  and a second implementation of message and error conversion.
- Two guided generations can first choose a tool and then produce arguments constrained by that
  tool's schema. The working POC returns a normal OpenWorker `ToolCall` without letting Apple
  execute the tool. OpenWorker remains the only component that authorizes, audits, and executes
  actions.

The SDK is alpha software distributed only as source in version 0.2.1. A live POC validates the
core provider architecture, but packaging and clean-machine operation remain unproven release
risks. Those are Phase 0 gates, not reasons to choose a permanently more complex architecture.

If the Python package cannot be embedded, signed, notarized, and run on clean supported Macs,
implement the same provider semantics behind a signed Swift helper using framed JSON over
standard input/output. Do not fall back to an HTTP proxy or the `fm` command-line tool.

## Research method

The previous version of this document was treated as an untrusted input. This decision was
rebuilt from:

1. current OpenWorker source, especially the provider, router, engine, attachment, catalog, GUI,
   and packaging boundaries;
2. Apple's current Foundation Models documentation and WWDC25/WWDC26 sessions;
3. the source and tagged release of Apple's Python SDK, not a third-party wrapper; and
4. a local installation and runtime probe on Apple silicon.

The recommendation does not assume that an API listed by Apple works in OpenWorker's packaged
application. Every distribution-sensitive claim becomes an explicit gate below.

## Product requirements and invariants

An acceptable integration must:

- keep model input and output on-device when the user selects the Apple on-device model;
- never silently switch to a cloud model;
- preserve OpenWorker's rule that providers propose actions while the engine authorizes, audits,
  and executes them;
- keep Windows and macOS 12-25 installations working even though Apple Foundation Models requires
  newer Apple software and compatible Apple Intelligence hardware;
- expose a useful unavailable state rather than failing only after the user sends a prompt;
- preserve stop, steering, durable history, tool results, and bounded agent iterations;
- account for the model's actual context size and the token cost of instructions, history,
  generation schemas, and tools;
- support a signed and notarized desktop release without requiring Xcode on an end-user's Mac;
  and
- be testable without Apple hardware for conversion logic, with separate hardware tests for
  framework behavior.

These requirements matter more than minimizing the number of new files.

## Current facts

### Apple platform

Apple introduced the native Swift Foundation Models framework in macOS 26. The system model is
available only when the OS, hardware, Apple Intelligence settings, language/locale, and model
assets permit it. Applications are expected to query availability before use.

Apple's February 2026 update added `contextSize` and token-counting APIs. The context size must be
queried rather than treated as a permanent constant: Apple changes the system model with OS
updates and explicitly tells developers to retest prompts against new model versions.

The macOS 27 APIs are still beta as of this decision. They add:

- a rebuilt on-device model;
- image input;
- a public `LanguageModel` abstraction for Apple, third-party, and open-source models;
- Dynamic Profiles for changing instructions, tools, and models within a session;
- Private Cloud Compute access;
- Foundation Models framework utilities; and
- an `fm` command-line tool.

Those additions broaden the alternative set, but most do not simplify an OpenWorker provider.
OpenWorker already has its own provider abstraction and agent orchestration.

### Official Python SDK

Apple's official Python SDK 0.2.1:

- requires macOS 26+, full Xcode 26+, Python 3.10+, Apple Intelligence, and compatible hardware;
- is marked alpha;
- is published to PyPI as a source distribution, not a wheel;
- builds a Swift/C dynamic library and Python `ctypes` binding during installation;
- supports availability, sessions, streaming, cancellation, transcripts, guided generation,
  runtime JSON Schema, native tool callbacks, context-size queries, and token counting;
- conditionally compiles image attachment support when the selected SDK is macOS 27+; and
- links its small generated library to the system `FoundationModels.framework`; it does not ship
  the model weights.

The package's full-Xcode build requirement is a build-machine requirement. OpenWorker must prove
that a prebuilt, signed copy can be included in the PyInstaller sidecar so users do not need
Xcode.

### Working proof of concept

The following probe was run on 2026-07-23:

- Apple silicon (`arm64`)
- macOS 27.0 beta
- Xcode 27.0 beta
- Python 3.14.6
- `apple-fm-sdk==0.2.1` installed from PyPI

Observed results:

- source build and import succeeded;
- `SystemLanguageModel.is_available()` returned available;
- `context_size` returned 4,096;
- the generated binding was about 1.2 MB and linked to the system Foundation Models framework;
- prompt and instruction token counting succeeded;
- plain completion and cumulative-snapshot streaming succeeded;
- the POC converted Apple snapshots into OpenWorker text deltas and a final `AssistantTurn`;
- guided tool routing selected `read_file` for an explicit file request;
- a second guided generation produced typed arguments `{"path":"README.md"}`;
- the POC returned that proposal as an OpenWorker `ToolCall` without executing it; and
- cancelling after the first streamed delta stopped the stream without a final turn.

The POC is
[`coworker/providers/apple_foundation_poc.py`](../../coworker/providers/apple_foundation_poc.py).
It is deliberately not registered in the production provider catalog. Nine SDK-free tests in
[`tests/test_apple_foundation_poc.py`](../../tests/test_apple_foundation_poc.py) cover availability,
canonical serialization, Apple schema normalization, text completion, final-message routing,
two-stage tool routing, streaming delta conversion, and invalid tool schemas.

With `apple-fm-sdk==0.2.1` installed, the live tool path is repeatable without registering or
executing a tool:

```sh
python -m coworker.providers.apple_foundation_poc --tool-demo
```

The POC found three concrete requirements that the API overview did not reveal:

1. Apple's raw JSON-schema decoder requires `x-order` on object schemas.
2. Object schemas, including imported OpenWorker tool-argument schemas, require `title`.
3. Closing the outer async iterator is insufficient after cancellation; the provider-owned event
   loop must also run `shutdown_asyncgens()` before closing or Python reports a leaked pending task.

The first exploratory calls also produced generic status 255 and decoding failures before schema
normalization and clean async teardown were in place. This is evidence that “imports successfully”
is not enough and that the release matrix must run complete provider operations. The POC proves
the source-checkout architecture on this beta machine; it does not prove PyInstaller collection,
signing, notarization, clean-machine use without Xcode, macOS 26 compatibility, or model quality
across a representative tool set.

### OpenWorker

OpenWorker already has the correct high-level ownership:

- `ProviderClient` accepts canonical messages and optional tool schemas and returns an
  `AssistantTurn` containing text and/or `ToolCall` values.
- `ProviderRouter` dispatches known `provider:model` identifiers to cached provider clients.
- `TurnEngine` persists the assistant turn, asks for authorization, audits, executes approved
  tools, records tool results, and invokes the provider again.
- the engine adapts a blocking provider stream to asyncio in a worker thread.
- attachments are stored in an OpenAI-shaped canonical form and adapted for the selected model.
- the desktop app packages the Python server as a PyInstaller onedir resource and supports
  macOS 12+ and Windows.

The main mismatches to solve are:

- `ProviderRouter.capabilities()` uses the static capability matrix instead of asking a live
  provider client;
- provider descriptors do not represent runtime availability;
- `TurnEngine` sends every registered schema on every model iteration;
- the provider contract carries canonical history but no stable conversation identifier for a
  stateful native session; and
- stopping the engine stops consumption but does not currently define a provider-specific
  cancellation hook for an in-flight native generation.

## Decision criteria

Each alternative is scored from 1 (poor) to 5 (strong). Weighted totals are out of 5.

| Criterion | Weight | What earns a high score |
|---|---:|---|
| Safety and semantic fit | 25% | Preserves engine-owned approval, audit, stop, and canonical history |
| Product coverage | 20% | Text, streaming, tools, availability, context control, and a path to vision |
| Distribution risk | 20% | Works in the signed app without breaking unsupported platforms |
| Maintainability | 15% | Small amount of single-language code with an upstream-supported boundary |
| Runtime and UX | 10% | Low overhead, good cancellation, diagnostics, and no exposed service |
| Strategic fit | 10% | Tracks Apple's supported direction without replacing OpenWorker architecture |

Distribution is weighted as highly as product coverage because a solution that works only from a
developer shell is not an OpenWorker integration. Safety is highest because bypassing approvals
would be a product defect, not an implementation detail.

## Alternatives

| Alternative | Safety | Coverage | Distribution | Maintenance | Runtime | Strategy | Weighted |
|---|---:|---:|---:|---:|---:|---:|---:|
| Direct Python SDK + guided tool proposals | 5 | 4 | 2 | 5 | 4 | 4 | **4.00** |
| Swift stdio helper + guided tool proposals | 5 | 4 | 3 | 2 | 3 | 5 | **3.75** |
| Direct Python SDK + native callback approval bridge | 2 | 5 | 2 | 3 | 4 | 4 | **3.15** |
| Swift/OpenAI-compatible local proxy | 4 | 4 | 3 | 2 | 2 | 4 | **3.30** |
| `fm` CLI subprocess | 3 | 2 | 2 | 4 | 2 | 2 | **2.55** |
| Tauri/Rust-to-Swift bridge | 3 | 4 | 2 | 1 | 4 | 3 | **2.80** |
| Custom PyObjC/FFI binding | 2 | 3 | 1 | 1 | 4 | 1 | **1.95** |
| Do not integrate yet | 5 | 1 | 5 | 5 | 1 | 2 | **3.50** |

### 1. Direct Python SDK with guided proposals — selected

Use the official Python SDK in the existing server. When no tools are present, use ordinary
streaming. When tools are present, request a guided, discriminated response that is either:

- a final assistant message; or
- one validated OpenWorker tool proposal.

Return the proposal as an OpenWorker `ToolCall`. The engine then performs its normal approval and
execution loop.

This loses Apple's internal multi-call tool planning in the first release and guided generation is
not streamed in SDK 0.2.1. Those limitations are preferable to weakening authorization.

### 2. Swift stdio helper — packaging fallback

Build a small universal Swift executable using the native framework and exchange versioned,
length-prefixed JSON messages over standard I/O. Keep message conversion and tool-proposal
semantics identical to the Python provider.

This is the best fallback because it uses Apple's primary API and gives explicit control over
signing. It is not the first choice because it duplicates conversion and error mapping, adds a
second child process, and requires supervision, backpressure, restart, cancellation, protocol
versioning, and log redaction.

### 3. Native tool callbacks with an approval bridge — reject for v1

The Python SDK now supports native tool callbacks, so this must be evaluated rather than described
as unavailable. However, the callback occurs inside Apple's response operation. OpenWorker would
need to suspend that operation, cross from the SDK's async context into `TurnEngine`, persist and
display the proposal, wait through immediate or durable approval, return a result, and correctly
handle stop, restart, timeout, denial, and steering.

Executing the connector in the callback is prohibited. Returning a fake “pending approval” tool
result lets the model continue from something that did not happen. Raising an exception to capture
arguments relies on undocumented transcript behavior. Revisit native callbacks only after guided
proposal mode ships and an evaluation shows a material quality advantage.

### 4. Local OpenAI-compatible proxy — reject

A Swift proxy or Apple's Foundation Models framework utilities could expose chat-completions-like
HTTP. That does not remove semantic work: history, availability, guided proposals, errors,
attachments, context budgeting, and cancellation still require translation. It also opens and
secures another local service and obscures Apple-specific diagnostics. A private stdio helper is a
better fallback.

### 5. `fm` CLI — reject as an application API

The new CLI is useful for people and shell scripts on macOS 27. It is not the documented embedding
surface for a signed app, does not support macOS 26, and does not provide OpenWorker's stable typed
request, event, cancellation, and error contract. It is useful as a manual diagnostic oracle, not
as the provider transport.

### 6. Tauri/Rust bridge — reject

Moving Apple access into the GUI shell puts model behavior in a platform-specific layer while the
agent loop and server remain in Python. It creates cross-process callbacks for every model event
and makes headless/server use diverge from desktop use.

### 7. Custom PyObjC or C FFI — reject

Foundation Models is a Swift-first framework. Rebuilding and maintaining a private bridge duplicates
the official Python SDK and follows a less supported ABI. There is no compensating product benefit.

### 8. Defer — reject

Waiting avoids alpha-SDK risk but delivers no on-device model. A gated optional provider can be
developed without affecting unsupported platforms, and Phase 0 can stop the release implementation
if packaging is not viable.

## Alternatives that were previously missing or misframed

The alternative set changes in 2026:

- The official Python SDK is no longer inference-only; version 0.2.1 contains native tools, raw
  JSON-schema generation, token measurement, transcripts, and conditional image support.
- The `fm` CLI is a new but unsuitable embedding option.
- Apple's `LanguageModel` protocol and Foundation Models utilities make a Swift proxy more
  plausible, but OpenWorker does not benefit from replacing its provider abstraction with Apple's.
- Private Cloud Compute is a separate cloud-backed product choice, not an automatic extension of
  an explicitly on-device model. It must not be hidden behind `apple:system`.
- Core AI and MLX model implementations provide other local models, not Apple's system model.
  OpenWorker can evaluate them later as separate providers; they should not complicate this work.
- A native callback approval bridge is now technically imaginable in both Swift and Python, but it
  is an orchestration redesign rather than a shortcut.

## Implementation plan

### Phase 0 — prove the release boundary

Do this before production provider code.

1. Pin `apple-fm-sdk` to an reviewed tag and source hash. Keep it out of core dependencies so
   Windows and older macOS source installs do not attempt to build it.
2. Build an arm64 wheel in a controlled macOS/Xcode environment. Record the SDK and deployment
   target used to build the Swift library.
3. Add a minimal PyInstaller experiment that imports the package lazily, then:
   - signs the embedded dynamic library and sidecar;
   - notarizes the app;
   - launches the server on macOS 12-25 without importing Apple code;
   - performs availability, text generation, streaming cancellation, guided JSON, native-tool
     fixture, token count, and context-size calls on supported stable macOS 26 and 27; and
   - repeats on a clean Mac without Xcode.
4. Capture structured native errors and logs for every failed operation. A generic status 255 is
   not an acceptable user-facing diagnostic.
5. Confirm the Apache-2.0 SDK code and built binding can be redistributed under OpenWorker's
   release process.

**Gate:** continue only when the packaged, notarized app passes on a clean supported Mac and still
starts on unsupported Macs. If it fails, implement the Swift stdio fallback with the same
acceptance tests. Do not change tool semantics to rescue packaging.

### Phase 1 — text provider and availability

1. Add `AppleFoundationProvider` behind a lazy optional import.
2. Add a no-secret Apple provider descriptor and the model identifier `apple:system`.
3. Add conservative matrix capabilities: tools off until Phase 3, vision off until Phase 4,
   streaming on, PDF off.
4. Introduce a typed provider availability result separate from model capabilities. Map Apple's
   native availability reason into stable codes such as unsupported OS, unsupported hardware,
   Apple Intelligence disabled, model assets unavailable, locale unavailable, and internal error.
5. Add the server endpoint and GUI state needed to show Apple only when supported and explain why
   it is unavailable. Never silently choose another provider.
6. Build an `AppleHistoryAdapter` that preserves system, user, assistant, and tool-result
   boundaries. Prefer reconstructing an Apple transcript from canonical history. If the released
   SDK cannot safely construct that transcript, use a documented, length-delimited serialization
   and make transcript fidelity an explicit evaluation gate.
7. Bridge the SDK's async API inside the provider's worker thread. The bridge owns and closes its
   event loop per request; no SDK task may outlive the provider call.
8. Implement text streaming with cumulative-snapshot-to-delta conversion if required by the SDK.
   Retain the final complete turn even when a stream yields no text deltas.
9. Add a provider cancellation hook so Stop cancels the native response, rather than merely
   dropping later chunks.

**Exit:** text conversations stream, stop promptly, persist, resume from canonical history, and
produce actionable availability and generation errors.

### Phase 2 — context policy

1. Query `context_size` at runtime. Never hard-code 4,096 as a universal limit.
2. Use Apple's token-count API to measure instructions, history, tool schemas, and the response
   schema. Cache counts only for immutable inputs and the current model/OS build.
3. Reserve a configurable response budget before sending a request.
4. Add deterministic tool selection before the provider call:
   - always retain tools explicitly required by the active persona or current workflow;
   - prefer tools named or described by the current user request;
   - preserve paired control tools needed to complete an operation; and
   - fail visibly if required schemas alone exceed the budget.
5. Bound large tool results before the next iteration while preserving durable full output outside
   the prompt where the tool supports it.
6. Trim oldest eligible history only through one tested policy. Never drop system instructions,
   the current user request, unresolved tool calls, or their results.
7. Emit context diagnostics in debug logs: maximum, reserved output, instructions, history, tools,
   schema, and final total.

**Exit:** representative personas fit deterministically, overflow is actionable, and the same
input produces the same selected tools and history.

### Phase 3 — engine-owned tool proposals

1. Turn on tool capability only after this phase passes.
2. Use the two-stage guided design proven by the POC:
   - the first compact schema chooses `kind=message` or `kind=tool`, returns message text, and
     constrains `tool_name` to the selected catalog;
   - for `kind=message`, return the text immediately; and
   - for `kind=tool`, make a second guided call whose entire schema is the selected tool's argument
     schema.
3. Normalize every object schema with `title`, `x-order`, and
   `additionalProperties: false`. Reject unsupported schema constructs before generation.
4. Keep arguments structurally typed. Do not encode arguments as a JSON string.
5. When tools are present, use non-streaming guided generation in the initial release. Display a
   cancellable thinking state rather than fake token streaming.
6. Reject unknown tools, extra properties, malformed arguments, duplicate identifiers, and
   inconsistent decision fields.
7. Return at most one `ToolCall` per Apple response. The existing engine loop handles subsequent
   calls after authorization and tool results.
8. Continue to use ordinary text streaming when no tools are available.
9. Add behavioral fixtures for choosing no tool, choosing the right tool, read approval, write
   approval, denial, stop while waiting, tool error, large result, and multi-iteration work.

**Exit:** no connector code runs inside the Apple SDK, every action appears in the normal approval
and audit stream, and adversarial output cannot forge an executable call.

### Phase 4 — vision on macOS 27

1. Enable vision only when all of these are true:
   - the Python binding was built with a macOS 27 SDK;
   - runtime macOS is 27+;
   - the selected model reports image capability; and
   - an image fixture succeeds.
2. Convert only validated local image attachments into `ImageAttachment` values.
3. Preserve attachment labels and enforce OpenWorker's size/count limits.
4. Keep PDFs on the existing fallback path until Apple documents and the provider tests a native
   PDF representation.

**Exit:** capability advertising matches runtime behavior; macOS 26 never receives image objects.

### Phase 5 — packaging, evaluation, and rollout

1. Add unit tests with a fake Apple SDK for routing, conversion, schema construction, cancellation,
   availability, context selection, and errors. Promote and extend the POC tests rather than
   rewriting them.
2. Add opt-in hardware integration tests for stable macOS 26 and 27.
3. Build a checked-in evaluation set covering ordinary chat, canonical-history fidelity, tool
   selection, argument accuracy, refusal behavior, prompt injection in tool results, and context
   pressure. Record OS and model build with every result.
4. Run the signed/notarized clean-machine matrix on each Apple SDK or supported macOS update.
5. Release behind an experimental on-device-model flag first. Show the actual limitations:
   compatible Mac required, smaller context, tool turns initially non-streaming, and no implicit
   cloud fallback.
6. Collect only local diagnostics by default. Any telemetry must be opt-in and must not include
   prompts, tool arguments, tool results, or attachments.
7. Define rollback as disabling the Apple descriptor in the model catalog while leaving other
   providers and stored conversations intact.

**Release criteria:** all safety fixtures pass, packaged stable-OS generation works without Xcode,
unsupported platforms still launch, context overflow is understandable, and the evaluation set
meets thresholds chosen before measuring the release candidate.

## Deferred work

After the guided-proposal provider is stable:

- evaluate a native callback approval bridge against the same tool-quality set;
- adopt it only if the quality improvement justifies its durable approval, restart, and
  cancellation complexity;
- evaluate Apple's Private Cloud Compute as an explicitly named, separately consented provider;
- evaluate Core AI or MLX models as separate local providers; and
- consider stateful Apple sessions only after the provider contract has an explicit conversation
  identity and invalidation model.

## Sources

Primary sources used for this decision:

- [Apple Foundation Models documentation](https://developer.apple.com/documentation/foundationmodels)
- [Apple Foundation Models updates](https://developer.apple.com/documentation/Updates/FoundationModels)
- [WWDC26: What's new in the Foundation Models framework](https://developer.apple.com/videos/play/wwdc2026/241/)
- [WWDC26: Build agentic app experiences with the Foundation Models framework](https://developer.apple.com/videos/play/wwdc2026/242/)
- [WWDC26: Bring an LLM provider to the Foundation Models framework](https://developer.apple.com/videos/play/wwdc2026/339/)
- [WWDC25: Deep dive into the Foundation Models framework](https://developer.apple.com/videos/play/wwdc2025/301/)
- [Apple's Python Foundation Models SDK](https://github.com/apple/python-apple-fm-sdk/tree/v0.2.1)
- [Python SDK 0.2.1 build backend](https://github.com/apple/python-apple-fm-sdk/blob/v0.2.1/build_backend.py)
- [Python SDK 0.2.1 package metadata](https://github.com/apple/python-apple-fm-sdk/blob/v0.2.1/pyproject.toml)
- [Python SDK tool documentation](https://github.com/apple/python-apple-fm-sdk/blob/v0.2.1/docs/source/tools.rst)
- [Python SDK session implementation](https://github.com/apple/python-apple-fm-sdk/blob/v0.2.1/src/apple_fm_sdk/session.py)
- [Apple Support: Apple Intelligence device requirements](https://support.apple.com/en-us/121115)
