import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
	analyzeVideoLocalizationBundle,
	bundleBudgetFailures,
	collectManifestClosure
} from './audit_video_localization_bundle.mjs';

function writeFixture() {
	const root = mkdtempSync(join(tmpdir(), 'vl-bundle-audit-'));
	const clientRoot = join(root, '.svelte-kit/output/client');
	const serverRoot = join(root, '.svelte-kit/output/server');
	const generatedRoot = join(root, '.svelte-kit/generated/client-optimized/nodes');
	mkdirSync(join(clientRoot, '.vite'), { recursive: true });
	mkdirSync(join(serverRoot, '.vite'), { recursive: true });
	mkdirSync(generatedRoot, { recursive: true });
	mkdirSync(join(clientRoot, 'assets'), { recursive: true });
	mkdirSync(join(serverRoot, 'entries'), { recursive: true });
	writeFileSync(
		join(generatedRoot, '4.js'),
		'export { default as component } from "../../../../src/routes/video-localization/+page.svelte";'
	);
	writeFileSync(join(clientRoot, 'route.js'), 'r'.repeat(10));
	writeFileSync(join(clientRoot, 'shared.js'), 's'.repeat(5));
	writeFileSync(join(clientRoot, 'lazy.js'), 'l'.repeat(7));
	writeFileSync(join(clientRoot, 'nested-lazy.js'), 'n'.repeat(6));
	writeFileSync(join(clientRoot, 'assets/route.css'), 'c'.repeat(3));
	writeFileSync(join(clientRoot, 'assets/lazy.css'), 'd'.repeat(4));
	writeFileSync(join(serverRoot, 'entries/route.js'), 'x'.repeat(11));

	const clientManifest = {
		'.svelte-kit/generated/client-optimized/nodes/4.js': {
			file: 'route.js',
			src: '.svelte-kit/generated/client-optimized/nodes/4.js',
			isEntry: true,
			imports: ['_shared'],
			dynamicImports: ['src/routes/video-localization/Lazy.svelte'],
			css: ['assets/route.css']
		},
		_shared: {
			file: 'shared.js',
			dynamicImports: ['src/routes/video-localization/NestedLazy.svelte']
		},
		'src/routes/video-localization/Lazy.svelte': {
			file: 'lazy.js',
			imports: ['_shared'],
			css: ['assets/lazy.css']
		},
		'src/routes/video-localization/NestedLazy.svelte': {
			file: 'nested-lazy.js'
		}
	};
	const serverManifest = {
		'src/routes/video-localization/+page.svelte': { file: 'entries/route.js' }
	};
	writeFileSync(join(clientRoot, '.vite/manifest.json'), JSON.stringify(clientManifest));
	writeFileSync(join(serverRoot, '.vite/manifest.json'), JSON.stringify(serverManifest));
	return root;
}

test('CLI audits the selected isolated build instead of the developer checkout', () => {
	const root = writeFixture();
	try {
		const result = spawnSync(process.execPath, [
			fileURLToPath(new URL('./audit_video_localization_bundle.mjs', import.meta.url)),
			'--frontend-root', root, '--json', '--check'
		], { encoding: 'utf8' });
		assert.equal(result.status, 0, result.stderr);
		assert.equal(JSON.parse(result.stdout).metrics.clientRoute.rawBytes, 10);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test('collectManifestClosure de-duplicates imports and honors exclusions', () => {
	const manifest = {
		root: { imports: ['shared', 'branch'] },
		branch: { imports: ['shared'] },
		shared: {}
	};
	assert.deepEqual([...collectManifestClosure(manifest, 'root')].sort(), ['branch', 'root', 'shared']);
	assert.deepEqual([...collectManifestClosure(manifest, 'root', new Set(['shared']))].sort(), ['branch', 'root']);
});

test('analyzeVideoLocalizationBundle separates static and on-demand files', () => {
	const root = writeFixture();
	try {
		const metrics = analyzeVideoLocalizationBundle(root);
		assert.equal(metrics.clientRoute.rawBytes, 10);
		assert.equal(metrics.staticClosure.rawBytes, 18);
		assert.equal(metrics.staticClosure.fileCount, 3);
		assert.equal(metrics.dynamicEntries.length, 2);
		assert.equal(
			metrics.dynamicEntries[0].key,
			'src/routes/video-localization/Lazy.svelte'
		);
		assert.equal(metrics.dynamicEntries[0].rawBytes, 11);
		assert.equal(metrics.dynamicEntries[0].fileCount, 2);
		assert.equal(
			metrics.dynamicEntries[1].key,
			'src/routes/video-localization/NestedLazy.svelte'
		);
		assert.equal(metrics.dynamicEntries[1].rawBytes, 6);
		assert.equal(metrics.dynamicEntries[1].fileCount, 1);
		assert.equal(metrics.serverRoute.rawBytes, 11);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test('bundleBudgetFailures reports every exceeded boundary', () => {
	const metrics = {
		clientRoute: { rawBytes: 10, gzipBytes: 8 },
		staticClosure: { rawBytes: 20, gzipBytes: 12 },
		serverRoute: { rawBytes: 15 }
	};
	const failures = bundleBudgetFailures(metrics, {
		clientRouteRawBytes: 9,
		clientRouteGzipBytes: 8,
		staticClosureRawBytes: 19,
		staticClosureGzipBytes: 12,
		serverRouteRawBytes: 14
	});
	assert.deepEqual(failures.map((failure) => failure.label), [
		'client route raw',
		'static closure raw',
		'server route raw'
	]);
});
