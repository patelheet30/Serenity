<script lang="ts">
	import { api } from '../../api';
	import Avatar from '../../components/Avatar.svelte';
	import DataTable from '../../components/DataTable.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import { ago } from '../../util';
	import type { TicketPageProps } from '../Tickets.svelte';
	import { stageColor, typeLabel, who, type Queue } from './queue';
	import TicketDetail from './TicketDetail.svelte';

	let { guildId, onerror }: TicketPageProps = $props();

	const REFRESH = 30_000; // the queue changes in Discord too

	let q = $state('');
	let search = $state(''); // q, 250 ms after the last keystroke
	let type = $state<string | null>(null);
	let stage = $state<string | null>(null);
	let tier = $state<string | null>(null);
	let status = $state<string | null>(null);
	let claim = $state<string | null>(null);

	let data = $state<Queue | null>(null);
	let selected = $state<number | null>(null);
	let sheet = $state(false); // the detail's bottom sheet on phones
	let version = $state(0); // bump to reload

	const types = $derived(new Map((data?.types ?? []).map((t) => [String(t.id), t])));
	const picked = $derived(type ? types.get(type) : undefined);
	const tierNames = $derived(new Map((picked ? [picked] : (data?.types ?? [])).flatMap((t) => t.tiers.map((r) => [String(r.id), r.name]))));
	const filtered = $derived(!!(q || type || stage || tier || status || claim));

	$effect(() => {
		const v = q;
		const t = setTimeout(() => (search = v), 250);
		return () => clearTimeout(t);
	});

	function params() {
		const p = new URLSearchParams();
		const set = { type, stage, tier, status, claim, q: search.trim() };
		for (const [k, v] of Object.entries(set)) if (v) p.set(k, v);
		return p;
	}

	$effect(() => {
		const id = guildId;
		const p = params();
		version;
		let stale = false;
		api<Queue>(`/guilds/${id}/tickets/queue?${p}`).then(
			(r) => {
				if (stale) return;
				data = r;
				if (selected === null && r.tickets.length && innerWidth > 980) open(r.tickets[0].number, false);
			},
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	$effect(() => {
		const t = setInterval(() => document.visibilityState === 'visible' && version++, REFRESH);
		return () => clearInterval(t);
	});

	function open(n: number, asSheet = true) {
		selected = n;
		sheet = asSheet;
	}

	function close() {
		selected = null;
		sheet = false;
	}

	function clearFilters() {
		q = search = '';
		type = stage = tier = status = claim = null;
	}

	const columns = $derived([
		{ key: 'number', label: '#' },
		{ key: 'type', label: 'Type' },
		{ key: 'opener', label: 'Opener' },
		{ key: 'stage', label: 'Stage' },
		{ key: 'tier', label: 'Tier' },
		{ key: 'status', label: 'Status' },
		{ key: 'priority', label: 'Priority' },
		{ key: 'assignee', label: 'Claimed by' },
		{ key: 'age', label: 'Age' },
		{ key: 'activity', label: 'Last activity' },
		...(picked?.fields ?? []).map((f) => ({ key: `f:${f.key}`, label: f.label }))
	]);
</script>

<div class="toolbar">
	<div class="search">
		<Icon name="search" />
		<input class="inp" type="search" data-search aria-label="Search tickets by number, opener or answer" placeholder="Search #number, opener, answer…" autocomplete="off" bind:value={q} />
	</div>
	<kbd class="hide-s">/</kbd>
	{#if data}<span class="cnt">{data.tickets.length} of {data.total} open</span>{/if}
	<span style="flex:1"></span>
	<span class="dim hide-s" style="font-size:12px"><kbd>j</kbd><kbd>k</kbd> move · <kbd>↵</kbd> open · <kbd>c</kbd> claim · <kbd>n</kbd> next stage · <kbd>e</kbd> escalate · <kbd>a</kbd> mod action</span>
</div>

{#snippet facet(label: string, value: string | null, set: (v: string | null) => void, options: [string, string, number][])}
	{#if options.length}
		<div class="facet" role="group" aria-label="Filter by {label.toLowerCase()}">
			<span class="lbl">{label}</span>
			{#each options as [v, name, n] (v)}
				<button class="chip" aria-pressed={value === v} onclick={() => set(value === v ? null : v)}>{name}<span class="n">{n}</span></button>
			{/each}
		</div>
	{/if}
{/snippet}

{#if data}
	{@const f = data.facets}
	<div class="facets">
		{@render facet('Type', type, (v) => ((type = v), (stage = null)), data.types.filter((t) => f.type[t.id]).map((t) => [String(t.id), typeLabel(t), f.type[t.id]]))}
		{@render facet('Stage', stage, (v) => (stage = v), (picked?.stages ?? []).filter((s) => f.stage[s.name]).map((s) => [s.name, s.name, f.stage[s.name]]))}
		{@render facet('Tier', tier, (v) => (tier = v), [...tierNames].filter(([id]) => f.tier[id]).map(([id, name]) => [id, name, f.tier[id]]))}
		<div class="facet" role="group" aria-label="Filter by status">
			<span class="lbl">Status</span>
			<button class="chip" aria-pressed={status === 'staff'} onclick={() => (status = status === 'staff' ? null : 'staff')}>Waiting on staff<span class="n">{f.status.staff ?? 0}</span></button>
			<button class="chip" aria-pressed={status === 'opener'} onclick={() => (status = status === 'opener' ? null : 'opener')}>Waiting on member<span class="n">{f.status.opener ?? 0}</span></button>
			<button class="chip" aria-pressed={claim === 'mine'} onclick={() => (claim = claim === 'mine' ? null : 'mine')}>Mine<span class="n">{f.mine}</span></button>
			<button class="chip" aria-pressed={claim === 'unclaimed'} onclick={() => (claim = claim === 'unclaimed' ? null : 'unclaimed')}>Unclaimed<span class="n">{f.unclaimed}</span></button>
			{#if filtered}<button class="btn sm ghost" onclick={clearFilters}>Clear all</button>{/if}
		</div>
	</div>
{/if}

<div class="split">
	<section class="pane" aria-label="Ticket queue">
		{#if !data}
			<EmptyState>Loading…</EmptyState>
		{:else if !data.tickets.length}
			<EmptyState icon="tickets">
				{#if filtered}No open tickets match these filters. <button class="linkish" onclick={clearFilters}>Clear filters</button>
				{:else}No open tickets. New ones appear here as members open them.{/if}
			</EmptyState>
		{:else}
			<DataTable
				label="Open tickets"
				rows={data.tickets}
				key={(t) => String(t.number)}
				selected={selected === null ? null : String(selected)}
				onopen={(t) => open(t.number)}
				maxHeight="calc(100dvh - 330px)"
				{columns}
			>
				{#snippet cell(t, col)}
					{@const ty = types.get(String(t.type_id))}
					{#if col.key === 'number'}
						<span class="mono">#{t.number}</span>
						{#if t.off_hours}<span class="tag" title="Opened outside support hours">off-hours</span>{/if}
					{:else if col.key === 'type'}
						{ty ? typeLabel(ty) : ''}
					{:else if col.key === 'opener'}
						<span class="chname" style="font-weight:400"><Avatar name={who(t.opener)} src={t.opener.avatar_url} size="xs" />{who(t.opener)}</span>
					{:else if col.key === 'stage'}
						{#if t.stage}<span class="stage" style:color={stageColor(ty?.stages.find((s) => s.name === t.stage))}>{t.stage}</span>{:else}<span class="dim">-</span>{/if}
					{:else if col.key === 'tier'}
						{t.tier_id ? tierNames.get(String(t.tier_id)) : '-'}
					{:else if col.key === 'status'}
						{#if t.waiting_on === 'staff'}<span class="pill acc">waiting on staff</span>{:else}<span class="dim mono" style="font-size:11.5px">waiting on member</span>{/if}
					{:else if col.key === 'priority'}
						<span class="prio" data-p={t.priority}>{t.priority}</span>
					{:else if col.key === 'assignee'}
						{#if t.assignee}{who(t.assignee)}{:else}<span class="dim">unclaimed</span>{/if}
					{:else if col.key === 'age'}
						<span class="mono dim" title={new Date(t.opened_at * 1000).toLocaleString()}>{ago(t.opened_at * 1000)}</span>
					{:else if col.key === 'activity'}
						<span class="mono dim" title={new Date(t.last_activity_at * 1000).toLocaleString()}>{ago(t.last_activity_at * 1000)}</span>
					{:else}
						<span class="fg2" style="display:block;max-width:220px;overflow:hidden;text-overflow:ellipsis">{t.fields[col.key.slice(2)] || '-'}</span>
					{/if}
				{/snippet}
			</DataTable>
		{/if}
	</section>

	<TicketDetail {guildId} number={selected} types={data?.types ?? []} {sheet} onclose={close} onchange={() => version++} onopen={(n) => open(n, sheet)} />
</div>

<style>
	.tag {
		margin-left: 6px;
		font-family: var(--mono);
		font-size: 10.5px;
		color: var(--lilac);
		border: 1px solid currentColor;
		border-radius: 4px;
		padding: 0 4px;
	}
	.stage,
	.prio {
		display: inline-block;
		font-family: var(--mono);
		font-size: 11px;
		line-height: 18px;
		padding: 0 6px;
		border-radius: 4px;
		border: 1px solid currentColor;
		white-space: nowrap;
	}
	.prio {
		color: var(--fg3);
	}
	.prio[data-p='high'] {
		color: var(--warn);
	}
	.prio[data-p='urgent'] {
		color: var(--red);
	}
</style>
