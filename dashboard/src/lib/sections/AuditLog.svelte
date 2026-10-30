<script lang="ts">
	import { api } from '../api';
	import Avatar from '../components/Avatar.svelte';
	import DataTable from '../components/DataTable.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import { toast } from '../toast.svelte';
	import { ago } from '../util';
	import type { CaseUser } from './moderation';

	type Change = { key: string; old: unknown; new: unknown };
	type Entry = {
		id: string;
		action: number;
		action_name: string;
		actor: CaseUser | null;
		target: string | null;
		reason: string | null;
		changes: Change[];
		created_at: number;
	};
	type Page = { enabled: boolean; retention_days: number; entries: Entry[]; next: string | null };

	let { guildId, onerror }: { guildId: string; onerror: (e: unknown) => void } = $props();

	// Discord audit log types, grouped the way people look for them.
	const GROUPS: { n: string; a: number[] }[] = [
		{ n: 'Members', a: [20, 21, 22, 23, 24, 25, 26, 27, 28] },
		{ n: 'Channels', a: [10, 11, 12, 13, 14, 15] },
		{ n: 'Roles', a: [30, 31, 32] },
		{ n: 'Server', a: [1] },
		{ n: 'Invites', a: [40, 41, 42] },
		{ n: 'Webhooks', a: [50, 51, 52] },
		{ n: 'Emojis & stickers', a: [60, 61, 62, 90, 91, 92] },
		{ n: 'Messages', a: [72, 73, 74, 75] },
		{ n: 'Threads', a: [110, 111, 112] },
		{ n: 'AutoMod', a: [140, 141, 142, 143, 144, 145] }
	];

	let q = $state('');
	let search = $state('');
	let groups = $state<string[]>([]);
	let actor = $state<CaseUser | null>(null);
	let target = $state<string | null>(null);
	let page = $state<Page | null>(null);
	let rows = $state<Entry[]>([]);
	let more = $state(false);

	const who = (u: CaseUser) => u.name ?? u.id;
	const filtered = $derived(groups.length > 0 || !!actor || !!target || !!q);
	const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

	$effect(() => {
		const v = q;
		const t = setTimeout(() => (search = v), 250);
		return () => clearTimeout(t);
	});

	function params(before?: string) {
		const p = new URLSearchParams();
		const actions = GROUPS.filter((g) => groups.includes(g.n)).flatMap((g) => g.a);
		if (actions.length) p.set('action', actions.join(','));
		if (actor) p.set('actor', actor.id);
		if (target) p.set('target', target);
		if (search.trim()) p.set('q', search.trim());
		if (before) p.set('before', before);
		return p;
	}

	let generation = 0;
	$effect(() => {
		const id = guildId;
		const p = params();
		generation++;
		let stale = false;
		api<Page>(`/guilds/${id}/audit?${p}`).then(
			(r) => !stale && ((page = r), (rows = r.entries)),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	async function loadMore() {
		if (!page?.next || more) return;
		more = true;
		const gen = generation;
		try {
			const r = await api<Page>(`/guilds/${guildId}/audit?${params(page.next)}`);
			if (gen !== generation || !page) return;
			rows.push(...r.entries);
			page.next = r.next;
		} catch (e) {
			if (gen === generation) toast(e instanceof Error ? e.message : String(e), { error: true });
		} finally {
			more = false;
		}
	}

	function clearFilters() {
		q = '';
		groups = [];
		actor = null;
		target = null;
	}

	const show = (v: unknown) =>
		v === null || v === undefined || v === '' ? 'none' : typeof v === 'object' ? JSON.stringify(v) : String(v);
	function summary(e: Entry): string {
		return e.changes
			.map((c) => (c.old === null || c.old === undefined ? `${c.key}: ${show(c.new)}` : `${c.key}: ${show(c.old)} → ${show(c.new)}`))
			.join(' · ');
	}
</script>

<div class="phead">
	<div>
		<h1>Audit log</h1>
		<p>{page ? (page.enabled ? `Kept for ${page.retention_days} days · /audit-history retention changes it` : 'Audit history is off') : 'Loading…'}</p>
	</div>
	{#if page && !page.enabled}
		<a class="btn" href="/dashboard/{guildId}/modules"><Icon name="warn" />Audit history is off · manage modules</a>
	{/if}
</div>

<div class="toolbar">
	<div class="search">
		<Icon name="search" />
		<input class="inp" type="search" data-search aria-label="Search reasons and changes" placeholder="Search reasons and changes…" autocomplete="off" bind:value={q} />
	</div>
	<span style="flex:1"></span>
</div>

<div class="facets">
	<div class="facet" role="group" aria-label="Filter by type">
		<span class="lbl">Type</span>
		{#each GROUPS as g (g.n)}
			<button class="chip" aria-pressed={groups.includes(g.n)} onclick={() => (groups = toggle(groups, g.n))}>{g.n}</button>
		{/each}
		{#if actor}
			<button class="chip" aria-pressed="true" aria-label="Remove filter by {who(actor)}" onclick={() => (actor = null)}>by: {who(actor)} <Icon name="x" style="width:11px;height:11px" /></button>
		{/if}
		{#if target}
			<button class="chip" aria-pressed="true" aria-label="Remove target filter" onclick={() => (target = null)}>target: {target} <Icon name="x" style="width:11px;height:11px" /></button>
		{/if}
		{#if filtered}<button class="btn sm ghost" onclick={clearFilters}>Clear all</button>{/if}
	</div>
</div>

<section class="pane" aria-label="Audit log entries">
	{#if !page}
		<EmptyState>Loading…</EmptyState>
	{:else if !rows.length}
		<EmptyState icon="audit">
			{#if filtered}Nothing matches these filters. <button class="linkish" onclick={clearFilters}>Clear filters</button>
			{:else if page.enabled}No entries yet. They appear here as changes happen (Serenity needs View Audit Log).
			{:else}Turn on the Audit history module to start keeping the audit log here. Nothing before that is stored.{/if}
		</EmptyState>
	{:else}
		<DataTable
			label="Audit log entries"
			{rows}
			key={(e) => e.id}
			maxHeight="calc(100dvh - 300px)"
			columns={[
				{ key: 'when', label: 'When' },
				{ key: 'action', label: 'Action' },
				{ key: 'by', label: 'By' },
				{ key: 'target', label: 'Target' },
				{ key: 'details', label: 'Details' }
			]}
		>
			{#snippet cell(e, col)}
				{#if col.key === 'when'}
					<span class="mono dim" title={new Date(e.created_at * 1000).toLocaleString()}>{ago(e.created_at * 1000)}</span>
				{:else if col.key === 'action'}
					<span class="mono" style="font-size:12px">{e.action_name}</span>
				{:else if col.key === 'by'}
					{#if e.actor}
						<button class="linkish chname" style="font-weight:400" onclick={() => (actor = e.actor)}><Avatar name={who(e.actor)} src={e.actor.avatar_url} size="xs" />{who(e.actor)}</button>
					{:else}<span class="dim">-</span>{/if}
				{:else if col.key === 'target'}
					{#if e.target}<button class="linkish mono" style="font-size:12px" onclick={() => (target = e.target)}>{e.target}</button>{:else}<span class="dim">-</span>{/if}
				{:else}
					<span class="fg2" title={[summary(e), e.reason].filter(Boolean).join('\n')} style="display:block;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
						{summary(e) || '-'}{#if e.reason}<span class="dim"> · {e.reason}</span>{/if}
					</span>
				{/if}
			{/snippet}
		</DataTable>
		{#if page.next}
			<div class="pb" style="text-align:center">
				<button class="btn sm" disabled={more} onclick={loadMore}>{more ? 'Loading…' : `Load more · ${rows.length} shown`}</button>
			</div>
		{/if}
	{/if}
</section>
