<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '../../api';
	import Avatar from '../../components/Avatar.svelte';
	import DataTable from '../../components/DataTable.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import { toast } from '../../toast.svelte';
	import { ago } from '../../util';
	import type { TicketPageProps } from '../Tickets.svelte';
	import { expiry, OUTCOMES, who, type TicketUser, type TranscriptRow } from './transcripts';

	type Type = { id: number; name: string; emoji: string | null };
	type Page = { transcripts: TranscriptRow[]; next: number | null };

	let { guildId, onerror }: TicketPageProps = $props();

	let types = $state<Type[]>([]);
	let picked = $state<number[]>([]);
	let outcome = $state<keyof typeof OUTCOMES | null>(null);
	let opener = $state<TicketUser | null>(null);
	let staff = $state<TicketUser | null>(null);
	let from = $state(''); // yyyy-mm-dd from <input type="date">, local time
	let to = $state('');

	let rows = $state<TranscriptRow[] | null>(null);
	let next = $state<number | null>(null);

	const filtered = $derived(picked.length > 0 || !!outcome || !!opener || !!staff || !!from || !!to);
	const day = (d: string) => Math.floor(new Date(`${d}T00:00`).getTime() / 1000);

	function params(before?: number) {
		const p = new URLSearchParams();
		if (picked.length) p.set('type', picked.join(','));
		if (outcome) p.set('outcome', outcome);
		if (opener) p.set('opener', opener.id);
		if (staff) p.set('staff', staff.id);
		if (from) p.set('since', String(day(from)));
		if (to) p.set('until', String(day(to) + 86_400)); // through the end of that day
		if (before) p.set('before', String(before));
		return p;
	}

	$effect(() => {
		const id = guildId;
		let stale = false;
		// A new server: drop the last one's rows and type picks (its ids mean nothing here).
		types = [];
		picked = [];
		opener = null;
		staff = null;
		rows = null;
		next = null;
		api<Type[]>(`/guilds/${id}/tickets/types`).then((t) => !stale && (types = t), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	let generation = 0; // a "Load more" answer for older filters is dropped
	$effect(() => {
		const id = guildId;
		const p = params();
		generation++;
		let stale = false;
		api<Page>(`/guilds/${id}/tickets/transcripts?${p}`).then(
			(r) => {
				if (stale) return;
				rows = r.transcripts;
				next = r.next;
			},
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	let more = $state(false);
	async function loadMore() {
		if (!next || more) return;
		more = true;
		const gen = generation;
		try {
			const r = await api<Page>(`/guilds/${guildId}/tickets/transcripts?${params(next)}`);
			if (gen !== generation || !rows) return;
			rows.push(...r.transcripts);
			next = r.next;
		} catch (e) {
			if (gen === generation) toast(e instanceof Error ? e.message : String(e), { error: true });
		} finally {
			more = false;
		}
	}

	function clearFilters() {
		picked = [];
		outcome = null;
		opener = null;
		staff = null;
		from = '';
		to = '';
	}

	const open = (t: TranscriptRow) => goto(`/transcripts/${t.id}`);
</script>

<div class="toolbar">
	<label class="field" style="display:flex;align-items:center;gap:6px">
		<span>Closed from</span><input class="inp" type="date" bind:value={from} max={to || undefined} />
	</label>
	<label class="field" style="display:flex;align-items:center;gap:6px">
		<span>to</span><input class="inp" type="date" bind:value={to} min={from || undefined} />
	</label>
	{#if rows}<span class="cnt">{rows.length}{next ? '+' : ''} shown</span>{/if}
</div>

<div class="facets">
	{#if types.length > 1}
		<div class="facet" role="group" aria-label="Filter by type">
			<span class="lbl">Type</span>
			{#each types as t (t.id)}
				<button class="chip" aria-pressed={picked.includes(t.id)} onclick={() => (picked = picked.includes(t.id) ? picked.filter((x) => x !== t.id) : [...picked, t.id])}>
					{t.emoji ?? ''} {t.name}
				</button>
			{/each}
		</div>
	{/if}
	<div class="facet" role="group" aria-label="Filter by outcome">
		<span class="lbl">Outcome</span>
		{#each Object.entries(OUTCOMES) as [k, label] (k)}
			<button class="chip" aria-pressed={outcome === k} onclick={() => (outcome = outcome === k ? null : (k as keyof typeof OUTCOMES))}>{label}</button>
		{/each}
		{#if opener}
			<button class="chip" aria-pressed="true" aria-label="Remove opener filter {who(opener)}" onclick={() => (opener = null)}>
				opener: {who(opener)} <Icon name="x" style="width:11px;height:11px" />
			</button>
		{/if}
		{#if staff}
			<button class="chip" aria-pressed="true" aria-label="Remove staff filter {who(staff)}" onclick={() => (staff = null)}>
				staff: {who(staff)} <Icon name="x" style="width:11px;height:11px" />
			</button>
		{/if}
		{#if filtered}<button class="btn sm ghost" onclick={clearFilters}>Clear all</button>{/if}
	</div>
</div>

<section class="pane" aria-label="Transcripts">
	{#if !rows}
		<EmptyState>Loading…</EmptyState>
	{:else if !rows.length}
		<EmptyState icon="tickets">
			{#if filtered}No transcripts match these filters. <button class="linkish" onclick={clearFilters}>Clear filters</button>
			{:else}No transcripts yet. Each closed ticket keeps one, unless its type turns them off.{/if}
		</EmptyState>
	{:else}
		<DataTable
			label="Transcripts"
			{rows}
			key={(t) => t.id}
			onopen={open}
			maxHeight="calc(100dvh - 330px)"
			columns={[
				{ key: 'number', label: 'Ticket' },
				{ key: 'type', label: 'Type' },
				{ key: 'opener', label: 'Opener' },
				{ key: 'staff', label: 'Staff' },
				{ key: 'closed', label: 'Closed' },
				{ key: 'outcome', label: 'Outcome' },
				{ key: 'rating', label: 'Rating' },
				{ key: 'expiry', label: 'Expiry' }
			]}
		>
			{#snippet cell(t, col)}
				{#if col.key === 'number'}
					<a class="linkish mono" href="/transcripts/{t.id}">#{t.number}</a>
				{:else if col.key === 'type'}
					{t.type.emoji ?? ''} {t.type.name}
				{:else if col.key === 'opener'}
					<button class="linkish chname" style="font-weight:400" title="Show only {who(t.opener)}’s tickets" onclick={() => (opener = t.opener)}>
						<Avatar name={who(t.opener)} src={t.opener.avatar_url} size="xs" />{who(t.opener)}
					</button>
				{:else if col.key === 'staff'}
					{#each t.staff as s, i (s.id)}
						{i ? ', ' : ''}<button class="linkish" title="Show only tickets {who(s)} handled" onclick={() => (staff = s)}>{who(s)}</button>
					{:else}<span class="dim">-</span>{/each}
				{:else if col.key === 'closed'}
					<span class="mono dim" title={new Date(t.closed_at * 1000).toLocaleString()}>{ago(t.closed_at * 1000)}</span>
				{:else if col.key === 'outcome'}
					<span class="fg2" title={t.reason ?? ''}>{OUTCOMES[t.outcome]}</span>
				{:else if col.key === 'rating'}
					{#if t.rating}<span class="mono" aria-label="{t.rating} out of 5">{'★'.repeat(t.rating)}<span class="dim">{'★'.repeat(5 - t.rating)}</span></span>{:else}<span class="dim">-</span>{/if}
				{:else}
					<span class="mono" class:acc={!!t.held_by_case} class:dim={!t.held_by_case} style="font-size:11.5px">{expiry(t)}</span>
					{#if t.deletion_requested_at}<span class="pill warn-t" title="The opener asked for this transcript to be deleted"><Icon name="warn" />asked to delete</span>{/if}
				{/if}
			{/snippet}
		</DataTable>
		{#if next}
			<div class="pb" style="text-align:center">
				<button class="btn sm" disabled={more} onclick={loadMore}>{more ? 'Loading…' : 'Load more'}</button>
			</div>
		{/if}
	{/if}
</section>
