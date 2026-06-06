# Issues Log

## Frontend Hardcoded v1/v2 Locations (T7 discovered, T10 will handle)

### `frontend/src/lib/api/types.ts:58`
```typescript
export type EngineVersionValue = 'v1' | 'v2';
```
→ 需要扩展为 `'indextts-v1' | 'indextts' | 'omnivoice'`

### `frontend/src/routes/generate/+page.svelte`
- L9: `let engineVersion = $state('v2');`
- L45: `let isV2 = $derived(engineVersion === 'v2');`
- L234: hardcoded v1 button
- L235: hardcoded v2 button

### `frontend/src/routes/engine-hub/+page.svelte`
- L8: `let activeTab = $state<'all' | 'v1' | 'v2'>('all');`
- L107, 113: filtering tabs use 'v1' | 'v2'

### `frontend/src/routes/settings/+page.svelte`
- L79: `<option value="v1">IndexTTS v1</option>`
- L80: `<option value="v2">IndexTTS v2</option>`

### Not hardcoded (no changes needed):
- `frontend/src/lib/api/engines.ts` — 无 v1/v2 硬编码