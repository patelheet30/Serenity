import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => ({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA: the landing and legal pages are prerendered, everything else falls back to 200.html (see Caddyfile).
			adapter: adapter({ fallback: '200.html' }),
			// Only the landing and legal pages are prerendered; don't crawl into the API they link to.
			prerender: { entries: ['/', '/privacy', '/terms', '/cookies'], crawl: false }
		})
	],
	server: {
		// The API runs inside the bot (uv run main.py) on METRICS_PORT, read from the shell or the
		// repo's .env like the bot does.
		proxy: { '/api': `http://localhost:${loadEnv(mode, '..', '').METRICS_PORT || 8080}` }
	}
}));
