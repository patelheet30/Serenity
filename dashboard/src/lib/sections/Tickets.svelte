<script module lang="ts">
	import type { Component } from 'svelte';
	import type { Channel } from '../api';

	/** What every page in tickets/ is given. Ticket staff get no channels. */
	export type TicketPageProps = { guildId: string; channels: Channel[]; staff: boolean; onerror: (e: unknown) => void };
</script>

<script lang="ts">
	import EmptyState from '../components/EmptyState.svelte';

	let {
		guildId,
		sub,
		channels,
		staff,
		onerror
	}: { guildId: string; sub: string | undefined } & TicketPageProps = $props();

	// A page is tickets/<name>.svelte: add the file and it replaces its placeholder here.
	const FILES = import.meta.glob<{ default: Component<TicketPageProps> }>('./tickets/*.svelte', { eager: true });
	const PAGES = [
		{ k: 'queue', name: 'Queue', staff: true },
		{ k: 'board', name: 'Board', staff: true },
		{ k: 'transcripts', name: 'Transcripts', staff: true },
		{ k: 'panels', name: 'Panels', staff: false },
		{ k: 'types', name: 'Types', staff: false },
		{ k: 'settings', name: 'Settings', staff: false },
		{ k: 'webhooks', name: 'Webhooks', staff: false },
		{ k: 'stats', name: 'Stats', staff: true }
	];

	const pages = $derived(PAGES.filter((p) => p.staff || !staff));
	const current = $derived(pages.find((p) => p.k === sub) ?? pages[0]);
	const Page = $derived(FILES[`./tickets/${current.name}.svelte`]?.default);
</script>

<div class="phead">
	<div>
		<h1>Tickets</h1>
		<p>{staff ? 'You’re ticket staff here, so other sections are hidden: they need Manage Server.' : 'Tickets, panels and how they work.'}</p>
	</div>
	<nav class="seg" aria-label="Tickets pages">
		{#each pages as p (p.k)}
			<a href="/dashboard/{guildId}/tickets/{p.k}" aria-current={p === current ? 'page' : undefined}>{p.name}</a>
		{/each}
	</nav>
</div>

{#if Page}
	<Page {guildId} {channels} {staff} {onerror} />
{:else}
	<section class="pane"><EmptyState>{current.name} is coming soon.</EmptyState></section>
{/if}
