<script lang="ts">
	import { api, send, type Modules } from '../api';
	import { save } from '../autosave.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import Toggle from '../components/Toggle.svelte';
	import type { IconName } from '../icons';
	import { toast } from '../toast.svelte';

	let { guildId, onerror }: { guildId: string; onerror: (e: unknown) => void } = $props();

	const MODULES: { k: string; icon: IconName; title: string; desc: string }[] = [
		{ k: 'slowmode', icon: 'slowmode', title: 'Slowmode', desc: 'Scores each configured channel every interval and adjusts slowmode up or down.' },
		{ k: 'logging', icon: 'logging', title: 'Logging', desc: 'Posts member, message, voice, server and mod events to your log channels.' },
		{ k: 'moderation', icon: 'cases', title: 'Moderation', desc: 'Numbered cases for bans, kicks, timeouts and warnings, with per-user history.' }
	];
	const title = (k: string) => MODULES.find((m) => m.k === k)?.title ?? k;

	let data: Modules | null = $state(null);
	// An inline explanation under one module: a missing dependency, or a dependent still on.
	let note: { k: string; kind: 'need' | 'block'; other: string[] } | null = $state(null);

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		api<Modules>(`/guilds/${id}/modules`).then((m) => !stale && (data = m), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	const dependents = (k: string) =>
		Object.keys(data!.requires).filter((m) => data!.requires[m].includes(k) && data!.enabled[m]);

	async function put(k: string, on: boolean): Promise<boolean> {
		const r = await save(() => send<Modules>('PUT', `/guilds/${guildId}/modules/${k}`, { enabled: on }));
		if (r) data = r;
		return !!r;
	}

	async function set(k: string, on: boolean) {
		const d = data!;
		note = null;
		const missing = on ? d.requires[k].filter((dep) => !d.enabled[dep]) : [];
		const blocking = on ? [] : dependents(k);
		if (missing.length || blocking.length) {
			d.enabled[k] = !on; // the server would refuse; explain instead
			note = { k, kind: missing.length ? 'need' : 'block', other: missing.length ? missing : blocking };
			return;
		}
		// Revert on the current data: a concurrent write may have replaced `d` meanwhile.
		if (!(await put(k, on))) return void (data!.enabled[k] = !on);
		toast(`${title(k)} turned ${on ? 'on' : 'off'}`, { undo: () => set(k, !on) });
	}

	async function enableWithDeps(k: string, deps: string[]) {
		note = null;
		for (const m of [...deps, k]) if (!(await put(m, true))) return;
		toast(`${[...deps, k].map(title).join(' and ')} turned on`);
	}
</script>

<div class="phead"><div><h1>Modules</h1><p>Turn whole features on or off. Settings are kept when a module is off.</p></div></div>

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<section class="pane mods" aria-label="Modules">
		{#each MODULES as m (m.k)}
			{@const deps = data.requires[m.k] ?? []}
			{@const usedBy = dependents(m.k)}
			<div class="mod" class:on={data.enabled[m.k]}>
				<span class="ic"><Icon name={m.icon} /></span>
				<div>
					<h3>{m.title}</h3>
					<p>{m.desc}</p>
					{#if usedBy.length}
						<span class="dep"><Icon name="lock" style="width:12px;height:12px" />required by {usedBy.map(title).join(', ')}</span>
					{:else if deps.length}
						<span class="dep"><Icon name="arrow" style="width:12px;height:12px" />requires {deps.map(title).join(', ')}</span>
					{/if}
				</div>
				<Toggle
					label="{m.title} module"
					bind:checked={data.enabled[m.k]}
					disabled={data.enabled[m.k] && usedBy.length > 0}
					onblocked={() => (note = { k: m.k, kind: 'block', other: usedBy })}
					onchange={(on) => set(m.k, on)}
				/>
				{#if note?.k === m.k}
					<div class="note" role="alert">
						{#if note.kind === 'need'}
							<Icon name="warn" />
							<span style="flex:1">{m.title} needs {note.other.map(title).join(', ')} on first.</span>
							<button class="btn sm pri" onclick={() => enableWithDeps(m.k, note!.other)}>
								Enable {[...note.other, m.k].map(title).join(' + ')}
							</button>
						{:else}
							<Icon name="lock" />
							<span style="flex:1">{m.title} can’t be turned off while {note.other.map(title).join(', ')} is on. Turn that off first.</span>
						{/if}
						<button class="btn sm ghost" onclick={() => (note = null)}>OK</button>
					</div>
				{/if}
			</div>
		{/each}
	</section>
{/if}
