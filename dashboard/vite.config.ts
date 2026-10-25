import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA: the landing page is prerendered, everything else falls back to 200.html (see Caddyfile).
			adapter: adapter({ fallback: '200.html' }),
			// Only the landing page is prerendered; don't crawl into the API it links to.
			prerender: { entries: ['/'], crawl: false }
		})
	],
	server: {
		// The API runs inside the bot (uv run main.py) on METRICS_PORT.
		proxy: { '/api': 'http://localhost:8080' }
	}
});
