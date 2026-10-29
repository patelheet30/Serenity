<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import Avatar from '../../components/Avatar.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Modal from '../../components/Modal.svelte';
	import { toast } from '../../toast.svelte';
	import { ago } from '../../util';
	import type { TicketPageProps } from '../Tickets.svelte';
	import { stageColor, typeLabel, who, type Detail, type Queue, type Ticket } from './queue';
	import TicketDetail from './TicketDetail.svelte';

	let { guildId, onerror }: TicketPageProps = $props();

	const REFRESH = 30_000;
	const NO_STAGE = ''; // tickets whose stage was removed from the type, or never set

	let type = $state<number | null>(null);
	let data = $state<Queue | null>(null);
	let version = $state(0);
	let selected = $state<number | null>(null);
	let sheet = $state(false);
	let dragging = $state<number | null>(null);
	let over = $state<string | null>(null);
	let closing = $state<{ ticket: Ticket; stage: string; reason: string } | null>(null);
	// Tickets with a stage move in flight: a second move waits for the first, so they can't land out of order.
	const moving = new Set<number>();
	let reloadDetail = $state(0); // bumped when the board moves the open ticket

	const boards = $derived((data?.types ?? []).filter((t) => t.stages.length));
	const current = $derived(boards.find((t) => t.id === type) ?? boards[0]);
	const columns = $derived.by(() => {
		const stages = current?.stages ?? [];
		const names = new Set(stages.map((s) => s.name));
		const tickets = (data?.tickets ?? []).filter((t) => t.type_id === current?.id);
		const cols = stages.map((s) => ({ stage: s, tickets: tickets.filter((t) => t.stage === s.name) }));
		const orphans = tickets.filter((t) => !t.stage || !names.has(t.stage));
		return orphans.length ? [{ stage: null, tickets: orphans }, ...cols] : cols;
	});
	// The card's title is the first answer the opener gives; the next two are its key fields.
	const shown = $derived((current?.fields ?? []).filter((f) => !f.staff_only).slice(0, 3));

	$effect(() => {
		const id = guildId;
		version;
		let stale = false;
		// Every type's open tickets in one call: switching boards needs no reload.
		api<Queue>(`/guilds/${id}/tickets/queue`).then(
			(r) => !stale && (data = r),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	$effect(() => {
		const t = setInterval(() => document.visibilityState === 'visible' && !dragging && version++, REFRESH);
		return () => clearInterval(t);
	});

	async function move(t: Ticket, to: string) {
		if (t.stage === to || !current || moving.has(t.number)) return;
		const stage = current.stages.find((s) => s.name === to);
		const from = t.stage;
		t.stage = to; // optimistic; the bot posts the move in the channel
		moving.add(t.number);
		const r = await save(() => send<Detail>('POST', `/guilds/${guildId}/tickets/${t.number}/stage`, { stage: to }));
		moving.delete(t.number);
		if (!r) {
			t.stage = from;
			return;
		}
		toast(`#${t.number} moved to ${to}`);
		if (stage?.terminal) closing = { ticket: t, stage: to, reason: to }; // closes "as" the stage, like the Discord button
		if (selected === t.number) reloadDetail++;
	}

	function step(t: Ticket, by: -1 | 1) {
		const stages = current?.stages ?? [];
		const next = stages[stages.findIndex((s) => s.name === t.stage) + by];
		if (next) move(t, next.name);
	}

	async function closeTicket(e: Event) {
		e.preventDefault();
		if (!closing) return;
		const { ticket, reason } = closing;
		const r = await save(() => send<Detail>('POST', `/guilds/${guildId}/tickets/${ticket.number}/close`, { reason }));
		if (!r) return;
		closing = null;
		toast(`Closed #${ticket.number}`);
		if (selected === ticket.number) selected = null;
		version++;
	}

	function drop(e: DragEvent, to: string | undefined) {
		e.preventDefault();
		over = null;
		const t = data?.tickets.find((x) => x.number === dragging);
		dragging = null;
		if (t && to) move(t, to);
	}
</script>

{#if data && boards.length > 1}
	<div class="facets">
		<div class="facet" role="group" aria-label="Board">
			<span class="lbl">Type</span>
			{#each boards as b (b.id)}
				<button class="chip" aria-pressed={b === current} onclick={() => ((type = b.id), (selected = null))}>{typeLabel(b)}<span class="n">{data.facets.type[b.id] ?? 0}</span></button>
			{/each}
		</div>
	</div>
{/if}

<div class:split={selected !== null}>
	<section class="pane" aria-label="Ticket board">
		{#if !data}
			<EmptyState>Loading…</EmptyState>
		{:else if !current}
			<EmptyState icon="tickets">No ticket type has stages yet. Add some with <span class="mono">/ticket type stage-add</span> to see a board here.</EmptyState>
		{:else}
			<div class="board">
				{#each columns as col (col.stage?.name ?? NO_STAGE)}
					{@const name = col.stage?.name}
					<div
						class="col"
						class:over={over === (name ?? NO_STAGE) && !!name}
						role="list"
						aria-label="{name ?? 'No stage'}, {col.tickets.length} tickets"
						ondragover={(e) => name && (e.preventDefault(), (over = name))}
						ondragleave={() => (over = null)}
						ondrop={(e) => drop(e, name)}
					>
						<h3>
							<i style:background={stageColor(col.stage ?? undefined)}></i>{name ?? 'No stage'}
							{#if col.stage?.terminal}<span class="dim" title="Moving a ticket here offers to close it">· closes</span>{/if}
							<span class="cnt" style="margin-left:auto">{col.tickets.length}</span>
						</h3>
						{#each col.tickets as t (t.number)}
							{@const i = current.stages.findIndex((s) => s.name === t.stage)}
							<div
								class="card"
								class:sel={selected === t.number}
								class:drag={dragging === t.number}
								role="listitem"
								draggable="true"
								ondragstart={(e) => ((dragging = t.number), e.dataTransfer?.setData('text/plain', String(t.number)))}
								ondragend={() => ((dragging = null), (over = null))}
							>
								<button class="open" onclick={() => ((selected = t.number), (sheet = true))} aria-label="Open ticket {t.number}">
									<span class="top">
										<span class="mono">#{t.number}</span>
										{#if t.priority !== 'normal'}<span class="prio" data-p={t.priority}>{t.priority}</span>{/if}
										{#if t.off_hours}<span class="tag">off-hours</span>{/if}
										<span class="mono dim" style="margin-left:auto" title={new Date(t.opened_at * 1000).toLocaleString()}>{ago(t.opened_at * 1000)}</span>
									</span>
									{#if shown[0]}<b class="title">{t.fields[shown[0].key] || 'No answer'}</b>{/if}
									{#each shown.slice(1) as f (f.key)}
										{#if t.fields[f.key]}<span class="kv"><span class="dim">{f.label}</span> {t.fields[f.key]}</span>{/if}
									{/each}
									<span class="who">
										<Avatar name={who(t.opener)} src={t.opener.avatar_url} size="xs" />{who(t.opener)}
										<span class="dim" style="margin-left:auto">{t.assignee ? who(t.assignee) : 'unclaimed'}</span>
									</span>
								</button>
								<span class="arrows">
									<button class="btn sm ghost" disabled={i <= 0} aria-label="Move #{t.number} to the previous stage" onclick={() => step(t, -1)}><Icon name="arrow" style="transform:scaleX(-1)" /></button>
									<button class="btn sm ghost" disabled={i < 0 || i >= current.stages.length - 1} aria-label="Move #{t.number} to the next stage" onclick={() => step(t, 1)}><Icon name="arrow" /></button>
								</span>
							</div>
						{:else}
							<p class="hint" style="padding:6px 2px">Nothing here.</p>
						{/each}
					</div>
				{/each}
			</div>
		{/if}
	</section>

	{#if selected !== null}
		{#key reloadDetail}
			<TicketDetail {guildId} number={selected} types={data?.types ?? []} {sheet} onclose={() => (selected = null)} onchange={() => version++} onopen={(n) => (selected = n)} />
		{/key}
	{/if}
</div>

<Modal bind:open={() => closing !== null, (v) => !v && (closing = null)} title="Close #{closing?.ticket.number ?? ''}?">
	<form id="boardClose" class="stack" style="gap:12px" onsubmit={closeTicket}>
		<p>{closing?.stage} is a final stage. Close the ticket as well? The channel is deleted and the opener gets the transcript by DM.</p>
		{#if closing}
			<label class="field"><span>Reason</span><input class="inp" maxlength="500" bind:value={closing.reason} /></label>
		{/if}
	</form>
	{#snippet actions()}
		<button class="btn" onclick={() => (closing = null)}>Keep open</button>
		<button class="btn danger" type="submit" form="boardClose">Close ticket</button>
	{/snippet}
</Modal>

<style>
	.board {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(240px, 1fr);
		gap: 10px;
		padding: 12px;
		overflow-x: auto;
		overscroll-behavior-x: contain;
	}
	.col {
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-height: 200px;
		padding: 8px;
		border-radius: var(--R);
		background: var(--ink);
		border: 1px dashed transparent;
		transition: border-color 0.15s;
	}
	.col.over {
		border-color: var(--accln);
	}
	.col h3 {
		display: flex;
		align-items: center;
		gap: 6px;
		margin: 0 2px 2px;
		font-size: 12px;
		font-weight: 600;
		color: var(--fg2);
	}
	.col h3 i {
		width: 8px;
		height: 8px;
		border-radius: 2px;
		flex: none;
	}
	.card {
		position: relative;
		border: 1px solid var(--line2);
		border-radius: var(--r);
		background: var(--ink2);
		cursor: grab;
	}
	.card:hover {
		border-color: var(--line3);
	}
	.card.sel {
		border-color: var(--accln);
	}
	.card.drag {
		opacity: 0.4;
	}
	.open {
		display: grid;
		gap: 4px;
		width: 100%;
		padding: 8px 10px 30px;
		border: 0;
		background: none;
		text-align: left;
		font-size: 12.5px;
		cursor: inherit;
	}
	.top,
	.who {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.title {
		font-weight: 600;
		font-size: 13px;
		overflow-wrap: anywhere;
	}
	.kv {
		color: var(--fg2);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.arrows {
		position: absolute;
		left: 6px;
		bottom: 4px;
		display: flex;
		gap: 2px;
	}
	.arrows .btn {
		height: 22px;
		padding: 0 6px;
	}
	.tag,
	.prio {
		font-family: var(--mono);
		font-size: 10.5px;
		border: 1px solid currentColor;
		border-radius: 4px;
		padding: 0 4px;
		color: var(--lilac);
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
