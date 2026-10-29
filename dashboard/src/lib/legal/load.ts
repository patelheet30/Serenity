import { marked } from 'marked';

const SOURCES = import.meta.glob<string>('./*.md', { query: '?raw', import: 'default', eager: true });

/** A legal page's title and HTML from its Markdown here. Only server loads call this, at build
 * time (the pages are prerendered), so marked never reaches the browser. */
export function legal(doc: 'privacy' | 'terms' | 'cookies') {
	const md = SOURCES[`./${doc}.md`];
	return { title: md.match(/^# (.+)$/m)![1], html: marked.parse(md, { async: false }) };
}
