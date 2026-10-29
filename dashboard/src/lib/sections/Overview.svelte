<script lang="ts">
	import { api, type Channel, type Modules } from '../api';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import Sparkline from '../components/Sparkline.svelte';
	import type { IconName } from '../icons';
	import { ago } from '../util';
	import type { Case } from './moderation';

	// Composes the other sections' endpoints (#49): nothing here has its own data source.
	type Slowmode = { interval: number; default_threshold: number; channels: { id: string; enabled: boolean; threshold: number | null }[] };
	type Live = { id: string; rate: number; history: number[]; slowmode: number | null };
	type Change = { channel_id: string; old: number; new: number; reason: string; rate: number; confidence: number; at: number };
	type Data = {
		slowmode: Slowmode;
		modules: Modules;
		logging: { types: { enabled: boolean }[] };
		history: Change[];
		cases: { cases: Case[] };
		analytics: { changes: Record<'6h' | '24h', number> };
		stats: { active: { timeouts: number; warnings: number } };
		/** Where Tickets is available: the ticket stats' counts. */
		tickets: { open: number; unclaimed: number; waiting_on_staff: number; opened: number } | null;
	};

	let {
		guildId,
		guildName,
		channels,
		tickets,
		onerror
	}: { guildId: string; guildName: string; channels: Channel[]; tickets: boolean; onerror: (e: unknown) => void } = $props();

	const MODULES: [string, IconName][] = [
		['slowmode', 'slowmode'],
		['logging', 'logging'],
		['moderation', 'cases']
	];

	let data = $state<Data | null>(null);
	let live = $state<Record<string, Live>>({});

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		live = {};
		const g = `/guilds/${id}`;
		Promise.all([
			api<Slowmode>(`${g}/slowmode`),
			api<Modules>(`${g}/modules`),
			api<Data['logging']>(`${g}/logging`),
			api<Change[]>(`${g}/slowmode/history`),
			api<Data['cases']>(`${g}/cases`),
			api<Data['stats']>(`${g}/modstats?days=7`),
			api<Data['analytics']>(`${g}/slowmode/analytics`),
			// Optional: a failing tickets pane mustn't take the rest of the page with it.
			tickets ? api<Data['tickets']>(`${g}/tickets/stats?days=7`).catch(() => null) : null
		]).then(
			([slowmode, modules, logging, history, cases, stats, analytics, tickets]) =>
				!stale && (data = { slowmode, modules, logging, history, cases, stats, analytics, tickets }),
			(e) => !stale && onerror(e)
		);
		// The pulse keeps moving; a failed refresh keeps the last numbers (the page loaded fine).
		const tick = () =>
			!document.hidden &&
			api<{ channels: Live[] }>(`${g}/slowmode/live`).then(
				(l) => !stale && (live = Object.fromEntries(l.channels.map((c) => [c.id, c]))),
				() => {}
			);
		tick();
		const timer = setInterval(tick, 10_000);
		return () => {
			stale = true;
			clearInterval(timer);
		};
	});

	const name = (id: string) => channels.find((c) => c.id === id)?.name ?? id;
	const fmt = (s: number | null | undefined) => (s == null ? '-' : s === 0 ? 'off' : s < 60 ? `${s}s` : `${Math.round(s / 60)}m`);
	const total = $derived(Object.values(live).reduce((a, c) => a + c.rate, 0));
</script>

<div class="phead">
	<div>
		<h1>{guildName}</h1>
		<p>{data ? `Auto slowmode checks every ${data.slowmode.interval} min` : 'Loading…'}</p>
	</div>
	<div class="pill"><span class="live" aria-hidden="true"></span><span><b>{total.toFixed(1)}</b> msgs/min across {Object.keys(live).length} channels</span></div>
</div>

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	{@const d = data}
	<div class="grid">
		<section class="pane s8" aria-labelledby="pulseT">
			<div class="ph">
				<h2 id="pulseT">Channel pulse</h2>
				<span class="sp"></span>
				<span class="dim hide-s" style="font-size:12px">rate · threshold · slowmode</span>
				<a class="btn sm ghost" href="/dashboard/{guildId}/slowmode">Configure <Icon name="arrow" /></a>
			</div>
			{#each d.slowmode.channels as c (c.id)}
				{@const l = live[c.id]}
				{@const th = c.threshold ?? d.slowmode.default_threshold}
				<a class="pulse-row" href="/dashboard/{guildId}/slowmode" aria-label="#{name(c.id)}, open in Slowmode">
					<span class="chname"><Icon name="hash" />{name(c.id)}</span>
					<Sparkline values={l?.history ?? []} threshold={th} muted={!c.enabled} label="#{name(c.id)} messages per minute" />
					<span class="mono" style="text-align:right">{l?.rate.toFixed(1) ?? '-'}</span>
					<span class="mono dim hide-s" style="text-align:right">/ {th}{c.threshold == null ? '*' : ''}</span>
					<span class="sm-val" class:on={l?.slowmode} style="text-align:right">{c.enabled ? fmt(l?.slowmode) : 'paused'}</span>
				</a>
			{:else}
				<EmptyState icon="slowmode">No channels yet. <a class="linkish" href="/dashboard/{guildId}/slowmode">Add some in Slowmode</a></EmptyState>
			{/each}
		</section>

		<section class="pane s4" aria-labelledby="modT">
			<div class="ph"><h2 id="modT">Modules</h2><span class="sp"></span><a class="btn sm ghost" href="/dashboard/{guildId}/modules">Manage</a></div>
			{#each MODULES as [k, icon] (k)}
				<div class="modrow">
					<Icon name={icon} /><span style="flex:1;text-transform:capitalize">{k}</span>
					<span class="mono" class:acc={d.modules.enabled[k]} class:dim={!d.modules.enabled[k]}>{d.modules.enabled[k] ? 'on' : 'off'}</span>
				</div>
			{/each}
			<div class="pb" style="border-top:1px solid var(--line)">
				<dl class="kv">
					<dt>Active warnings</dt><dd>{d.stats.active.warnings}</dd>
					<dt>Active timeouts</dt><dd>{d.stats.active.timeouts}</dd>
					<dt>Slowmode changes, 6h</dt><dd>{d.analytics.changes['6h'].toLocaleString()}</dd>
					<dt>Log types enabled</dt><dd>{d.logging.types.filter((t) => t.enabled).length} / {d.logging.types.length}</dd>
				</dl>
			</div>
		</section>

		<section class="pane s7" aria-labelledby="chgT">
			<div class="ph"><h2 id="chgT">Recent slowmode changes</h2><span class="sp"></span><a class="btn sm ghost" href="/dashboard/{guildId}/slowmode">All history</a></div>
			<div class="scroll">
				{#if d.history.length}
					<table class="tbl">
						<thead><tr><th>When</th><th>Channel</th><th>Change</th><th>Reason</th><th class="r">Rate</th></tr></thead>
						<tbody>
							{#each d.history.slice(0, 6) as h, i (i)}
								<tr>
									<td class="mono dim">{ago(h.at * 1000)}</td>
									<td>#{name(h.channel_id)}</td>
									<td class="mono">{fmt(h.old)} <span class="dim">→</span> <span class:acc={h.new < h.old}>{fmt(h.new)}</span></td>
									<td class="fg2">{h.reason}</td>
									<td class="mono r">{h.rate.toFixed(1)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{:else}
					<EmptyState>No slowmode changes yet. They appear here as Serenity adjusts channels.</EmptyState>
				{/if}
			</div>
		</section>

		<section class="pane s5" aria-labelledby="rcT">
			<div class="ph"><h2 id="rcT">Latest cases</h2><span class="sp"></span><a class="btn sm ghost" href="/dashboard/{guildId}/cases">All cases</a></div>
			{#if d.cases.cases.length}
				<ul class="mini-list">
					{#each d.cases.cases.slice(0, 6) as c (c.number)}
						<li>
							<span class="mono">#{c.number}</span>
							<span class="act" data-a={c.action}>{c.action}</span>
							<span class="grow">{c.target.name ?? c.target.id} <span class="dim">· {c.reason ?? 'No reason'}</span></span>
							<span class="mono dim">{ago(c.created_at * 1000)}</span>
						</li>
					{/each}
				</ul>
			{:else}
				<EmptyState icon="cases">No cases yet.</EmptyState>
			{/if}
		</section>

		{#if d.tickets}
			{@const q = `/dashboard/${guildId}/tickets/queue`}
			<section class="pane s12" aria-labelledby="tkT">
				<div class="ph"><h2 id="tkT">Tickets</h2><span class="sp"></span><a class="btn sm ghost" href={q}>Open queue <Icon name="arrow" /></a></div>
				<div class="tiles" style="border:0;border-radius:0">
					<a class="tile" href={q}><b>{d.tickets.open}</b><span>Open now</span></a>
					<a class="tile" href={q}><b class:acc={d.tickets.unclaimed}>{d.tickets.unclaimed}</b><span>Unclaimed</span></a>
					<a class="tile" href={q}><b>{d.tickets.waiting_on_staff}</b><span>Waiting on staff</span></a>
					<a class="tile" href="/dashboard/{guildId}/tickets/stats"><b>{d.tickets.opened}</b><span>Opened in 7 days</span></a>
				</div>
			</section>
		{/if}
	</div>
{/if}

<style>
	.tiles a {
		text-decoration: none;
	}
	.tiles a:hover {
		background: var(--ink3);
	}
</style>
