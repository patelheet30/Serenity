<script lang="ts">
	import { page } from '$app/state';
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Modal from '../../components/Modal.svelte';
	import Segmented from '../../components/Segmented.svelte';
	import Select from '../../components/Select.svelte';
	import { toast } from '../../toast.svelte';
	import type { TicketPageProps } from '../Tickets.svelte';

	type Type = { id: number; name: string; emoji: string | null; description: string | null };
	type Style = 'buttons' | 'menu';
	type Panel = {
		id: number;
		channel_id: string;
		message_id: string | null;
		url: string | null;
		title: string;
		description: string | null;
		style: Style;
		type_ids: number[];
	};
	type Pack = { key: string; name: string; blurb: string; types: Omit<Type, 'id'>[] };
	type Data = { panels: Panel[]; types: Type[]; packs: Pack[] };
	type Role = { id: string; name: string; managed: boolean };
	/** What the editor holds: a published panel (id) or a new one (id null). */
	type Draft = { id: number | null; channel_id: string | null; title: string; description: string; style: Style; type_ids: number[] };

	let { guildId, channels, onerror }: TicketPageProps = $props();

	const MAX_TYPES = 25; // as /ticket panel
	let data = $state<Data | null>(null);
	let roles = $state<Role[]>([]);
	let draft = $state<Draft | null>(null);
	let original = ''; // the draft as loaded, to tell whether it changed
	let pack = $state<string | null>(null); // the template pack picked for a new panel
	let staffRole = $state<string | null>(null);
	let adding = $state<string | null>(null);
	let busy = $state(false);
	let confirmDelete = $state(false);

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		draft = null;
		Promise.all([api<Data>(`/guilds/${id}/tickets/panels`), api<Role[]>(`/guilds/${id}/roles`)]).then(
			([d, r]) => !stale && ((data = d), (roles = r.filter((x) => !x.managed && x.id !== id))),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	// ?new=<pack> (the command palette's "new panel …") opens a new panel with that pack picked.
	const wanted = $derived(page.url.searchParams.get('new'));
	$effect(() => {
		if (data && wanted !== null) startNew(data.packs.some((p) => p.key === wanted) ? wanted : null);
	});

	const text = $derived(channels.filter((c) => c.type === 'text' || c.type === 'announcement'));
	const channelName = (id: string) => channels.find((c) => c.id === id)?.name ?? id;
	const typeOf = (id: number) => data?.types.find((t) => t.id === id);
	const shown = $derived(draft ? draft.type_ids.map(typeOf).filter((t) => t !== undefined) : []);
	const rows = $derived(Array.from({ length: Math.ceil(shown.length / 5) }, (_, i) => shown.slice(i * 5, i * 5 + 5)));
	const published = $derived(draft?.id != null ? data?.panels.find((p) => p.id === draft!.id) : undefined);
	const dirty = $derived(draft !== null && JSON.stringify(draft) !== original);
	const problem = $derived(
		!draft
			? ''
			: !draft.channel_id
				? 'Pick a channel to post it in.'
				: !draft.title.trim()
					? 'Give the panel a title.'
					: !draft.type_ids.length
						? 'Add at least one ticket type.'
						: ''
	);

	function edit(d: Draft) {
		draft = d;
		original = JSON.stringify(d);
		adding = null;
	}

	function open(p: Panel) {
		pack = null;
		edit({ id: p.id, channel_id: p.channel_id, title: p.title, description: p.description ?? '', style: p.style, type_ids: [...p.type_ids] });
	}

	function startNew(key: string | null) {
		pack = key;
		edit({ id: null, channel_id: null, title: '', description: '', style: 'buttons', type_ids: [] });
	}

	/** Create the pack's types (the server keeps ones it already has) and fill in its draft. */
	async function usePack() {
		const key = pack!;
		busy = true;
		const r = await save(() =>
			send<{ types: Type[]; panel: Omit<Draft, 'id' | 'channel_id'> }>('POST', `/guilds/${guildId}/tickets/packs/${key}`, { staff_role_id: staffRole })
		);
		busy = false;
		if (!r || !data || !draft) return;
		data.types = [...data.types.filter((t) => !r.types.some((n) => n.id === t.id)), ...r.types].sort((a, b) => a.id - b.id);
		Object.assign(draft, r.panel);
		pack = null;
		if (r.types.length) toast(`${r.types.length} ticket types ready. Edit their forms and staff in Types.`);
	}

	function move(i: number, by: number) {
		const ids = draft!.type_ids;
		[ids[i], ids[i + by]] = [ids[i + by], ids[i]];
	}

	async function publish() {
		if (!draft || problem) return;
		const body = { title: draft.title, description: draft.description, style: draft.style, type_ids: draft.type_ids };
		busy = true;
		const p = await save(() =>
			draft!.id == null
				? send<Panel>('POST', `/guilds/${guildId}/tickets/panels`, { ...body, channel_id: draft!.channel_id })
				: send<Panel>('PATCH', `/guilds/${guildId}/tickets/panels/${draft!.id}`, body)
		);
		busy = false;
		if (!p || !data) return;
		const isNew = !data.panels.some((x) => x.id === p.id);
		data.panels = isNew ? [...data.panels, p] : data.panels.map((x) => (x.id === p.id ? p : x));
		open(p);
		toast(isNew ? `Panel posted in #${channelName(p.channel_id)}` : 'Panel updated in Discord');
	}

	async function remove() {
		confirmDelete = false;
		const id = draft!.id!;
		if (!(await save(() => send('DELETE', `/guilds/${guildId}/tickets/panels/${id}`))) || !data) return;
		data.panels = data.panels.filter((p) => p.id !== id);
		draft = null;
		toast('Panel and its message deleted');
	}
</script>

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<div class="grid">
		<section class="pane s4" aria-labelledby="plT">
			<div class="ph">
				<h2 id="plT">Panels</h2>
				<span class="cnt">{data.panels.length}</span>
				<span class="sp"></span>
				<button class="btn sm" onclick={() => startNew(null)}><Icon name="plus" />New panel</button>
			</div>
			{#if data.panels.length}
				<ul class="mini-list">
					{#each data.panels as p (p.id)}
						<li>
							<button class="prow-b" aria-current={draft?.id === p.id ? 'true' : undefined} onclick={() => open(p)}>
								<span class="tx">
									<span class="nm">{p.title}</span>
									<span class="dim">#{channelName(p.channel_id)} · {p.type_ids.length} type{p.type_ids.length === 1 ? '' : 's'} · {p.style === 'menu' ? 'dropdown' : 'buttons'}</span>
								</span>
								{#if p.message_id}
									<span class="pill acc" title="Posted in #{channelName(p.channel_id)}"><Icon name="check" />posted</span>
								{:else}
									<span class="pill warn-t" title="Its message was deleted. Open it and save to post it again."><Icon name="warn" />not posted</span>
								{/if}
							</button>
						</li>
					{/each}
				</ul>
			{:else}
				<EmptyState icon="tickets">No panels yet. Start one from a template pack.</EmptyState>
			{/if}
		</section>

		<section class="pane s8" aria-labelledby="edT">
			<div class="ph">
				<h2 id="edT">{!draft ? 'Editor' : draft.id == null ? 'New panel' : `Panel #${draft.id}`}</h2>
				{#if published?.url}
					<span class="sp"></span>
					<a class="btn sm ghost" href={published.url} target="_blank" rel="noreferrer">Open in Discord <Icon name="ext" /></a>
				{/if}
			</div>
			{#if !draft}
				<EmptyState icon="tickets">Pick a panel to edit, or start a new one.</EmptyState>
			{:else}
				{#if draft.id == null}
					<div class="pb stack" style="border-bottom:1px solid var(--line)">
						<div class="field">
							<span id="pkL">Start from a template pack</span>
							<div class="packs" role="group" aria-labelledby="pkL">
								{#each data.packs as p (p.key)}
									<button class="pack" aria-pressed={pack === p.key} onclick={() => (pack = pack === p.key ? null : p.key)}>
										<b>{p.name}</b>
										<span class="fg2">{p.blurb}</span>
										{#if p.types.length}<span class="dim">{p.types.map((t) => `${t.emoji ?? ''} ${t.name}`).join(' · ')}</span>{/if}
									</button>
								{/each}
							</div>
						</div>
						{#if pack}
							{@const picked = data.packs.find((p) => p.key === pack)!}
							<div class="rowline" style="justify-content:flex-start;flex-wrap:wrap;gap:10px">
								{#if picked.types.length}
									<Select
										label="Staff role for the new types"
										placeholder="Staff role (optional)"
										bind:value={staffRole}
										options={roles.map((r) => ({ value: r.id, label: `@${r.name}` }))}
									/>
								{/if}
								<button class="btn pri" disabled={busy} onclick={usePack}>Use {picked.name}</button>
								<span class="hint">
									{picked.types.length
										? `Creates ${picked.types.length} ticket types with forms and stages (types you already have by name are kept).`
										: 'Fills in a starter title.'}
								</span>
							</div>
						{/if}
					</div>
				{/if}

				<div class="pe">
					<div class="pb stack">
						<div class="field">
							<span>Channel</span>
							{#if draft.id == null}
								<Select
									label="Channel"
									placeholder="Pick a channel"
									bind:value={draft.channel_id}
									options={text.map((c) => ({ value: c.id, label: `#${c.name}` }))}
								/>
							{:else}
								<span class="chname" style="font-size:13px"><Icon name="hash" />{channelName(draft.channel_id!)}</span>
								<p class="hint">A panel stays in its channel. To move it, delete it and publish a new one.</p>
							{/if}
						</div>
						<label class="field">
							<span>Title</span>
							<input class="inp" type="text" maxlength="256" placeholder="Open a ticket" bind:value={draft.title} />
						</label>
						<label class="field">
							<span>Description</span>
							<textarea class="inp" rows="3" maxlength="2000" placeholder="Text under the title (optional)" bind:value={draft.description}></textarea>
						</label>
						<div class="field">
							<span>Style</span>
							<Segmented
								label="Panel style"
								bind:value={draft.style}
								options={[
									{ value: 'buttons', label: 'Buttons' },
									{ value: 'menu', label: 'Dropdown' }
								]}
							/>
						</div>
						<div class="field">
							<span id="tyL">Ticket types <span class="cnt">{draft.type_ids.length}/{MAX_TYPES}</span></span>
							{#if shown.length}
								<ol class="tlist" aria-labelledby="tyL">
									{#each shown as t, i (t.id)}
										<li>
											<span class="nm">{t.emoji ?? ''} {t.name}</span>
											<button class="btn sm ghost" aria-label="Move {t.name} up" disabled={i === 0} onclick={() => move(i, -1)}>↑</button>
											<button class="btn sm ghost" aria-label="Move {t.name} down" disabled={i === shown.length - 1} onclick={() => move(i, 1)}>↓</button>
											<button class="btn sm ghost" aria-label="Remove {t.name}" onclick={() => (draft!.type_ids = draft!.type_ids.filter((x) => x !== t.id))}><Icon name="x" /></button>
										</li>
									{/each}
								</ol>
							{/if}
							{#if data.types.some((t) => !draft!.type_ids.includes(t.id)) && draft.type_ids.length < MAX_TYPES}
								<Select
									label="Add ticket type"
									placeholder="Add ticket type"
									bind:value={adding}
									options={data.types.filter((t) => !draft!.type_ids.includes(t.id)).map((t) => ({ value: String(t.id), label: `${t.emoji ?? ''} ${t.name}`.trim() }))}
									onchange={(v) => {
										draft!.type_ids = [...draft!.type_ids, Number(v)];
										adding = null;
									}}
								/>
							{:else if !data.types.length}
								<p class="hint">This server has no ticket types yet. Start from a template pack, or create them in Types.</p>
							{/if}
						</div>
					</div>

					<div class="pb">
						<div class="field"><span>Preview</span></div>
						<div class="dc" role="img" aria-label="Preview of the panel message in Discord">
							<img class="dc-av" src="/orb.svg" alt="" width="40" height="40" />
							<div class="dc-msg">
								<div class="dc-head"><b>Serenity</b><span class="dc-app">APP</span><span class="dc-time">Today at {new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span></div>
								<div class="dc-embed">
									<div class="dc-title">{draft.title.trim() || 'Untitled panel'}</div>
									{#if draft.description.trim()}<div class="dc-desc">{draft.description}</div>{/if}
								</div>
								{#if !shown.length}
									<div class="dc-none">Add a ticket type to show its {draft.style === 'menu' ? 'option' : 'button'}.</div>
								{:else if draft.style === 'menu'}
									<div class="dc-select"><span>Choose what you need help with</span><Icon name="chev" /></div>
									<ul class="dc-opts">
										{#each shown as t (t.id)}
											<li>
												{#if t.emoji}<span class="em">{t.emoji}</span>{/if}
												<span><b>{t.name}</b>{#if t.description}<span>{t.description}</span>{/if}</span>
											</li>
										{/each}
									</ul>
								{:else}
									{#each rows as row, r (r)}
										<div class="dc-row">
											{#each row as t (t.id)}<span class="dc-btn">{#if t.emoji}<span>{t.emoji}</span>{/if}{t.name}</span>{/each}
										</div>
									{/each}
								{/if}
							</div>
						</div>
						<p class="hint" style="margin-top:8px">Buttons sit five to a row; a dropdown shows each type’s description. A “Support” line appears under the description when you set support hours.</p>
					</div>
				</div>

				<div class="pb acts-b">
					{#if draft.id != null}
						<button class="btn danger" onclick={() => (confirmDelete = true)}>Delete</button>
					{/if}
					<span class="sp"></span>
					{#if problem}<span class="hint">{problem}</span>{/if}
					{#if draft.id == null || dirty}
						<button class="btn" onclick={() => (draft!.id == null ? (draft = null) : open(published!))}>Discard</button>
					{/if}
					<button class="btn pri" disabled={busy || !!problem || (draft.id != null && !dirty && !!published?.message_id)} onclick={publish}>
						{draft.id == null ? `Publish${draft.channel_id ? ` to #${channelName(draft.channel_id)}` : ''}` : published?.message_id ? 'Update message' : 'Post again'}
					</button>
				</div>
			{/if}
		</section>
	</div>
{/if}

<Modal bind:open={confirmDelete} title="Delete this panel?">
	<p>Its message in #{draft?.channel_id ? channelName(draft.channel_id) : ''} is deleted too. Ticket types and open tickets stay.</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (confirmDelete = false)}>Cancel</button>
		<button class="btn danger" onclick={remove}>Delete panel</button>
	{/snippet}
</Modal>

<style>
	.mini-list li {
		padding: 0;
	}
	.prow-b {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: 10px;
		text-align: left;
		padding: 10px 14px;
		background: none;
		border: 0;
		color: inherit;
		cursor: pointer;
	}
	.prow-b .tx {
		flex: 1;
		min-width: 0;
		display: grid;
		gap: 2px;
	}
	.prow-b .nm {
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.prow-b .dim {
		font-size: 12px;
	}
	.prow-b:hover,
	.prow-b[aria-current='true'] {
		background: var(--ink3);
	}
	.mini-list .pill :global(svg) {
		width: 11px;
		height: 11px;
	}
	.warn-t {
		color: var(--warn);
	}
	.packs {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
		gap: 8px;
	}
	.pack {
		display: grid;
		gap: 4px;
		align-content: start;
		text-align: left;
		padding: 10px 12px;
		border-radius: var(--r);
		border: 1px solid var(--line2);
		background: var(--ink);
		color: var(--fg);
		font-size: 12.5px;
		cursor: pointer;
	}
	.pack b {
		font-size: 13.5px;
		font-weight: 600;
	}
	.pack:hover {
		border-color: var(--line3);
	}
	.pack[aria-pressed='true'] {
		border-color: var(--acc);
		background: var(--accbg);
	}
	.pe {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
	}
	@media (max-width: 1100px) {
		.pe {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	.tlist {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: var(--r);
	}
	.tlist li {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 4px 4px 4px 10px;
		font-size: 13px;
	}
	.tlist li + li {
		border-top: 1px solid var(--line);
	}
	.tlist .nm {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.acts-b {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		border-top: 1px solid var(--line);
	}
	.acts-b .sp {
		flex: 1;
	}

	/* The panel as Discord shows it (dark theme), mirroring TicketService.build_panel. */
	.dc {
		display: flex;
		gap: 14px;
		padding: 14px 16px;
		border-radius: var(--r);
		background: #313338;
		color: #dbdee1;
		font-family: 'gg sans', 'Noto Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif;
		font-size: 15px;
		line-height: 1.375;
		overflow: hidden;
	}
	.dc-av {
		flex: none;
		border-radius: 50%;
		background: #1e1f22;
	}
	.dc-msg {
		min-width: 0;
		flex: 1;
		display: grid;
		gap: 6px;
		justify-items: start;
	}
	.dc-head {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.dc-head b {
		color: #f2f3f5;
		font-weight: 500;
	}
	.dc-app {
		background: #5865f2;
		color: #fff;
		font-size: 10px;
		font-weight: 600;
		padding: 0 4px;
		border-radius: 3px;
		line-height: 15px;
	}
	.dc-time {
		color: #949ba4;
		font-size: 12px;
	}
	.dc-embed {
		max-width: 432px;
		width: 100%;
		display: grid;
		gap: 8px;
		padding: 10px 16px 14px 12px;
		border-left: 4px solid #5865f2;
		border-radius: 4px;
		background: #2b2d31;
		overflow-wrap: anywhere;
	}
	.dc-title {
		color: #f2f3f5;
		font-weight: 600;
		font-size: 16px;
	}
	.dc-desc {
		font-size: 14px;
		white-space: pre-wrap;
	}
	.dc-row {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.dc-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 16px;
		border-radius: 8px;
		background: #5865f2;
		color: #fff;
		font-size: 14px;
		font-weight: 500;
		white-space: nowrap;
	}
	.dc-select {
		max-width: 400px;
		width: 100%;
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 40px;
		padding: 0 12px;
		border-radius: 8px;
		border: 1px solid #1e1f22;
		background: #1e1f22;
		color: #949ba4;
		font-size: 14px;
	}
	.dc-opts {
		max-width: 400px;
		width: 100%;
		margin: -4px 0 0;
		padding: 4px 0;
		list-style: none;
		border-radius: 0 0 8px 8px;
		background: #2b2d31;
		font-size: 14px;
	}
	.dc-opts li {
		display: flex;
		gap: 8px;
		align-items: center;
		padding: 6px 12px;
	}
	.dc-opts li > span:last-child {
		display: grid;
	}
	.dc-opts b {
		color: #f2f3f5;
		font-weight: 500;
	}
	.dc-opts .em {
		font-size: 18px;
	}
	.dc-opts li span span {
		color: #b5bac1;
		font-size: 13px;
	}
	.dc-none {
		color: #949ba4;
		font-size: 13px;
		font-style: italic;
	}
</style>
