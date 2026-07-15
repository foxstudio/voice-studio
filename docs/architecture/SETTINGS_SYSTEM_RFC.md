# Settings System Architecture and UI RFC

**Status**: proposed, audit complete, implementation not started
**Date**: 2026-07-14
**Scope**: Voice Studio settings contracts, persistence, provider configuration, storage management, frontend state, information architecture, and visual consistency.
**Related**: `ARCHITECTURE_REFACTOR_ROADMAP.md`, `ENGINE_PROVIDER_POLICY_RFC.md`, `DIRECTORY_GOVERNANCE_RFC.md`, `SCHEMA_COMPATIBILITY_RFC.md`.

## 中文决策摘要

这次不是单纯换一套颜色或重新排版，而是把设置体系从“一个页面堆全部字段”改成“按用户任务组织、每一项都真实生效、状态和下一步一眼可见”。

### 已确认的事实

- 当前设置页 1321 行，页面状态、保存逻辑、凭据、LLM、存储操作和约 560 行样式都在一个文件中。
- 后端 `settings_store.py` 616 行，同时处理普通配置、密钥、LLM、模型路径、目录、磁盘扫描、清理和打开 Finder。
- 当前相关测试、`svelte-check` 和生产构建通过，说明它现在能用；问题是边界和保存契约已经不适合继续堆功能。
- 已隔离复现两个必须先修的问题：部分 PATCH 会重置未提交字段；非法豆包 URL 会返回 500，而不是正常字段错误。
- LLM 目前只完成连接配置和测试，字幕校对、语义断句、本土化等业务尚未真正调用它。
- `data_dir` 并不是可以在运行中安全迁移数据库和所有子目录的总开关。

### 推荐页面结构

```text
设置                           [2 项未保存] [保存更改]

概览
常用设置        默认引擎、音色、语言、输出格式
云服务          MiMo、豆包的状态、作用和下一步
AI 助手         LLM 连接、模型和真实启用状态
文件与存储      当前占用、打开位置、可安全清理项
高级设置        路径、Resource ID、端点和重启项
```

首页不再直接展示所有技术字段，而是先告诉用户：现在用什么、哪些服务可用、缺什么、下一步按哪里。Base URL、Resource ID、AK/SK 和路径等进入对应服务的管理区或高级设置。

### 推荐实施顺序

1. 先修请求校验和部分 PATCH，补齐事务、冲突和回归测试。
2. 盘点每个设置的真实消费者，隐藏或删除“能保存但不生效”的假设置。
3. 拆出配置解析、事务仓储、密钥、Provider 状态和存储服务，旧接口暂作兼容层。
4. 再拆前端 controller、API client 和设置分区，先保持行为一致。
5. 捕获当前真实页面，产出三套视觉方案，选定后再实施统一 token 和组件。
6. 最后做真实保存/刷新/业务消费、响应式、键盘和无障碍验收。

### 需要用户拍板

- 数据根目录是否只读，还是要做真正带验证和回滚的数据迁移向导。
- LLM 本轮是否接入至少一个真实业务；如果不接，页面必须明确标为“连接配置，功能接入中”。
- 正式启动方式是否永久只允许本机访问；局域网模式需要另做认证和安全设计。
- 专用浏览器工具本轮初始化失败，是否允许使用项目内 Playwright 直接捕获页面截图。

## 1. Decision Summary

The settings surface should be rebuilt as a set of task-oriented sections backed by explicit domain contracts. It should not remain one large form, and it should not be redesigned as a visual-only pass over the current save behavior.

The recommended direction is:

- Keep the application as a local modular monolith.
- Fix the unsafe settings contract before changing the layout.
- Split preferences, providers, storage, and interface settings into separate API and state boundaries.
- Resolve configuration through one service that reports effective value and source.
- Keep secrets write-only and expose connection status rather than secret fields.
- Reuse the existing engine/provider architecture instead of creating a second registry.
- Make the first screen explain current state and the next action without requiring a help drawer.
- Move technical values such as Base URL, resource IDs, and filesystem paths into advanced views.

This is a medium-sized refactor, not a rewrite. The existing API can remain as a compatibility facade while new contracts are introduced section by section.

## 2. User Outcome

A user opening Settings should be able to answer these questions immediately:

1. What does the application currently use by default?
2. Which local and cloud services are available?
3. If a service is unavailable, what is missing and what should I do next?
4. Where are files stored and which files are safe to clean?
5. Are there unsaved changes, and did each change save successfully?

The page should not require the user to understand Token Plan, Resource ID, AK/SK, OpenAI Compatible, environment variables, or storage internals before completing common tasks.

## 3. Audit Baseline

### 3.1 Frontend

`frontend/src/routes/settings/+page.svelte` currently contains:

- settings, engine, voice, storage, credential, and LLM state;
- initial loading and all save orchestration;
- four credential write paths;
- LLM profile CRUD, model discovery, and connection testing;
- storage refresh, Finder opening, and cleanup confirmation;
- all page markup;
- roughly 560 lines of page-specific CSS.

The page is 1321 lines in the audited worktree. It loads settings, engines, up to 2000 voices, and a full storage scan in one `Promise.all`. Any failure can prevent the main settings object from rendering.

The page uses global `.panel`, `.field`, `.badge`, and `.btn` styles for some sections, while the LLM workspace defines a second local visual language with hard-coded colors, spacing, status surfaces, and navigation behavior. Existing shared `Field`, `Select`, and `Toggle` components are not used by this page.

### 3.2 Backend

`backend/app/services/settings_store.py` currently owns:

- regular settings load and persistence;
- MiMo, Doubao, Volcengine, and LLM secrets;
- LLM profile persistence;
- directory creation;
- model directory discovery;
- storage scanning and flow descriptions;
- cleanup allowlists and deletion;
- opening local folders.

The file is 616 lines in the audited worktree. These responsibilities have different security, failure, performance, and transaction requirements and should not share one store module.

### 3.3 Live Runtime Snapshot

The local runtime was reachable during the audit:

- frontend: `127.0.0.1:5173`;
- backend: `127.0.0.1:8000`;
- settings API returned 28 public fields without secret values;
- storage API returned 19 locations, 8 artifact flows, and 3 cleanup targets;
- settings database permissions were `600`, and its containing directory permissions were `700`.

The current implementation passed the audited settings/provider test selection, `svelte-check`, and a production frontend build. The issue is therefore not that the current page is unusable; it is that its contracts and boundaries are unsafe to extend.

### 3.4 Visual Evidence Limit

The code, live HTTP endpoints, API behavior, and build were inspected. A current-run screenshot audit was not completed because the dedicated in-app browser runtime failed during initialization. No screenshot-derived claim is made in this RFC. Visual implementation must begin with a fresh capture at desktop and narrow widths.

## 4. Confirmed Contract Defects

### P0-A: PATCH silently resets omitted fields

`PATCH /api/settings` accepts `AppSettings`, whose fields all have defaults, and `settings_store.update()` writes the resulting complete model. A partial request such as:

```json
{"theme": "dark"}
```

resets omitted values such as `default_engine_id` and `default_language` to server defaults. The current frontend avoids this only because it happens to submit the full response object.

Required correction:

- introduce `AppSettingsPatch` with optional fields;
- update only `model_fields_set`;
- reject unknown fields;
- save all changed fields in one database transaction;
- add a revision check before old clients can overwrite newer settings.

### P0-B: validation errors can become HTTP 500

An invalid `doubao_base_url` produces a Pydantic validation error containing a `ValueError` in its context. The global handler sends `exc.errors()` directly to `JSONResponse`, which is not serializable in this case. The result is `500 Internal Server Error` instead of a stable field error.

Required correction:

- serialize validation errors through `jsonable_encoder` or a safe error mapper;
- return field paths and user-readable messages;
- add contract tests for URL, enum, range, and path validation failures.

### P1-A: non-atomic multi-request save

The page saves regular settings, MiMo secret, Doubao secret, and Volcengine credentials in sequence. Earlier writes remain committed if a later request fails, while the page exposes only a generic saved state.

Required correction:

- common preferences save independently from provider credentials;
- each section owns its busy, success, and error state;
- a save result states exactly which section changed;
- destructive credential clearing is a separate confirmed action;
- the UI never presents several independent writes as one atomic success.

### P1-B: configuration claims exceed runtime behavior

The settings model contains values that are either not consumed or only partially consumed:

| Field | Current UI | Confirmed runtime behavior | Target decision |
|---|---|---|---|
| `default_engine_id` | shown | used by Generate initialization | keep |
| `default_voice_id` | shown | used by Generate initialization | keep, validate compatibility |
| `default_language` | shown | used by Generate initialization | keep |
| `default_output_format` | shown | Generate store still initializes to WAV | wire or hide until wired |
| `device` | shown | affects Qwen aligner; TTS runners still have their own defaults | redefine as runtime policy or move to advanced |
| `cloud_enabled` | shown | enforced by cloud request builders | keep, remove secret-write side effect |
| `default_emotion` | hidden | no confirmed application-level consumer | remove or wire explicitly |
| `default_emo_alpha` | hidden | Generate store has its own `0.6` default | remove or wire explicitly |
| `theme` | hidden | no confirmed interface consumer | implement as interface preference or remove |
| LLM default profile | shown in LLM panel | no production workflow consumes it | label experimental or connect a real use case |

The LLM panel currently says it supports subtitle correction, semantic segmentation, and localization. The backend planner still reports `llm_available=False`. Until one real workflow consumes the selected profile, the UI must describe it as connection configuration rather than an active AI capability.

### P1-C: misleading data-root semantics

`data_dir` is editable and described as the root for the database and subdirectories, but the database path is selected when `database.py` is imported from `VOICE_STUDIO_DB_PATH` or `VOICE_STUDIO_DATA_DIR`. Updating `data_dir` does not move or reopen the database. Independently persisted child directories also do not follow a later `data_dir` change.

Required correction:

- ordinary settings must not present `data_dir` as a live migration control;
- show resolved database and directory paths as current state;
- if data-root migration is supported, implement it as a dedicated wizard with preflight, copy, verification, restart, and rollback;
- otherwise make the data root read-only and document the startup environment override.

### P1-D: provider configuration lacks source and health

Credential flags merge database values and environment variables into a Boolean. The frontend cannot explain why a cleared local key still appears configured, whether a key was ever tested, or whether it has the required permission.

Required response shape:

```ts
type CredentialStatus = {
  configured: boolean;
  source: 'database' | 'environment' | 'keychain' | 'none';
  validation: 'unknown' | 'valid' | 'invalid';
  last_checked_at: string | null;
  message: string | null;
};
```

No secret value is returned.

### P1-E: provider URL safety

Doubao restricts its Base URL to the official HTTPS host. MiMo does not have equivalent validation, while its client attaches the API key to requests derived from the saved Base URL.

Required correction:

- official managed providers use an allowlisted host and a non-editable default endpoint;
- custom endpoints exist only for provider types that intentionally support them;
- any advanced endpoint override requires an explicit trust warning and server-side validation;
- credentials cannot be submitted together with a clear instruction.

## 5. Settings Field Ownership

Every setting must have one owner and one documented consumer. A field without a consumer is not a setting; it is unfinished product work.

| Domain | Owns | Does not own |
|---|---|---|
| Preferences | default engine, compatible voice, language, output format | provider credentials, storage cleanup |
| Runtime | compute policy and restart-required runtime choices | per-generation creative parameters |
| Providers | enabled state, public endpoint metadata, provider defaults, credential status | engine execution queues |
| LLM connections | profile name, endpoint, model, enabled/default state, secret status | subtitle or localization business rules |
| Storage | resolved paths, capacity, cleanup capability, migration state | generic application preferences |
| Interface | theme, density, reduced motion preference | backend runtime settings |

Engine-specific creative parameters such as emotion strength should normally remain with the generation engine or preset, not become global application settings unless the product has a clear cross-engine meaning.

## 6. Target Backend Architecture

The target fits the existing modular-monolith roadmap:

```text
backend/app/
  api/
    preferences.py
    provider_settings.py
    storage_settings.py
    runtime_settings.py
  core/
    config/
      models.py
      resolver.py
      repository.py
      secrets.py
      service.py
  providers/
    registry.py
    mimo.py
    doubao.py
    llm.py
  services/
    settings_store.py        # temporary compatibility facade
```

### 6.1 Config models

Use separate request and response models:

```text
PreferencesSnapshot / PreferencesPatch
RuntimeSnapshot / RuntimePatch
ProviderSnapshot / ProviderPatch
CredentialWrite / CredentialStatus
StorageSnapshot / StorageAction
SettingsOverview
```

Responses may include effective values and safe metadata. Request models never include response-only `configured` fields.

### 6.2 ConfigResolver

The resolver has an explicit precedence policy per field:

```text
startup-only environment override
        ↓
database or Keychain value
        ↓
schema default
        ↓
effective value + source + editable + restart_required
```

Precedence must not be inferred independently by each consumer. The resolver returns metadata so the UI can explain non-editable environment overrides.

### 6.3 SettingsRepository

Responsibilities:

- read and write public configuration records;
- update a set of fields in one SQLite transaction;
- maintain a monotonically increasing revision;
- reject updates based on a stale revision;
- preserve unknown future records without loading them into older public models;
- expose no provider or filesystem behavior.

### 6.4 SecretStore

Responsibilities:

- write, replace, read internally, and delete secrets;
- never return secret values through API models;
- report source and configured state;
- support a macOS Keychain implementation with a SQLite compatibility reader;
- provide a reversible migration rather than deleting old secret rows immediately.

The existing `600/700` database permissions remain a defense-in-depth requirement even after Keychain support.

### 6.5 Provider registry reuse

Do not build a separate settings-only provider catalog. Extend the existing engine/provider facts with UI-safe configuration metadata:

- display name and description;
- local/cloud category;
- capabilities;
- configuration state;
- credential requirements;
- connection test support;
- official credential links;
- safe advanced fields;
- whether a change requires restart.

Provider adapters own endpoint validation, health checks, and public error mapping. Task queues continue to own orchestration.

### 6.6 Storage service

Move storage audit, opening, and cleanup out of the settings repository. Storage actions require their own allowlist and typed risk level.

The response should distinguish:

- persistent user data;
- generated outputs;
- reproducible cache;
- logs;
- source material required for future editing;
- model weights.

Cleanup descriptions and available cleanup actions must come from the same backend capability record so dead frontend branches cannot drift from the allowlist.

## 7. Target API Contracts

Recommended endpoints:

```text
GET   /api/settings/overview
GET   /api/settings/preferences
PATCH /api/settings/preferences
GET   /api/settings/runtime
PATCH /api/settings/runtime

GET   /api/providers
GET   /api/providers/{provider_id}
PATCH /api/providers/{provider_id}
PUT   /api/providers/{provider_id}/credential
DELETE /api/providers/{provider_id}/credential
POST  /api/providers/{provider_id}/test

GET   /api/llm-profiles
POST  /api/llm-profiles/test-draft
PUT   /api/llm-profiles/{profile_id}
DELETE /api/llm-profiles/{profile_id}

GET   /api/storage/overview
POST  /api/storage/open
POST  /api/storage/cleanup
POST  /api/storage/migrations/preflight
POST  /api/storage/migrations
```

`POST /api/llm-profiles/test-draft` tests unsaved connection data without silently persisting it. The existing settings URLs remain facades until all consumers move.

Example save response:

```json
{
  "revision": 18,
  "changed": ["default_language", "default_output_format"],
  "restart_required": [],
  "preferences": {
    "default_language": "zh",
    "default_output_format": "mp3"
  }
}
```

## 8. Target Frontend Architecture

```text
frontend/src/routes/settings/
  +page.svelte
  settings-controller.svelte.ts
  settings.types.ts
  components/
    SettingsSectionNav.svelte
    SettingsOverview.svelte
    SettingsSaveBar.svelte
    GeneralSettingsSection.svelte
    ProviderConnectionsSection.svelte
    ProviderStatusCard.svelte
    ProviderEditor.svelte
    SecretField.svelte
    LlmProfilesSection.svelte
    LlmProviderList.svelte
    LlmProviderEditor.svelte
    StorageSection.svelte
    StorageLocationCard.svelte
    AdvancedSettingsSection.svelte
    ConfirmActionDialog.svelte

frontend/src/lib/api/settings/
  client.ts
  types.ts
```

### 8.1 Page responsibility

`+page.svelte` owns:

- route-level composition;
- section navigation;
- responsive layout slots;
- no persistence rules;
- no provider-specific branching.

### 8.2 Controller responsibility

The controller owns independent section state:

```text
preferences: data / draft / dirty / loading / saving / error
providers: summary list and per-provider editor state
llm: profiles / selected draft / dirty / testing / error
storage: data / refreshing / action state / error
```

Switching sections does not discard drafts. Switching LLM profiles with unsaved changes requires save, discard, or cancel.

### 8.3 API boundary

Do not continue adding settings methods to the global `Api` object and types to the monolithic `types.ts`. A settings client owns settings contracts. Core types should be generated from OpenAPI or checked automatically against it.

## 9. Information Architecture

Recommended desktop structure:

```text
Settings                                      [2 unsaved] [Save changes]

┌──────────────────┬────────────────────────────────────────────────┐
│ Overview         │ Current defaults                              │
│ General          │ Engine      IndexTTS v2                       │
│ Cloud services   │ Voice       Fox                               │
│ AI assistant     │ Language    Chinese                           │
│ Files & storage  │ Output      WAV                               │
│ Advanced         │                                                │
│                  │ Service status                                │
│                  │ Local engines  Ready                          │
│                  │ MiMo           Missing credential [Set up]    │
│                  │ Doubao         Available          [Manage]     │
│                  │ AI assistant    Connected          [Manage]     │
└──────────────────┴────────────────────────────────────────────────┘
```

At narrow widths, the section navigation becomes a horizontally scrollable tab row or compact selector. The save bar remains visible without covering fields.

### 9.1 Overview

The overview is read-mostly. It shows:

- current defaults;
- local/cloud/AI service status;
- storage usage summary;
- warnings requiring action;
- direct links to the correct section.

It does not duplicate every form field.

### 9.2 General

Common settings only:

- default engine;
- compatible default voice;
- default language;
- default output format if wired end to end.

Each label includes a short outcome-oriented sentence. Technical implementation details remain hidden.

### 9.3 Cloud services

MiMo and Doubao appear as provider cards with four consistent states:

```text
off
setup_required
available
error
```

The card answers:

- what the service enables;
- whether sending data to the cloud is involved;
- what is missing;
- the next primary action.

Advanced endpoint and resource fields appear only inside the provider editor. The Doubao voice API key and Volcengine AK/SK remain separate credential groups with separate purposes and official acquisition links.

### 9.4 AI assistant

The LLM section uses a master-detail layout only after the user enters the section. Overview cards should not expose provider-management complexity.

Connection testing uses the current draft without saving. Successful testing does not imply that subtitle or localization features are wired; capability state is reported separately.

### 9.5 Files and storage

Storage is separated from preferences because it has slower loading, destructive actions, and different error handling.

The default presentation groups locations by user meaning rather than backend category strings:

- My data;
- Generated files;
- Cache and logs;
- Models.

Paths are secondary text with copy/open actions. Cleanup appears once per eligible location, not again in a duplicate page footer.

### 9.6 Advanced

Advanced settings include:

- resolved paths;
- startup-controlled values;
- endpoint overrides only where supported;
- resource IDs;
- restart-required runtime options;
- diagnostics and schema version.

Advanced controls include stronger validation and plain warnings about consequences.

## 10. Interaction Contract

### Loading

- Sections load independently.
- Overview can render partial status.
- Storage failure does not block preferences.
- Every failed section provides retry in place.

### Editing

- Dirty state is computed per section.
- Save buttons activate only when a valid change exists.
- Navigation preserves drafts.
- Leaving the page with dirty drafts prompts once.

### Saving

- The save control shows idle, dirty, saving, saved, and error states.
- Success feedback appears next to the initiating control and in the sticky save bar.
- Field errors focus the first invalid control.
- Partial provider failures cannot be summarized as global success.

### Secrets

- Secret input never receives an existing value.
- Configured source is shown without exposing the value.
- Replacing and deleting are separate actions.
- Delete requires explicit confirmation.
- An environment-controlled credential explains that it cannot be removed in the page.

### Destructive storage actions

- Confirmation text names what will be removed and what future capability will be lost.
- Backend capability determines risk text.
- Completion reports files and bytes removed.
- Persistent data and model weights have no cleanup action in this surface.

## 11. Visual System Direction

The page should extend the current dark Voice Studio language while making it calmer, more legible, and more consistent.

### 11.1 Semantic tokens

Add semantic tokens before restyling components:

```css
--surface-page
--surface-panel
--surface-panel-raised
--surface-input
--border-subtle
--border-strong
--text-primary
--text-secondary
--text-tertiary
--action-primary
--status-success
--status-warning
--status-danger
--focus-ring
--space-1 ... --space-8
--radius-sm / --radius-md / --radius-lg
--type-caption / --type-body / --type-title
```

Page components must not introduce raw status colors unless they are promoted into semantic tokens.

### 11.2 Component vocabulary

Use one implementation for:

- section header;
- setting row;
- text input and select;
- toggle;
- helper text and field error;
- provider status card;
- status badge;
- primary, secondary, and danger buttons;
- inline notice;
- confirmation dialog;
- sticky save bar.

The same vocabulary should later be reused by Engine Hub, Voice Library, and Generate rather than copying settings-specific CSS.

### 11.3 Hierarchy rules

- One primary action per card or section.
- Status precedes configuration details.
- Explanations state effect, not implementation.
- Technical identifiers use monospace only when the identifier itself matters.
- Destructive controls are visually quiet until the user enters the destructive flow.
- Help drawers supplement the page; they do not carry the primary explanation burden.

### 11.4 Accessibility requirements

- Visible focus on every interactive control.
- Minimum 40px primary touch target at narrow widths.
- Provider selection uses correct listbox keyboard behavior or ordinary navigation buttons, not incomplete ARIA semantics.
- Status changes use `role=status`; blocking errors use `role=alert`.
- Color is never the only state indicator.
- Reduced-motion preference disables nonessential progress animation.
- Layout remains usable at 200% zoom and widths from 390px through 1440px.

## 12. Migration Plan

### Phase 0: protect current behavior

- Add isolated tests for partial PATCH behavior and validation error serialization.
- Add frontend controller characterization tests for current credential and LLM flows.
- Record the current OpenAPI settings schemas.
- Capture current settings screenshots at desktop and narrow widths before UI changes.

Exit criteria:

- unsafe PATCH and validation behavior are reproducible in tests;
- no real user database is used by tests;
- current visual states are captured and inspected.

### Phase 1: repair contracts

- Add safe validation error serialization.
- Add `AppSettingsPatch` and transactional multi-field update.
- Separate public response fields from update fields.
- Reject secret value plus clear instruction.
- remove implicit `cloud_enabled=true` from secret writes.
- Add revision metadata.

Exit criteria:

- one-field update preserves every omitted field;
- all validation failures return stable JSON;
- concurrent stale update is rejected;
- existing full-object clients remain compatible.

### Phase 2: establish configuration domains

- Introduce ConfigResolver and SettingsRepository.
- Move secret operations behind SecretStore.
- Move storage audit/actions to a storage service.
- Add provider status/source metadata.
- Keep `settings_store.py` as a facade.

Exit criteria:

- every setting has one documented source and consumer;
- writes are transactional inside a domain;
- no public response exposes a secret;
- environment overrides are visible and explainable.

### Phase 3: make claims truthful

- Wire default output format or remove it from common settings.
- decide the runtime meaning of `device`.
- implement or remove theme and global emotion fields.
- connect one real LLM workflow or label LLM connections as not yet active.
- validate engine/voice compatibility.

Exit criteria:

- every visible setting has an end-to-end consumer test;
- the UI copy matches current capability state;
- incompatible defaults cannot be saved.

### Phase 4: split frontend behavior

- Introduce the settings client and controller.
- Extract section components without redesigning them first.
- Add independent loading, dirty, saving, and error states.
- Replace silent LLM save-and-test with draft test.

Exit criteria:

- route file performs composition only;
- section behavior is covered by Vitest;
- storage/provider failures do not block common settings;
- no draft is silently discarded.

### Phase 5: visual redesign

- Capture the existing page and related product screens.
- Produce exactly three visual directions based on the selected information architecture.
- Select one direction before production styling.
- Implement semantic tokens and shared components.
- Rebuild overview and sections.

Exit criteria:

- selected target and implementation are compared at matching viewport/state;
- visual language matches Engine Hub, Voice Library, and Generate;
- no page-specific duplicate control system remains.

### Phase 6: runtime and accessibility verification

- Test desktop, tablet, and 390px layouts.
- Test keyboard navigation, focus, validation, save, retry, and destructive actions.
- Verify saved values after reload and in their real consumer workflows.
- Verify provider states against the live local API without exposing secrets.

Exit criteria:

- requirements in Section 13 are all evidenced;
- compatibility facade can be scheduled for later removal;
- no unresolved P0/P1 issue remains in the settings scope.

## 13. Acceptance Criteria

### Contract

- Partial settings updates do not alter omitted fields.
- Invalid input returns stable field-level JSON, never a generic 500.
- Section writes are atomic and revision-aware.
- Secrets never appear in API responses, logs, screenshots, or frontend state snapshots.
- Credential source and validation state are explainable.

### Product truth

- Every visible setting has a confirmed runtime consumer.
- LLM wording states exactly which features currently use it.
- Data-root wording matches real database and child-directory behavior.
- Provider endpoint fields cannot silently redirect official credentials.

### Usability

- A first-time user can identify default behavior and service readiness from Overview.
- Common settings require no advanced terminology.
- Every unavailable provider gives one clear next action.
- Saving, testing, clearing, and cleanup have distinct meanings.
- Help content is optional, not required to understand the primary workflow.

### Visual consistency

- Settings, Engine Hub, Voice Library, and Generate share semantic tokens and core controls.
- Cards, fields, badges, notices, and buttons use one hierarchy.
- Status is communicated by text/icon and not color alone.
- 390, 760, 900, 1280, and 1440px layouts have no horizontal overflow.

### Verification

- Backend contract and provider tests pass.
- Frontend controller and component tests pass.
- `svelte-check` and production build pass.
- Current and redesigned screenshots are captured and inspected.
- Live local save/reload and real consumer behavior are verified using non-secret test values.

## 14. Product Decisions Required

### Decision A: data root

Recommended: make the current data root read-only in normal settings and design a separate verified migration wizard later.

Alternatives:

- read-only startup configuration only;
- new-files-only behavior with explicit warning;
- full migration wizard now.

### Decision B: LLM scope

Recommended: keep connection management, but do not claim active subtitle/localization capability until at least one real workflow consumes the default profile.

Alternatives:

- hide the section temporarily;
- label it experimental;
- include one end-to-end LLM workflow in this refactor.

### Decision C: network exposure

Recommended: supported local launch paths bind to loopback only. Any LAN mode requires authentication, CSRF protection, secret hardening, and a separate security review.

### Decision D: direct browser capture

The dedicated in-app browser runtime failed during this audit. Direct Playwright capture should be used only after user approval, as required by the browser tooling policy.

## 15. Recommended First Implementation Batch

Do not begin with layout code. The first batch should contain only:

1. safe request-validation serialization;
2. `AppSettingsPatch` and transactional updates;
3. tests for partial PATCH, invalid input, secret conflict, and cloud-enabled precedence;
4. no visual changes;
5. no real data migration;
6. no removal of the current LLM work.

After that batch is verified, capture the current page and generate three visual targets before implementing the new layout.
