<script lang="ts">
	import { api, send, type Channel, type Modules } from '../api';
	import { save } from '../autosave.svelte';
	import DataTable from '../components/DataTable.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import InlineEdit from '../components/InlineEdit.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import Sparkline from '../components/Sparkline.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { registerCommands } from '../palette.svelte';
	import { toast } from '../toast.svelte';
	import { ago } from '../util';

	type Row = { id: string; enabled: boolean; threshold: number | null; slowmode: number | null };
	type Slowmode = { enabled: boolean; default_threshold: number; interval: number; channels: Row[] };
	type Live = { id: string; rate: number; history: number[]; slowmode: number | null };
	type Change = { channel_id: string; old: number; new: number; reason: string; rate: number; confidence: number; at: number };
	type Hour = { messages: number; users: number; avg_slowmode: number; max_slowmode: number };
	type Analytics = { hourly: Hour[]; changes: Record<'6h' | '24h', number>; effectiveness: { channel_id: string; before: number; after: number; samples: number }[] };

	let { guildId, channels, onerror }: { guildId: string; channels: Channel[]; onerror: (e: unknown) => void } =
		$props();

	const MAX = 100; // same limits as /serenity guild threshold and /serenity channel threshold
	let data: Slowmode | null = $state(null);
	let modules: Modules | null = $state(null);
	let adding: string | null = $state(null);

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		modules = null;
		Promise.all([api<Slowmode>(`/guilds/${id}/slowmode`), api<Modules>(`/guilds/${id}/modules`)]).then(
			([s, m]) => !stale && ((data = s), (modules = m)),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	// Live rates and history poll every 10 s while the tab is visible. Only a failure before the
	// first answer replaces the page; later ones keep the last numbers and say so.
	let live: Record<string, Live> = $state({});
	let history = $state<Change[] | null>(null);
	let analytics = $state<Analytics | null>(null);
	let liveFailing = $state(false);
	let historyFor = $state<string | null>(null);

	$effect(() => {
		const id = guildId;
		let stale = false;
		live = {};
		history = null;
		analytics = null;
		historyFor = null;
		liveFailing = false;
		let loaded = false; // plain, so reading it doesn't make this effect depend on `history`
		async function tick() {
			if (document.hidden && loaded) return;
			try {
				const [l, h] = await Promise.all([
					api<{ channels: Live[] }>(`/guilds/${id}/slowmode/live`),
					api<Change[]>(`/guilds/${id}/slowmode/history`)
				]);
				if (stale) return;
				live = Object.fromEntries(l.channels.map((c) => [c.id, c]));
				history = h;
				loaded = true;
				liveFailing = false;
			} catch (e) {
				if (stale) return;
				if (loaded) liveFailing = true;
				else onerror(e);
			}
		}
		tick();
		const timer = setInterval(tick, 10_000);
		api<Analytics>(`/guilds/${id}/slowmode/analytics`).then((a) => !stale && (analytics = a), (e) => !stale && onerror(e));
		return () => {
			stale = true;
			clearInterval(timer);
		};
	});

	const name = (id: string) => channels.find((c) => c.id === id)?.name ?? id;
	const text = $derived(channels.filter((c) => c.type === 'text'));
	const available = $derived(text.filter((c) => !data?.channels.some((r) => r.id === c.id)));
	const threshold = (r: Row) => r.threshold ?? data!.default_threshold;
	const slowmodeOf = (r: Row) => live[r.id]?.slowmode ?? r.slowmode;
	const shownHistory = $derived((history ?? []).filter((h) => !historyFor || h.channel_id === historyFor));
	const fmt = (s: number | null) => (s == null ? '-' : s === 0 ? 'off' : s < 60 ? `${s}s` : `${Math.round(s / 60)}m`);

	async function patch(body: Partial<Slowmode>) {
		const r = await save(() => send<Slowmode>('PATCH', `/guilds/${guildId}/slowmode`, body));
		if (r) data = r;
		return r;
	}

	async function putChannel(id: string, body: Partial<Row>) {
		const r = await save(() => send<Slowmode>('PUT', `/guilds/${guildId}/slowmode/channels/${id}`, body));
		if (r) data = r;
		return r;
	}

	async function remove(row: Row) {
		const r = await save(() => send<Slowmode>('DELETE', `/guilds/${guildId}/slowmode/channels/${row.id}`));
		if (!r) return;
		data = r;
		toast(`#${name(row.id)} removed`, {
			undo: () => putChannel(row.id, { enabled: row.enabled, threshold: row.threshold })
		});
	}

	/** Blank or 0 means "use the server default". */
	function parseThreshold(v: string): number | null | undefined {
		const t = v.trim();
		if (t === '' || t === 'default') return null;
		const n = Number(t);
		if (Number.isInteger(n) && n >= 0 && n <= MAX) return n || null;
		toast(`Threshold must be a whole number from 1 to ${MAX}, or blank for the default`, { error: true });
	}

	async function setThreshold(row: Row, v: string) {
		const t = parseThreshold(v);
		if (t !== undefined) await putChannel(row.id, { threshold: t });
	}

	// The default threshold saves 500 ms after the last step, so holding + doesn't send 20 writes.
	let draft: number | null = $state(null);
	let timer: ReturnType<typeof setTimeout> | undefined;
	function step(to: number) {
		draft = Math.min(MAX, Math.max(1, Math.round(to) || 1));
		clearTimeout(timer);
		timer = setTimeout(async () => {
			const v = draft!;
			await patch({ default_threshold: v });
			if (draft === v) draft = null;
		}, 500);
	}
	$effect(() => () => clearTimeout(timer));

	$effect(() =>
		registerCommands([
			{
				id: 'slowmode:threshold',
				group: 'Templates',
				title: 'set #channel threshold …',
				icon: 'slowmode',
				keywords: 'slowmode',
				template: 'set #',
				parse: (q) => {
					const m = /^set #?([\w-]+) threshold (\d+|default)$/i.exec(q);
					const ch = m && (text.find((c) => c.name === m[1]) ?? text.find((c) => c.name.startsWith(m[1])));
					const t = m && parseThreshold(m[2]);
					if (!ch || t === undefined) return null;
					return {
						label: `Set #${ch.name} threshold to ${t ?? 'the server default'}`,
						run: () => putChannel(ch.id, { threshold: t }).then((r) => r && toast(`#${ch.name} threshold → ${t ?? 'default'}`))
					};
				}
			},
			{
				id: 'slowmode:default',
				group: 'Templates',
				title: 'set default threshold …',
				icon: 'slowmode',
				template: 'set default threshold ',
				parse: (q) => {
					const m = /^set default threshold (\d+)$/i.exec(q);
					const t = m ? Number(m[1]) : 0;
					if (!t || t > MAX) return null;
					return {
						label: `Set the default threshold to ${t}`,
						run: () => patch({ default_threshold: t }).then((r) => r && toast(`Default threshold → ${t}`))
					};
				}
			}
		])
	);
</script>

<div class="phead">
	<div>
		<h1>Slowmode</h1>
		<p>Thresholds are messages per minute. A blank channel threshold uses the server default. Changes save as you make them and apply on the next check.</p>
	</div>
	<div class="pill">
		{#if liveFailing}<Icon name="warn" />reconnecting…{:else}<span class="live" aria-hidden="true"></span>live{/if}
	</div>
</div>

{#if !data || !modules}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<div class="grid">
		<section class="pane s4" aria-labelledby="gsT">
			<div class="ph">
				<h2 id="gsT">Server settings</h2>
				<span class="sp"></span>
				<Toggle
					label="Auto slowmode for this server"
					bind:checked={data.enabled}
					onchange={async (on) => !(await patch({ enabled: on })) && (data!.enabled = !on)}
				/>
			</div>
			<div class="pb stack">
				{#if !modules.enabled.slowmode}
					<p class="hint warn-t">
						The Slowmode module is off. Settings are kept but nothing is applied.
						<button
							class="linkish"
							onclick={async () => {
								const m = await save(() => send<Modules>('PUT', `/guilds/${guildId}/modules/slowmode`, { enabled: true }));
								if (m) modules = m;
							}}>Turn it on</button
						>
					</p>
				{/if}
				<div class="field">
					<span id="thL">Default threshold</span>
					<div class="rowline">
						<div class="stepper" role="group" aria-labelledby="thL">
							<button aria-label="Decrease default threshold" onclick={() => step((draft ?? data!.default_threshold) - 1)}>−</button>
							<input
								type="number"
								inputmode="numeric"
								min="1"
								max={MAX}
								aria-labelledby="thL"
								value={draft ?? data.default_threshold}
								onchange={(e) => step(Number(e.currentTarget.value))}
							/>
							<button aria-label="Increase default threshold" onclick={() => step((draft ?? data!.default_threshold) + 1)}>+</button>
						</div>
						<span class="hint">msgs / min</span>
					</div>
					<p class="hint">Used by channels without their own threshold.</p>
				</div>
				<div class="field">
					<span id="ivL">Update interval</span>
					<Segmented
						label="Update interval"
						bind:value={data.interval}
						options={[1, 2, 3, 4, 5].map((v) => ({ value: v, label: `${v}m` }))}
						onchange={async (v, previous) => {
							// Only undo our own value: a later click may already have saved another.
							if (!(await patch({ interval: v })) && data!.interval === v) data!.interval = previous;
						}}
					/>
					<p class="hint">How often Serenity re-scores each channel.</p>
				</div>
			</div>
		</section>

		<section class="pane s8" aria-labelledby="chT">
			<div class="ph">
				<h2 id="chT">Channels</h2>
				<span class="cnt">{data.channels.length}</span>
				<span class="sp"></span>
				<span class="dim hide-s" style="font-size:12px">click a threshold to edit · <kbd>j</kbd><kbd>k</kbd> move</span>
				<Select
					label="Add channel"
					placeholder="Add channel"
					align="right"
					bind:value={adding}
					options={available.map((c) => ({ value: c.id, label: `#${c.name}` }))}
					onchange={async (id) => {
						adding = null;
						if (await putChannel(id, {})) toast(`#${name(id)} added`);
					}}
				/>
			</div>
			{#if data.channels.length}
				<DataTable
					label="Slowmode channels"
					rows={data.channels}
					key={(r) => r.id}
					columns={[
						{ key: 'name', label: 'Channel' },
						{ key: 'rate', label: 'Live rate', align: 'right' },
						{ key: 'spark', label: 'Last 24 min' },
						{ key: 'threshold', label: 'Threshold' },
						{ key: 'slowmode', label: 'Slowmode' },
						{ key: 'enabled', label: 'Auto' },
						{ key: 'actions', label: '', align: 'right' }
					]}
				>
					{#snippet cell(row, c)}
						{#if c.key === 'name'}
							<span class="chname"><Icon name="hash" />{name(row.id)}</span>
						{:else if c.key === 'rate'}
							<span class="mono" class:warn-t={row.enabled && (live[row.id]?.rate ?? 0) > threshold(row)}>{live[row.id]?.rate.toFixed(1) ?? '-'}</span>
						{:else if c.key === 'spark'}
							<div style="width:130px">
								<Sparkline
									values={live[row.id]?.history ?? []}
									threshold={threshold(row)}
									muted={!row.enabled}
									label="#{name(row.id)}, messages per minute over the last 24 minutes"
								/>
							</div>
						{:else if c.key === 'threshold'}
							<InlineEdit
								label="threshold for #{name(row.id)}, blank uses the default"
								value={row.threshold?.toString() ?? ''}
								placeholder="default {data!.default_threshold}"
								onsave={(v) => setThreshold(row, v)}
							/>
						{:else if c.key === 'slowmode'}
							<span class="sm-val" class:on={slowmodeOf(row)}>{fmt(slowmodeOf(row))}</span>
						{:else if c.key === 'enabled'}
							<Toggle
								label="Auto slowmode for #{name(row.id)}"
								bind:checked={row.enabled}
								onchange={async (on) => !(await putChannel(row.id, { enabled: on })) && (row.enabled = !on)}
							/>
						{:else}
							<button
								class="btn sm ghost"
								aria-pressed={historyFor === row.id}
								aria-label="Show change history for #{name(row.id)}"
								onclick={() => (historyFor = historyFor === row.id ? null : row.id)}
								>History <span class="mono dim">{history?.filter((h) => h.channel_id === row.id).length ?? 0}</span></button
							>
							<button class="btn sm ghost" aria-label="Remove #{name(row.id)}" onclick={() => remove(row)}><Icon name="x" /></button>
						{/if}
					{/snippet}
				</DataTable>
			{:else}
				<EmptyState icon="slowmode">No channels yet. Add one and Serenity starts managing its slowmode.</EmptyState>
			{/if}
		</section>

		<section class="pane s7" aria-labelledby="hT">
			<div class="ph">
				<h2 id="hT">Change history</h2>
				<span class="cnt">{shownHistory.length}</span>
				<span class="sp"></span>
				{#if historyFor}
					<button class="chip" aria-pressed="true" aria-label="Show every channel's changes" onclick={() => (historyFor = null)}>
						#{name(historyFor)} <Icon name="x" style="width:11px;height:11px" />
					</button>
				{/if}
			</div>
			<div class="scroll" style="max-height:420px">
				{#if shownHistory.length}
					<table class="tbl">
						<thead><tr><th>When</th><th>Channel</th><th>Change</th><th>Reason</th><th class="r">Rate</th><th class="r">Conf.</th></tr></thead>
						<tbody>
							{#each shownHistory as h, i (i)}
								<tr>
									<td class="mono dim" title={new Date(h.at * 1000).toLocaleString()}>{ago(h.at * 1000)}</td>
									<td>#{name(h.channel_id)}</td>
									<td class="mono">{fmt(h.old)} <span class="dim">→</span> <span class:acc={h.new < h.old}>{fmt(h.new)}</span></td>
									<td class="fg2">{h.reason}</td>
									<td class="mono r">{h.rate.toFixed(1)}</td>
									<td class="mono r dim">{Math.round(h.confidence * 100)}%</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{:else}
					<EmptyState
						>{!history
							? 'Loading…'
							: historyFor
								? `No changes in #${name(historyFor)} yet.`
								: 'No slowmode changes yet. They appear here as Serenity adjusts channels.'}</EmptyState
					>
				{/if}
			</div>
		</section>

		<section class="pane s5" aria-labelledby="anT">
			<div class="ph"><h2 id="anT">Analytics</h2><span class="sp"></span><span class="dim" style="font-size:12px">last 24 h</span></div>
			{#if analytics}
				{@const hourly = analytics.hourly}
				{@const peak = Math.max(1, ...hourly.map((h) => h.messages))}
				{@const busy = hourly.filter((h) => h.messages)}
				<div class="pb stack">
					<div>
						<div class="rowline" style="margin-bottom:8px">
							<span class="fg2" style="font-size:12px">Messages per hour in this server’s channels</span>
							<span class="mono" style="font-size:12px">{hourly.reduce((a, h) => a + h.messages, 0).toLocaleString()} total</span>
						</div>
						<div class="bars" role="img" aria-label="Messages per hour over the last 24 hours, peak {peak}">
							{#each hourly as h, i (i)}
								<i
									class:now={i === hourly.length - 1}
									style:--h="{(h.messages / peak) * 100}%"
									title="{hourly.length - i}h ago · {h.messages} messages · {h.users} chatters · slowmode up to {fmt(h.max_slowmode)}"
								></i>
							{/each}
						</div>
						<div class="axis"><span>24h ago</span><span>12h</span><span>now</span></div>
					</div>
					<div class="tiles two">
						<div class="tile" title="Unique people per channel, added up across channels">
							<b>{Math.max(0, ...hourly.map((h) => h.users)).toLocaleString()}</b><span>Most chatters in an hour</span>
						</div>
						<div class="tile"><b>{fmt(busy.length ? Math.round(busy.reduce((a, h) => a + h.avg_slowmode, 0) / busy.length) : 0)}</b><span>Avg slowmode</span></div>
						<div class="tile"><b>{fmt(Math.max(0, ...hourly.map((h) => h.max_slowmode)))}</b><span>Max slowmode</span></div>
						<div class="tile"><b>{analytics.changes['24h'].toLocaleString()}</b><span>Changes, 24 h</span></div>
					</div>
					{#if analytics.effectiveness.length}
						{@const top = Math.max(...analytics.effectiveness.map((e) => Math.max(e.before, e.after))) * 1.1 || 1}
						<div>
							<div class="rowline" style="margin-bottom:6px">
								<span class="fg2" style="font-size:12px">Rate before → after slowmode, 30 days</span>
								<span class="legend">
									<span><i style="background:var(--amber);border-radius:50%;width:8px;height:8px"></i>before</span>
									<span><i style="background:var(--acc);border-radius:50%;width:8px;height:8px"></i>after</span>
								</span>
							</div>
							{#each analytics.effectiveness as e (e.channel_id)}
								<div class="db">
									<span class="chname" style="font-weight:400">#{name(e.channel_id)}</span>
									<span class="trk" aria-hidden="true">
										<span class="ln" style:left="{(Math.min(e.after, e.before) / top) * 100}%" style:width="{(Math.abs(e.before - e.after) / top) * 100}%"></span>
										<span class="dot" style:left="{(e.before / top) * 100}%" style:background="var(--amber)"></span>
										<span class="dot" style:left="{(e.after / top) * 100}%" style:background="var(--acc)"></span>
									</span>
									<span class="mono" style="text-align:right;font-size:12px">{e.before.toFixed(0)} → {e.after.toFixed(0)}</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{:else}
				<EmptyState>Loading…</EmptyState>
			{/if}
		</section>
	</div>
{/if}
