import { marked } from 'marked';

const SOURCES = import.meta.glob<string>('./*.md', { query: '?raw', import: 'default', eager: true });

/** A legal page's title and HTML from its Markdown here. Only server loads call this, at build
 * time (the pages are prerendered), so marked never reaches the browser. */
export function legal(doc: 'privacy' | 'terms' | 'cookies') {
	const md = SOURCES[`./${doc}.md`];
	const title = md.match(/^# (.+)$/m)?.[1] ?? doc;
	// The link-preview text: the Overview's first sentence, without Markdown (the title if a doc
	// has no Overview, so a renamed heading can't break the build).
	const overview = (md.split('## Overview')[1] ?? '').trim().split('\n\n')[0];
	const sentence = overview.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*`]/g, '').split(/(?<=\.) /)[0];
	return { title, description: sentence || title, html: marked.parse(md, { async: false }), path: `/${doc}` };
}
