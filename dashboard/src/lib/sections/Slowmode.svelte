<script lang="ts">
	import { api, send, type Channel, type Modules } from '../api';
	import { save } from '../autosave.svelte';
	import DataTable from '../components/DataTable.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import InlineEdit from '../components/InlineEdit.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { registerCommands } from '../palette.svelte';
	import { toast } from '../toast.svelte';

	type Row = { id: string; enabled: boolean; threshold: number | null; slowmode: number | null };
	type Slowmode = { enabled: boolean; default_threshold: number; interval: number; channels: Row[] };

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

	const name = (id: string) => channels.find((c) => c.id === id)?.name ?? id;
	const text = $derived(channels.filter((c) => c.type === 'text'));
	const available = $derived(text.filter((c) => !data?.channels.some((r) => r.id === c.id)));
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
						onchange={(v) => patch({ interval: v })}
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
						{ key: 'threshold', label: 'Threshold' },
						{ key: 'slowmode', label: 'Slowmode' },
						{ key: 'enabled', label: 'Auto' },
						{ key: 'actions', label: '', align: 'right' }
					]}
				>
					{#snippet cell(row, c)}
						{#if c.key === 'name'}
							<span class="chname"><Icon name="hash" />{name(row.id)}</span>
						{:else if c.key === 'threshold'}
							<InlineEdit
								label="threshold for #{name(row.id)}, blank uses the default"
								value={row.threshold?.toString() ?? ''}
								placeholder="default {data!.default_threshold}"
								onsave={(v) => setThreshold(row, v)}
							/>
						{:else if c.key === 'slowmode'}
							<span class="sm-val" class:on={row.slowmode}>{fmt(row.slowmode)}</span>
						{:else if c.key === 'enabled'}
							<Toggle
								label="Auto slowmode for #{name(row.id)}"
								bind:checked={row.enabled}
								onchange={async (on) => !(await putChannel(row.id, { enabled: on })) && (row.enabled = !on)}
							/>
						{:else}
							<button class="btn sm ghost" aria-label="Remove #{name(row.id)}" onclick={() => remove(row)}><Icon name="x" /></button>
						{/if}
					{/snippet}
				</DataTable>
			{:else}
				<EmptyState icon="slowmode">No channels yet. Add one and Serenity starts managing its slowmode.</EmptyState>
			{/if}
		</section>
	</div>
{/if}
