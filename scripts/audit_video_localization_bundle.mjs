#!/usr/bin/env node

import { readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

export const SCHEMA_VERSION = 'video-localization-bundle-budget-v1';
export const DEFAULT_BUDGETS = Object.freeze({
	clientRouteRawBytes: 470_000,
	clientRouteGzipBytes: 135_000,
	staticClosureRawBytes: 750_000,
	staticClosureGzipBytes: 225_000,
	// Includes the editor's collection merge/acknowledgement state machine.
	// Browser route and static-transfer budgets remain unchanged.
	serverRouteRawBytes: 700_000
});

const ROUTE_SOURCE = 'src/routes/video-localization/+page.svelte';

function readJson(path) {
	return JSON.parse(readFileSync(path, 'utf8'));
}

function fileMetrics(path) {
	const contents = readFileSync(path);
	return {
		rawBytes: contents.byteLength,
		gzipBytes: gzipSync(contents).byteLength
	};
}

export function collectManifestClosure(manifest, rootKey, excludedKeys = new Set()) {
	if (!manifest[rootKey]) throw new Error(`Bundle manifest is missing entry: ${rootKey}`);
	const visited = new Set();
	const visit = (key) => {
		if (visited.has(key) || excludedKeys.has(key)) return;
		const entry = manifest[key];
		if (!entry) throw new Error(`Bundle manifest import is missing entry: ${key}`);
		visited.add(key);
		for (const dependency of entry.imports ?? []) visit(dependency);
	};
	visit(rootKey);
	return visited;
}

export function manifestFiles(manifest, entryKeys) {
	const files = new Set();
	for (const key of entryKeys) {
		const entry = manifest[key];
		if (entry?.file) files.add(entry.file);
		for (const css of entry?.css ?? []) files.add(css);
		for (const asset of entry?.assets ?? []) files.add(asset);
	}
	return files;
}

export function measureRelativeFiles(root, files) {
	let rawBytes = 0;
	let gzipBytes = 0;
	for (const file of files) {
		const metrics = fileMetrics(join(root, file));
		rawBytes += metrics.rawBytes;
		gzipBytes += metrics.gzipBytes;
	}
	return { rawBytes, gzipBytes, fileCount: files.size };
}

function findClientRouteKey(frontendRoot, manifest) {
	for (const [key, entry] of Object.entries(manifest)) {
		if (!entry.isEntry || !entry.src?.includes('/nodes/')) continue;
		const generatedSource = resolve(frontendRoot, entry.src);
		try {
			if (readFileSync(generatedSource, 'utf8').includes(ROUTE_SOURCE)) return key;
		} catch {
			// Keep searching so the final error reports the missing route rather than
			// coupling the audit to one generated node number.
		}
	}
	throw new Error(`Unable to locate the client entry for ${ROUTE_SOURCE}`);
}

function dynamicEntryMetrics(clientRoot, manifest, staticKeys, dynamicKey) {
	const keys = collectManifestClosure(manifest, dynamicKey, staticKeys);
	const files = manifestFiles(manifest, keys);
	return {
		key: dynamicKey,
		file: manifest[dynamicKey].file,
		...measureRelativeFiles(clientRoot, files)
	};
}

function dynamicImportKeys(manifest, staticKeys) {
	const keys = new Set();
	for (const staticKey of staticKeys) {
		for (const dynamicKey of manifest[staticKey]?.dynamicImports ?? []) keys.add(dynamicKey);
	}
	return [...keys].sort();
}

export function analyzeVideoLocalizationBundle(frontendRoot) {
	const clientRoot = join(frontendRoot, '.svelte-kit/output/client');
	const serverRoot = join(frontendRoot, '.svelte-kit/output/server');
	const clientManifest = readJson(join(clientRoot, '.vite/manifest.json'));
	const serverManifest = readJson(join(serverRoot, '.vite/manifest.json'));
	const clientRouteKey = findClientRouteKey(frontendRoot, clientManifest);
	const clientRouteEntry = clientManifest[clientRouteKey];
	const staticKeys = collectManifestClosure(clientManifest, clientRouteKey);
	const staticFiles = manifestFiles(clientManifest, staticKeys);
	const serverRouteEntry = serverManifest[ROUTE_SOURCE];
	if (!serverRouteEntry?.file) {
		throw new Error(`Unable to locate the server entry for ${ROUTE_SOURCE}`);
	}

	return {
		schemaVersion: SCHEMA_VERSION,
		route: '/video-localization',
		clientRoute: {
			key: clientRouteKey,
			file: clientRouteEntry.file,
			...fileMetrics(join(clientRoot, clientRouteEntry.file))
		},
		staticClosure: {
			entryCount: staticKeys.size,
			...measureRelativeFiles(clientRoot, staticFiles)
		},
		dynamicEntries: dynamicImportKeys(clientManifest, staticKeys).map((dynamicKey) =>
			dynamicEntryMetrics(clientRoot, clientManifest, staticKeys, dynamicKey)
		),
		serverRoute: {
			key: ROUTE_SOURCE,
			file: serverRouteEntry.file,
			...fileMetrics(join(serverRoot, serverRouteEntry.file))
		}
	};
}

export function bundleBudgetFailures(metrics, budgets = DEFAULT_BUDGETS) {
	const checks = [
		['client route raw', metrics.clientRoute.rawBytes, budgets.clientRouteRawBytes],
		['client route gzip', metrics.clientRoute.gzipBytes, budgets.clientRouteGzipBytes],
		['static closure raw', metrics.staticClosure.rawBytes, budgets.staticClosureRawBytes],
		['static closure gzip', metrics.staticClosure.gzipBytes, budgets.staticClosureGzipBytes],
		['server route raw', metrics.serverRoute.rawBytes, budgets.serverRouteRawBytes]
	];
	return checks
		.filter(([, actual, limit]) => actual > limit)
		.map(([label, actual, limit]) => ({ label, actual, limit }));
}

function formatBytes(value) {
	return `${(value / 1000).toFixed(2)} kB`;
}

function printReport(metrics, failures) {
	console.log(`Schema: ${metrics.schemaVersion}`);
	console.log(
		`Client route: ${formatBytes(metrics.clientRoute.rawBytes)} raw / ${formatBytes(metrics.clientRoute.gzipBytes)} gzip`
	);
	console.log(
		`Static closure: ${formatBytes(metrics.staticClosure.rawBytes)} raw / ${formatBytes(metrics.staticClosure.gzipBytes)} gzip / ${metrics.staticClosure.fileCount} files`
	);
	console.log(
		`Server route: ${formatBytes(metrics.serverRoute.rawBytes)} raw / ${formatBytes(metrics.serverRoute.gzipBytes)} gzip`
	);
	for (const entry of metrics.dynamicEntries) {
		console.log(
			`Dynamic ${entry.key}: ${formatBytes(entry.rawBytes)} raw / ${formatBytes(entry.gzipBytes)} gzip / ${entry.fileCount} files`
		);
	}
	if (failures.length) {
		for (const failure of failures) {
			console.error(
				`BUDGET EXCEEDED: ${failure.label} ${formatBytes(failure.actual)} > ${formatBytes(failure.limit)}`
			);
		}
	} else {
		console.log('Bundle budget: PASS');
	}
}

function parseArgs(argv) {
	const args = [...argv];
	const rootIndex = args.indexOf('--frontend-root');
	let frontendRoot;
	if (rootIndex !== -1) {
		frontendRoot = args[rootIndex + 1];
		if (!frontendRoot || frontendRoot.startsWith('--')) throw new Error('--frontend-root requires a directory');
		args.splice(rootIndex, 2);
	}
	const flags = new Set(args);
	const unknown = args.filter((arg) => !['--check', '--json'].includes(arg));
	if (unknown.length) throw new Error(`Unknown arguments: ${unknown.join(', ')}`);
	return { check: flags.has('--check'), json: flags.has('--json'), frontendRoot };
}

function main() {
	const options = parseArgs(process.argv.slice(2));
	const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
	const frontendRoot = options.frontendRoot ? resolve(options.frontendRoot) : join(repoRoot, 'frontend');
	const metrics = analyzeVideoLocalizationBundle(frontendRoot);
	const failures = bundleBudgetFailures(metrics);
	if (options.json) {
		console.log(JSON.stringify({ metrics, budgets: DEFAULT_BUDGETS, failures }, null, 2));
	} else {
		printReport(metrics, failures);
	}
	if (options.check && failures.length) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	try {
		main();
	} catch (error) {
		console.error(`Bundle audit failed: ${error instanceof Error ? error.message : String(error)}`);
		process.exitCode = 2;
	}
}
