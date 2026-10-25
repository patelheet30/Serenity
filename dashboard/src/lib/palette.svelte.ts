import { untrack } from 'svelte';
import { score } from './fuzzy';
import type { IconName } from './icons';

export type Command = {
	id: string;
	title: string;
	/** Heading the command is listed under, e.g. "Navigate" or "Servers". */
	group: string;
	icon?: IconName;
	keywords?: string;
	/** Global key sequence, e.g. "g o". Listed in the ? sheet and run from anywhere. */
	shortcut?: string;
	/** What Tab completes the query to, e.g. "set #memes threshold ". */
	template?: string;
	/** Free-form arguments: return what running the typed query would do, or null. */
	parse?: (query: string) => { label: string; run: () => void } | null;
	available?: () => boolean;
	run?: () => void;
};

export type Item = {
	group: string;
	label: string;
	icon: IconName;
	hint?: string;
	fill?: string;
	run?: () => void;
	parsed?: boolean;
};

let commands = $state.raw<Command[]>([]);
export const palette = $state({ open: false, query: '', keys: false });

/**
 * Add commands while the caller is mounted. Use inside $effect so they are removed on destroy:
 * `$effect(() => registerCommands([...]))`.
 */
export function registerCommands(list: Command[]): () => void {
	// untrack: the caller's effect must depend on its own inputs, not on the registry it writes.
	untrack(() => (commands = [...commands, ...list]));
	return () => untrack(() => (commands = commands.filter((c) => !list.includes(c))));
}

export const available = () => commands.filter((c) => c.available?.() ?? true);

export function openPalette(query = '') {
	palette.query = query;
	palette.open = true;
}

export function byShortcut(keys: string): Command | undefined {
	return available().find((c) => c.shortcut === keys && c.run);
}

export function search(raw: string): Item[] {
	const q = raw.trim().replace(/\s+/g, ' ');
	const list = available();
	const toItem = (c: Command): Item => ({
		group: c.group,
		label: c.title,
		icon: c.icon ?? 'arrow',
		hint: c.shortcut ?? (c.template && !c.run ? 'tab' : undefined),
		fill: c.run ? undefined : c.template,
		run: c.run
	});

	const parsed: Item[] = [];
	if (q) {
		for (const c of list) {
			const p = c.parse?.(q);
			if (p) parsed.push({ group: 'Run', label: p.label, icon: 'run', hint: '↵', run: p.run, parsed: true });
		}
	}

	const ranked = q
		? list
				.map((c) => ({ c, s: score(q, `${c.title} ${c.keywords ?? ''}`) }))
				.filter((x) => x.s > 0)
				.sort((a, b) => b.s - a.s)
				.slice(0, 30)
				.map((x) => toItem(x.c))
		: list.map(toItem);

	// Keep groups together in first-seen order, so headings appear once.
	const order = [...new Set(ranked.map((i) => i.group))];
	return parsed.concat(ranked.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group)));
}
