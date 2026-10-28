<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import DataTable from '../../components/DataTable.svelte';
	import Drawer from '../../components/Drawer.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Modal from '../../components/Modal.svelte';
	import Select from '../../components/Select.svelte';
	import { toast } from '../../toast.svelte';
	import { ago } from '../../util';
	import type { TicketPageProps } from '../Tickets.svelte';

	type Delivery = { event: string; attempts: number; status: number | null; latency_ms: number | null; error: string | null };
	type Webhook = { id: number; url: string; events: string[]; type_id: number | null; created_at: number; last?: (Delivery & { at: number }) | null };
	type Logged = Delivery & { id: number; created_at: number };
	type TicketType = { id: number; name: string; emoji: string | null };

	let { guildId, onerror }: TicketPageProps = $props();

	let data = $state<{ webhooks: Webhook[]; events: string[]; max: number } | null>(null);
	let types = $state<TicketType[]>([]);
	let version = $state(0); // bump to reload the list after a write

	$effect(() => {
		const id = guildId;
		version;
		let stale = false;
		Promise.all([api<typeof data>(`/guilds/${id}/tickets/webhooks`), api<TicketType[]>(`/guilds/${id}/tickets/types`)]).then(
			([w, t]) => {
				if (stale) return;
				data = w;
				types = t;
				if (selected === null && w!.webhooks.length) selected = w!.webhooks[0].id;
			},
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	const short = (e: string) => e.replace(/^ticket\./, '');
	const typeName = (id: number | null) => (id === null ? 'All types' : (types.find((t) => t.id === id)?.name ?? 'Deleted type'));

	// The delivery log of the selected webhook.
	let selected = $state<number | null>(null);
	let log = $state<Logged[] | null>(null);
	let logVersion = $state(0);
	$effect(() => {
		const id = selected;
		logVersion;
		log = null;
		if (id === null) return;
		let stale = false;
		api<Logged[]>(`/guilds/${guildId}/tickets/webhooks/${id}/deliveries`).then(
			(r) => !stale && (log = r),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	// Add and edit share one drawer; `editing` is null when adding.
	let drawer = $state(false);
	let editing = $state<Webhook | null>(null);
	let url = $state('');
	let events = $state<string[]>([]);
	let scope = $state(''); // a type id, or '' for every type
	let busy = $state(false);

	function edit(w: Webhook | null) {
		editing = w;
		url = w?.url ?? '';
		events = [...(w?.events ?? [])];
		scope = w?.type_id ? String(w.type_id) : '';
		drawer = true;
	}

	async function submit(e: Event) {
		e.preventDefault();
		busy = true;
		const body = { url: url.trim(), events, type_id: scope ? Number(scope) : null };
		const base = `/guilds/${guildId}/tickets/webhooks`;
		const r = await save(() =>
			editing ? send<Webhook>('PATCH', `${base}/${editing.id}`, body) : send<Webhook & { secret: string }>('POST', base, body)
		);
		busy = false;
		if (!r) return;
		drawer = false;
		if (!editing) {
			selected = r.id;
			reveal(r.id, (r as Webhook & { secret: string }).secret);
		} else toast(`Webhook #${r.id} saved`);
		version++;
	}

	// Remove and rotate ask first; the secret from add or rotate is shown once.
	let confirm = $state<{ kind: 'remove' | 'rotate'; hook: Webhook } | null>(null);
	let confirmOpen = $state(false);
	let secret = $state<{ id: number; value: string } | null>(null);
	let secretOpen = $state(false);
	$effect(() => {
		if (!secretOpen) secret = null; // don't keep it around once dismissed
	});

	function ask(kind: 'remove' | 'rotate', hook: Webhook) {
		confirm = { kind, hook };
		confirmOpen = true;
	}

	function reveal(id: number, value: string) {
		secret = { id, value };
		secretOpen = true;
	}

	async function confirmed() {
		if (!confirm) return;
		const { kind, hook } = confirm;
		confirmOpen = false;
		const path = `/guilds/${guildId}/tickets/webhooks/${hook.id}`;
		if (kind === 'remove') {
			if (!(await save(() => send('DELETE', path)))) return;
			toast(`Removed webhook #${hook.id}`);
			if (selected === hook.id) selected = null;
			version++;
		} else {
			const r = await save(() => send<{ secret: string }>('POST', `${path}/rotate`));
			if (r) reveal(hook.id, r.secret);
		}
	}

	let testing = $state<number | null>(null);
	async function test(w: Webhook) {
		testing = w.id;
		const r = await save(() => send<{ ok: boolean; error: string | null }>('POST', `/guilds/${guildId}/tickets/webhooks/${w.id}/test`));
		testing = null;
		if (!r) return;
		toast(r.ok ? `Webhook #${w.id} accepted the test event` : `Webhook #${w.id} didn’t accept the test event: ${r.error}`, { error: !r.ok });
		selected = w.id;
		logVersion++;
		version++;
	}

	async function copy() {
		try {
			await navigator.clipboard.writeText(secret!.value);
			toast('Secret copied');
		} catch {
			toast('Couldn’t copy; select the secret and copy it by hand', { error: true });
		}
	}
</script>

{#snippet outcome(d: Delivery)}
	{#if d.error === null}
		<span class="pill" style="color:var(--lilac)"><Icon name="check" />{d.status}</span>
	{:else}
		<span class="pill" style="color:var(--red)" title={d.error}><Icon name="warn" />{d.status ?? 'failed'}</span>
	{/if}
{/snippet}

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	{@const hooks = data.webhooks}
	{@const full = hooks.length >= data.max}
	<div class="grid">
		<section class="pane s12" aria-labelledby="whT">
			<div class="ph">
				<h2 id="whT">Webhooks</h2>
				<span class="cnt">{hooks.length}/{data.max}</span>
				<span class="sp"></span>
				<button class="btn sm pri" disabled={full} title={full ? `A server can have at most ${data.max} webhooks.` : undefined} onclick={() => edit(null)}>
					<Icon name="plus" />Add webhook
				</button>
			</div>
			{#if !hooks.length}
				<EmptyState icon="ext">No webhooks yet. Add one to POST ticket events, signed, to your own systems.</EmptyState>
			{:else}
				<DataTable
					label="Webhooks"
					rows={hooks}
					key={(w) => String(w.id)}
					selected={selected === null ? null : String(selected)}
					onopen={(w) => (selected = w.id)}
					columns={[
						{ key: 'url', label: 'URL' },
						{ key: 'events', label: 'Events' },
						{ key: 'scope', label: 'Tickets' },
						{ key: 'last', label: 'Last delivery' },
						{ key: 'acts', label: '', align: 'right' }
					]}
				>
					{#snippet cell(w, col)}
						{#if col.key === 'url'}
							<span class="mono" title={w.url} style="display:block;max-width:min(320px,45vw);overflow:hidden;text-overflow:ellipsis"><span class="dim">#{w.id}</span> {w.url}</span>
						{:else if col.key === 'events'}
							<span class="fg2" title={w.events.map(short).join(', ')} style="display:block;max-width:240px;overflow:hidden;text-overflow:ellipsis">{w.events.length ? w.events.map(short).join(', ') : 'All events'}</span>
						{:else if col.key === 'scope'}
							{typeName(w.type_id)}
						{:else if col.key === 'last'}
							{#if w.last}
								<span style="display:inline-flex;gap:8px;align-items:center">{@render outcome(w.last)}<span class="mono dim" title={new Date(w.last.at * 1000).toLocaleString()}>{short(w.last.event)} · {ago(w.last.at * 1000)}</span></span>
							{:else}
								<span class="dim">Nothing sent yet</span>
							{/if}
						{:else}
							<span style="display:inline-flex;gap:4px">
								<button class="btn sm" disabled={testing !== null} onclick={() => test(w)}><Icon name="run" />{testing === w.id ? 'Sending…' : 'Test'}</button>
								<button class="btn sm ghost" aria-label="Edit webhook #{w.id}" onclick={() => edit(w)}><Icon name="edit" /></button>
								<button class="btn sm ghost" aria-label="Rotate the secret of webhook #{w.id}" title="Rotate secret" onclick={() => ask('rotate', w)}><Icon name="key" /></button>
								<button class="btn sm ghost danger" aria-label="Remove webhook #{w.id}" onclick={() => ask('remove', w)}><Icon name="x" /></button>
							</span>
						{/if}
					{/snippet}
				</DataTable>
			{/if}
		</section>

		{#if selected !== null && hooks.some((w) => w.id === selected)}
			<section class="pane s12" aria-labelledby="dlT">
				<div class="ph">
					<h2 id="dlT">Deliveries · webhook #{selected}</h2>
					{#if log}<span class="cnt">{log.length}</span>{/if}
					<span class="sp"></span>
					<span class="hint hide-s">Last 50, kept 7 days</span>
					<button class="btn sm ghost" aria-label="Refresh deliveries" onclick={() => logVersion++}><Icon name="undo" />Refresh</button>
				</div>
				{#if !log}
					<EmptyState>Loading…</EmptyState>
				{:else if !log.length}
					<EmptyState>Nothing sent yet. Send a test event to check the receiver.</EmptyState>
				{:else}
					<DataTable
						label="Deliveries for webhook #{selected}"
						rows={log}
						key={(d) => String(d.id)}
						maxHeight="420px"
						columns={[
							{ key: 'event', label: 'Event' },
							{ key: 'status', label: 'Status' },
							{ key: 'attempts', label: 'Attempts', align: 'right' },
							{ key: 'latency', label: 'Latency', align: 'right' },
							{ key: 'error', label: 'Error' },
							{ key: 'time', label: 'Time' }
						]}
					>
						{#snippet cell(d, col)}
							{#if col.key === 'event'}
								<span class="mono">{short(d.event)}</span>
							{:else if col.key === 'status'}
								{#if d.attempts}{@render outcome(d)}{:else}<span class="dim">pending</span>{/if}
							{:else if col.key === 'attempts'}
								<span class="mono">{d.attempts}</span>
							{:else if col.key === 'latency'}
								<span class="mono">{d.latency_ms === null ? '-' : `${d.latency_ms} ms`}</span>
							{:else if col.key === 'error'}
								<span class="fg2" title={d.error ?? ''} style="display:block;max-width:320px;overflow:hidden;text-overflow:ellipsis">{d.error ?? ''}</span>
							{:else}
								<span class="mono dim" title={new Date(d.created_at * 1000).toLocaleString()}>{ago(d.created_at * 1000)}</span>
							{/if}
						{/snippet}
					</DataTable>
				{/if}
			</section>
		{/if}
	</div>

	<Drawer bind:open={drawer} title={editing ? `Edit webhook #${editing.id}` : 'Add webhook'}>
		<form class="stack" onsubmit={submit}>
			<label class="field">
				<span>URL</span>
				<input class="inp mono" type="url" required maxlength="500" placeholder="https://example.com/serenity" autocomplete="off" bind:value={url} />
				<small class="hint">https only, on the public internet.</small>
			</label>
			<div class="field" role="group" aria-label="Events">
				<span>Events</span>
				<div style="display:flex;flex-wrap:wrap;gap:6px">
					<button type="button" class="chip" aria-pressed={!events.length} onclick={() => (events = [])}>All events</button>
					{#each data.events as e (e)}
						<button
							type="button"
							class="chip"
							aria-pressed={events.includes(e)}
							onclick={() => (events = events.includes(e) ? events.filter((x) => x !== e) : [...events, e])}>{short(e)}</button
						>
					{/each}
				</div>
				<small class="hint">With all events, ones added later are sent too.</small>
			</div>
			<div class="field">
				<span>Tickets</span>
				<Select label="Ticket type" bind:value={scope} options={[{ value: '', label: 'All types' }, ...types.map((t) => ({ value: String(t.id), label: t.name }))]} />
			</div>
			<div class="rowline">
				<span class="hint">{editing ? 'The secret stays the same.' : 'The signing secret is shown once, next.'}</span>
				<button class="btn pri" type="submit" disabled={busy || !url.trim()}>{busy ? 'Checking…' : editing ? 'Save' : 'Add webhook'}</button>
			</div>
		</form>
	</Drawer>
{/if}

<Modal bind:open={confirmOpen} title={confirm?.kind === 'remove' ? `Remove webhook #${confirm.hook.id}?` : `Rotate the secret of webhook #${confirm?.hook.id}?`}>
	<p>
		{#if confirm?.kind === 'remove'}
			Ticket events stop going to <span class="mono">{confirm.hook.url}</span>, and its delivery log is deleted.
		{:else}
			The current secret stops working at once. Update your receiver with the new one, shown next.
		{/if}
	</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (confirmOpen = false)}>Cancel</button>
		<button class="btn danger" onclick={confirmed}>{confirm?.kind === 'remove' ? 'Remove webhook' : 'Rotate secret'}</button>
	{/snippet}
</Modal>

<Modal bind:open={secretOpen} title="Signing secret for webhook #{secret?.id}">
	<p>Shown only now. Each POST carries <span class="mono">X-Serenity-Timestamp</span> and <span class="mono">X-Serenity-Signature: sha256=&lt;hex&gt;</span>, the HMAC-SHA256 of <span class="mono">&lt;timestamp&gt;.&lt;body&gt;</span> keyed with this secret.</p>
	<div style="display:flex;gap:8px;margin-bottom:16px">
		<input class="inp mono" style="flex:1" readonly aria-label="Signing secret" value={secret?.value ?? ''} onfocus={(e) => e.currentTarget.select()} />
		<button class="btn" onclick={copy}><Icon name="key" />Copy</button>
	</div>
	{#snippet actions()}
		<button class="btn pri" onclick={() => (secretOpen = false)}>Done</button>
	{/snippet}
</Modal>
