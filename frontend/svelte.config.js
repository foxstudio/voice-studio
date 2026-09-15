import adapter from '@sveltejs/adapter-static';

const validationOutDir = process.env.VOICE_STUDIO_SVELTE_OUT_DIR;
const validationDistDir = process.env.VOICE_STUDIO_FRONTEND_DIST;

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		...(validationOutDir ? { outDir: validationOutDir } : {}),
		adapter: adapter({
			...(validationDistDir ? { pages: validationDistDir, assets: validationDistDir } : {}),
			fallback: 'index.html'
		})
	}
};

export default config;
