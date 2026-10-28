<script lang="ts">
	import { api, send, type Channel, type Modules } from '../api';
	import { save } from '../autosave.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { registerCommands } from '../palette.svelte';
	import { toast } from '../toast.svelte';
	import { MOD } from '../util';

	type LogType = { type: string; channel_id: string | null; enabled: boolean };
	type Ignore = { target_type: 'user' | 'channel'; target_id: string; log_type: string; name: string | null };
	type Logging = { types: LogType[]; ignores: Ignore[]; failed?: string[] };
	type Target = { target_type: 'user' | 'channel'; target_id: string; name: string | null };
	type Member = { id: string; username: string; display_name: string };

	let {
		guildId,
		channels,
		onerror,
		onchannels
	}: { guildId: string; channels: Channel[]; onerror: (e: unknown) => void; onchannels: () => void } = $props();

	const TYPES: Record<string, { n: string; d: string }> = {
		member: { n: 'Member', d: 'Joins, leaves, nickname and role changes' },
		message: { n: 'Message', d: 'Edits, deletes and bulk deletes' },
		voice: { n: 'Voice', d: 'Joins, leaves and moves' },
		server: { n: 'Server', d: 'Channels, roles, emoji, settings and dashboard changes' },
		mod: { n: 'Mod', d: 'Every moderation case' },
		ticket: { n: 'Ticket', d: 'Tickets opened, claimed, closed and reopened' }
	};

	let data: Logging | null = $state(null);
	// The log types this server has (the ticket log only where Tickets is available).
	const KEYS: string[] = $derived(
		(data as Logging | null)?.types.map((t) => t.type) ?? Object.keys(TYPES)
	);
	let modules: Modules | null = $state(null);
	let settingUp = $state(false);
	// Targets added this visit stay listed even when every box is cleared, so they can be re-ticked.
	let pinned: Target[] = $state([]);
	let filter = $state('');
	let addKind: 'channel' | 'user' = $state('channel');
	let addTarget: string | null = $state(null);
	let addScope = $state('*');
	let members: Member[] = $state([]);

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		modules = null;
		pinned = [];
		Promise.all([api<Logging>(`/guilds/${id}/logging`), api<Modules>(`/guilds/${id}/modules`)]).then(
			([l, m]) => !stale && ((data = l), (modules = m)),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	const postable = $derived(channels.filter((c) => c.type === 'text' || c.type === 'announcement'));
	const ignorable = $derived(channels.filter((c) => c.type !== 'category'));
	const channelName = (id: string | null) => channels.find((c) => c.id === id)?.name;
	const same = (a: Target, b: Target) => a.target_type === b.target_type && a.target_id === b.target_id;
	const label = (t: Target) =>
		t.target_type === 'channel' ? `#${channelName(t.target_id) ?? t.name ?? t.target_id}` : `@${t.name ?? t.target_id}`;

	const targets = $derived.by(() => {
		const all: Target[] = [];
		for (const t of [...(data?.ignores ?? []), ...pinned]) if (!all.some((x) => same(x, t))) all.push(t);
		const q = filter.trim().toLowerCase().replace(/^[#@]/, '');
		return all.filter((t) => label(t).toLowerCase().includes(q));
	});
	const has = (t: Target, scope: string) => !!data?.ignores.some((i) => same(i, t) && i.log_type === scope);

	async function putType(type: string, body: { channel_id?: string; enabled?: boolean }) {
		const r = await save(() => send<Logging>('PUT', `/guilds/${guildId}/logging/${type}`, body));
		if (r) data = r;
		return r;
	}

	async function setup() {
		settingUp = true;
		const r = await save(() => send<Logging>('POST', `/guilds/${guildId}/logging/setup`));
		settingUp = false;
		if (!r) return;
		data = r;
		onchannels();
		if (r.failed?.length) toast(`Couldn’t create ${r.failed.join(', ')}`, { error: true });
		else toast('Log channels ready');
	}

	async function ignore(t: Target, scope: string) {
		const r = await save(() => send<Logging>('POST', `/guilds/${guildId}/logging/ignores`, { ...t, log_type: scope }));
		if (r) data = r;
		return r;
	}

	async function unignore(t: Target, scope?: string) {
		const q = new URLSearchParams({ target_type: t.target_type, target_id: t.target_id });
		if (scope) q.set('log_type', scope);
		const r = await save(() => send<Logging>('DELETE', `/guilds/${guildId}/logging/ignores?${q}`));
		if (r) data = r;
		return r;
	}

	async function toggle(t: Target, scope: string) {
		if (!pinned.some((p) => same(p, t))) pinned.push(t);
		await (has(t, scope) ? unignore(t, scope) : ignore(t, scope));
	}

	async function removeTarget(t: Target) {
		const scopes = data!.ignores.filter((i) => same(i, t)).map((i) => i.log_type);
		if (scopes.length && !(await unignore(t))) return;
		pinned = pinned.filter((p) => !same(p, t));
		if (scopes.length)
			toast(`${label(t)} is logged again`, {
				undo: async () => {
					for (const s of scopes) await ignore(t, s);
				}
			});
	}

	async function add(e: SubmitEvent) {
		e.preventDefault();
		if (!addTarget) return;
		const name = addKind === 'user' ? (members.find((m) => m.id === addTarget)?.username ?? null) : null;
		const t: Target = { target_type: addKind, target_id: addTarget, name };
		if (await ignore(t, addScope)) {
			if (!pinned.some((p) => same(p, t))) pinned.push(t);
			addTarget = null;
		}
	}

	async function searchMembers(q: string) {
		try {
			members = await api<Member[]>(`/guilds/${guildId}/members?query=${encodeURIComponent(q)}`);
		} catch (e) {
			onerror(e);
		}
	}

	$effect(() =>
		registerCommands([
			{
				id: 'logging:ignore',
				group: 'Templates',
				title: 'ignore #channel for …',
				icon: 'logging',
				keywords: 'logging ignore',
				template: 'ignore #',
				parse: (q) => {
					const m = /^ignore #?([\w-]+)(?: for (all|\w+))?$/i.exec(q);
					const ch = m && ignorable.find((c) => c.name === m[1]);
					const scope = m?.[2] && m[2] !== 'all' ? m[2].toLowerCase() : '*';
					if (!ch || (scope !== '*' && !KEYS.includes(scope))) return null;
					const t: Target = { target_type: 'channel', target_id: ch.id, name: ch.name };
					return {
						label: `Ignore #${ch.name} in ${scope === '*' ? 'every log' : `${TYPES[scope].n} logs`}`,
						run: () => ignore(t, scope).then((r) => r && toast(`#${ch.name} ignored`))
					};
				}
			}
		])
	);
</script>

<div class="phead">
	<div><h1>Logging</h1><p>Events are posted as embeds to the channels below. Serenity stores none of it.</p></div>
	{#if modules && !modules.enabled.logging}
		<button
			class="btn"
			onclick={async () => {
				const m = await save(() => send<Modules>('PUT', `/guilds/${guildId}/modules/logging`, { enabled: true }));
				if (m) modules = m;
			}}><Icon name="warn" />Logging module is off · turn on</button
		>
	{/if}
</div>

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	{@const ready = data.types.every((t) => t.channel_id)}
	<div class="grid">
		<section class="pane s12" aria-labelledby="suT">
			<div class="pb setup">
				<div>
					<h2 id="suT" style="font-size:15px;margin:0 0 4px;font-family:var(--disp);letter-spacing:-.01em">One-click setup</h2>
					<p class="hint" style="font-size:13px;margin-bottom:10px">
						Creates a staff-only category with one channel per log type and routes everything to it. Log types that already have a channel keep it.
					</p>
					<pre class="tree" aria-label="Channels that will be created">📋 Serenity Logs
{KEYS.map((k, i) => `${i === KEYS.length - 1 ? '└' : '├'}── #${k}-log`).join('\n')}</pre>
				</div>
				<div style="display:grid;gap:8px;justify-items:end">
					<button class="btn" class:pri={!ready} disabled={settingUp} onclick={setup}>
						<Icon name={ready ? 'check' : 'run'} />{settingUp ? 'Setting up…' : ready ? 'Set up · run again' : 'Run setup'}
					</button>
					<span class="hint">{ready ? 'Every log type has a channel. Re-running is safe.' : 'Needs Manage Channels.'}</span>
				</div>
			</div>
		</section>

		<section class="pane s6" aria-labelledby="ltT">
			<div class="ph"><h2 id="ltT">Log types</h2><span class="cnt">{data.types.filter((t) => t.enabled).length}/{KEYS.length} on</span></div>
			<div class="scroll">
				<table class="tbl">
					<thead><tr><th>Type</th><th>Channel</th><th>On</th></tr></thead>
					<tbody>
						{#each data.types as t (t.type)}
							<tr>
								<td>
									<div style="font-weight:500">{TYPES[t.type].n}</div>
									<div class="dim" style="font-size:12px;white-space:normal">{TYPES[t.type].d}</div>
								</td>
								<td>
									<Select
										label="{TYPES[t.type].n} log channel"
										placeholder={t.channel_id ? `#${t.channel_id}` : 'Choose channel…'}
										value={t.channel_id}
										options={postable.map((c) => ({ value: c.id, label: `#${c.name}` }))}
										onchange={(id) => putType(t.type, { channel_id: id })}
									/>
								</td>
								<td>
									<Toggle
										label="{TYPES[t.type].n} logging"
										bind:checked={t.enabled}
										disabled={!t.channel_id}
										onblocked={() => toast(`Pick a channel for ${TYPES[t.type].n} logs first`)}
										onchange={async (on) => !(await putType(t.type, { enabled: on })) && (t.enabled = !on)}
									/>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<section class="pane s6" aria-labelledby="igT">
			<div class="ph">
				<h2 id="igT">Ignore list</h2>
				<span class="cnt">{targets.length}</span>
				<span class="sp"></span>
				<div class="search" style="width:180px">
					<Icon name="search" /><input class="inp" type="search" aria-label="Filter ignore list" placeholder="Filter…" bind:value={filter} style="height:28px" />
				</div>
			</div>
			<div class="scroll" style="max-height:380px">
				<table class="tbl mx">
					<thead>
						<tr>
							<th>Target</th><th class="c">All</th>
							{#each KEYS as k (k)}<th class="c">{TYPES[k].n}</th>{/each}
							<th><span class="sr">Remove</span></th>
						</tr>
					</thead>
					<tbody>
						{#each targets as t (`${t.target_type}:${t.target_id}`)}
							{@const all = has(t, '*')}
							<tr>
								<td><span class="chname"><Icon name={t.target_type === 'channel' ? 'hash' : 'user'} />{label(t).slice(1)}</span></td>
								<td class="c">
									<button class="cellb" aria-pressed={all} aria-label="Ignore {label(t)} in every log" onclick={() => toggle(t, '*')}>
										{#if all}<Icon name="check" />{/if}
									</button>
								</td>
								{#each KEYS as k (k)}
									{@const on = has(t, k)}
									<td class="c">
										<button
											class="cellb"
											class:cov={all && !on}
											aria-pressed={on}
											aria-label="Ignore {label(t)} in {TYPES[k].n} logs{all ? ' (covered by All)' : ''}"
											disabled={all}
											title={all ? 'Covered by All' : undefined}
											onclick={() => toggle(t, k)}
										>
											{#if on || all}<Icon name="check" />{/if}
										</button>
									</td>
								{/each}
								<td class="r"><button class="btn sm ghost" aria-label="Stop ignoring {label(t)}" onclick={() => removeTarget(t)}><Icon name="x" /></button></td>
							</tr>
						{:else}
							<tr><td colspan={KEYS.length + 3}><EmptyState>{filter ? 'No ignored targets match.' : 'Nothing ignored. Every channel and user is logged.'}</EmptyState></td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			<form class="addrow" onsubmit={add}>
				<div class="field">
					<span>Add</span>
					<Segmented
						label="Ignore a channel or a user"
						bind:value={addKind}
						options={[{ value: 'channel', label: 'Channel' }, { value: 'user', label: 'User' }]}
						onchange={() => (addTarget = null)}
					/>
				</div>
				<div class="field" style="flex:1;min-width:130px">
					<span>{addKind === 'channel' ? 'Channel' : 'User'}</span>
					{#if addKind === 'channel'}
						<Select label="Channel to ignore" placeholder="Choose…" bind:value={addTarget} options={ignorable.map((c) => ({ value: c.id, label: `#${c.name}` }))} />
					{:else}
						<Select
							label="User to ignore"
							placeholder="Search members…"
							bind:value={addTarget}
							options={members.map((m) => ({ value: m.id, label: m.display_name, hint: m.username }))}
							onsearch={searchMembers}
						/>
					{/if}
				</div>
				<label class="field">
					<span>Scope</span>
					<select class="inp" bind:value={addScope}>
						<option value="*">All types</option>
						{#each KEYS as k (k)}<option value={k}>{TYPES[k].n}</option>{/each}
					</select>
				</label>
				<button class="btn pri" type="submit" disabled={!addTarget}><Icon name="plus" />Ignore</button>
			</form>
			<div class="pb legend" style="padding-top:0">
				<span><i style="background:var(--acc)"></i>ignored</span>
				<span><i style="border:1px dashed var(--accln)"></i>covered by All</span>
				<span>Tip: <span class="mono">ignore #bots for message</span> in {MOD}K</span>
			</div>
		</section>
	</div>
{/if}
