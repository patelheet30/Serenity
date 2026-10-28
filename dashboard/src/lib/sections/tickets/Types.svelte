<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import DataTable from '../../components/DataTable.svelte';
	import Drawer from '../../components/Drawer.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Modal from '../../components/Modal.svelte';
	import Segmented from '../../components/Segmented.svelte';
	import Select from '../../components/Select.svelte';
	import Toggle from '../../components/Toggle.svelte';
	import { toast } from '../../toast.svelte';
	import type { TicketPageProps } from '../Tickets.svelte';

	type Kind = 'short' | 'paragraph' | 'number' | 'date' | 'select' | 'url' | 'user' | 'pattern';
	type Field = { key: string; label: string; type: Kind; required: boolean; identity?: boolean; staff_only?: boolean; options?: string[]; pattern?: string };
	type Stage = { name: string; color: number | null; terminal: boolean; auto_close: boolean };
	type TicketType = {
		id: number;
		name: string;
		emoji: string | null;
		description: string | null;
		category_id: string | null;
		staff_role_ids: string[];
		fields: Field[];
		stages: Stage[];
		claim_mode: 'reply' | 'button' | 'both';
		open_limit: number;
		name_template: string;
		transcripts: boolean;
		retention_days: number | null;
		auto_unclaim_hours: number | null;
		ratings: boolean;
		auto_close_hours: number | null;
		hours: string | null;
		open_off_hours: boolean;
	};
	type Role = { id: string; name: string; managed: boolean };
	// The editor's copy of a field: dropdown options as the comma list the admin types.
	type DraftField = Omit<Field, 'key' | 'options' | 'identity' | 'staff_only' | 'pattern'> & {
		key?: string;
		options: string;
		identity: boolean;
		staff_only: boolean;
		pattern: string;
	};
	type DraftStage = Omit<Stage, 'color'> & { color: string | null };

	let { guildId, channels, onerror }: TicketPageProps = $props();

	// The bot's limits (ticket_service.py): a modal holds 5 inputs; dropdown and member questions
	// are asked with select menus after it, at most 4.
	const MAX_FIELDS = 5;
	const MAX_PICKED = 4;
	const MAX_STAGES = 25;
	const KINDS: { value: Kind; label: string }[] = [
		{ value: 'short', label: 'Short text' },
		{ value: 'paragraph', label: 'Paragraph' },
		{ value: 'number', label: 'Number' },
		{ value: 'date', label: 'Date' },
		{ value: 'select', label: 'Dropdown' },
		{ value: 'url', label: 'Link' },
		{ value: 'user', label: 'Member' },
		{ value: 'pattern', label: 'Pattern (regex)' }
	];
	const CLAIM = [
		{ value: 'reply' as const, label: 'First reply' },
		{ value: 'button' as const, label: 'Button' },
		{ value: 'both' as const, label: 'Both' }
	];

	let types = $state<TicketType[] | null>(null);
	let roles: Role[] = $state([]);
	let open = $state(false);
	let cur = $state<TicketType | null>(null); // as saved
	let d = $state<TicketType | null>(null); // as edited
	let fields: DraftField[] = $state([]);
	let stages: DraftStage[] = $state([]);
	let example = $state('');
	let confirmDelete = $state(false);
	let newName = $state('');
	let newRole = $state<string | null>(null);
	let addRole = $state<string | null>(null);

	$effect(() => {
		const id = guildId;
		let stale = false;
		types = null;
		Promise.all([api<TicketType[]>(`/guilds/${id}/tickets/types/config`), api<Role[]>(`/guilds/${id}/roles`)]).then(
			([t, r]) => !stale && ((types = t), (roles = r.filter((x) => !x.managed && x.id !== id))),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	const categories = $derived(channels.filter((c) => c.type === 'category'));
	const roleName = (id: string) => roles.find((r) => r.id === id)?.name ?? id;
	const hex = (c: number | null) => (c === null ? null : `#${c.toString(16).padStart(6, '0')}`);
	const label = (t: TicketType) => (t.emoji ? `${t.emoji} ${t.name}` : t.name);

	const toDraft = (f: Field): DraftField => ({
		...f,
		options: f.options?.join(', ') ?? '',
		identity: !!f.identity,
		staff_only: !!f.staff_only,
		pattern: f.pattern ?? ''
	});
	const fromDraft = (f: DraftField) => ({ ...f, options: f.options.split(',').map((o) => o.trim()).filter(Boolean) });
	const draftStage = (s: Stage): DraftStage => ({ ...s, color: hex(s.color) });

	const fieldsDirty = $derived(!!cur && JSON.stringify(fields.map(fromDraft)) !== JSON.stringify(cur.fields.map(toDraft).map(fromDraft)));
	const stagesDirty = $derived(!!cur && JSON.stringify(stages) !== JSON.stringify(cur.stages.map(draftStage)));
	const picked = $derived(fields.filter((f) => !f.staff_only && (f.type === 'select' || f.type === 'user')).length);

	function edit(t: TicketType) {
		cur = t;
		d = structuredClone($state.snapshot(t));
		fields = t.fields.map(toDraft);
		stages = t.stages.map(draftStage);
		open = true;
	}

	function saved(t: TicketType) {
		types = types!.map((x) => (x.id === t.id ? t : x));
		cur = t;
	}

	/** Save some settings; on failure the edited copy goes back to what's saved. */
	async function patch<K extends keyof TicketType>(...keys: K[]) {
		const t = cur!;
		const body = Object.fromEntries(keys.map((k) => [k, d![k] ?? null])); // a cleared number is undefined
		const r = await save(() => send<TicketType>('PATCH', `/guilds/${guildId}/tickets/types/${t.id}`, body));
		if (r) {
			saved(r);
			for (const k of keys) d![k] = r[k];
		} else if (d && cur === t) for (const k of keys) d[k] = t[k];
		return r;
	}

	async function saveFields() {
		const r = await save(() =>
			send<TicketType>('PATCH', `/guilds/${guildId}/tickets/types/${cur!.id}`, { fields: fields.map(fromDraft) })
		);
		if (r) {
			saved(r);
			fields = r.fields.map(toDraft);
			toast('Form saved');
		}
	}

	async function saveStages() {
		const r = await save(() => send<TicketType>('PATCH', `/guilds/${guildId}/tickets/types/${cur!.id}`, { stages }));
		if (r) {
			saved(r);
			stages = r.stages.map(draftStage);
			toast('Workflow saved');
		}
	}

	function move<T>(list: T[], i: number, by: number) {
		const j = i + by;
		if (j < 0 || j >= list.length) return;
		[list[i], list[j]] = [list[j], list[i]];
	}

	async function create(e: SubmitEvent) {
		e.preventDefault();
		const r = await save(() =>
			send<TicketType>('POST', `/guilds/${guildId}/tickets/types`, { name: newName, staff_role_ids: [newRole] })
		);
		if (!r) return;
		types = [...types!, r];
		newName = '';
		newRole = null;
		edit(r);
	}

	async function remove() {
		const t = cur!;
		confirmDelete = false;
		const ok = await save(() => send<void>('DELETE', `/guilds/${guildId}/tickets/types/${t.id}`).then(() => true));
		if (!ok) return;
		types = types!.filter((x) => x.id !== t.id);
		open = false;
		toast(`Deleted ${t.name}`);
	}

	// The channel name example, from the bot's own renderer, 250 ms after the last keystroke.
	$effect(() => {
		if (!d || !open) return;
		const body = { template: d.name_template, type: d.name, fields: fields.filter((f) => f.key) };
		const t = setTimeout(async () => {
			try {
				example = (await send<{ name: string }>('POST', `/guilds/${guildId}/tickets/types/channel-name`, body)).name;
			} catch {
				example = '';
			}
		}, 250);
		return () => clearTimeout(t);
	});
</script>

{#if !types}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<section class="pane" aria-labelledby="ttT">
		<div class="ph"><h2 id="ttT">Ticket types</h2><span class="cnt">{types.length}</span></div>
		{#if types.length}
			<DataTable
				label="Ticket types"
				rows={types}
				key={(t) => String(t.id)}
				selected={open && cur ? String(cur.id) : null}
				onopen={edit}
				columns={[
					{ key: 'name', label: 'Type' },
					{ key: 'staff', label: 'Staff' },
					{ key: 'form', label: 'Form' },
					{ key: 'stages', label: 'Workflow' },
					{ key: 'edit', label: '', align: 'right' }
				]}
			>
				{#snippet cell(t, c)}
					{#if c.key === 'name'}
						<span style="font-weight:500">{label(t)}</span>
					{:else if c.key === 'staff'}
						<span class="fg2">{t.staff_role_ids.map((r) => `@${roleName(r)}`).join(', ') || 'Manage Server only'}</span>
					{:else if c.key === 'form'}
						<span class="mono dim">{t.fields.length ? `${t.fields.length} question${t.fields.length === 1 ? '' : 's'}` : 'none'}</span>
					{:else if c.key === 'stages'}
						<span class="dim">{t.stages.map((s) => s.name).join(' → ') || 'none'}</span>
					{:else}
						<button class="btn sm ghost" aria-label="Edit {t.name}" onclick={() => edit(t)}><Icon name="edit" />Edit</button>
					{/if}
				{/snippet}
			</DataTable>
		{:else}
			<EmptyState icon="tickets">No ticket types yet. Create one, then show it on a panel.</EmptyState>
		{/if}
		<form class="addrow" onsubmit={create}>
			<label class="field" style="flex:1;min-width:150px">
				<span>New type</span>
				<input class="inp" placeholder="e.g. Ban appeals" maxlength="80" bind:value={newName} />
			</label>
			<div class="field">
				<span>Staff role</span>
				<Select label="Staff role for the new type" placeholder="Choose role…" bind:value={newRole} options={roles.map((r) => ({ value: r.id, label: `@${r.name}` }))} />
			</div>
			<button class="btn pri" type="submit" disabled={!newName.trim() || !newRole}><Icon name="plus" />Create</button>
		</form>
	</section>
{/if}

<Drawer bind:open title={cur ? label(cur) : 'Ticket type'}>
	{#if d && cur}
		<div class="dsec sec stack">
			<h3>Basics</h3>
			<label class="field">
				<span>Name</span>
				<input class="inp" maxlength="80" bind:value={d.name} onchange={() => patch('name')} />
			</label>
			<div class="two">
				<label class="field">
					<span>Emoji</span>
					<input class="inp" placeholder="none" maxlength="64" bind:value={d.emoji} onchange={() => patch('emoji')} />
				</label>
				<div class="field">
					<span>Category</span>
					<Select
						label="Category for {cur.name} channels"
						placeholder="None"
						value={d.category_id ?? ''}
						options={[{ value: '', label: 'None (top level)' }, ...categories.map((c) => ({ value: c.id, label: c.name }))]}
						onchange={(v) => ((d!.category_id = v || null), patch('category_id'))}
					/>
				</div>
			</div>
			<label class="field">
				<span>Dropdown description</span>
				<input class="inp" placeholder="Shown in a dropdown panel" maxlength="100" bind:value={d.description} onchange={() => patch('description')} />
			</label>
			<div class="field">
				<span>Staff roles</span>
				<div class="chips">
					{#each d.staff_role_ids as r (r)}
						<button
							class="chip"
							aria-label="Remove @{roleName(r)}"
							onclick={() => ((d!.staff_role_ids = d!.staff_role_ids.filter((x) => x !== r)), patch('staff_role_ids'))}
						>@{roleName(r)} <Icon name="x" style="width:11px;height:11px" /></button>
					{/each}
					<Select
						label="Add a staff role"
						placeholder="Add role"
						bind:value={addRole}
						options={roles.filter((r) => !d!.staff_role_ids.includes(r.id)).map((r) => ({ value: r.id, label: `@${r.name}` }))}
						onchange={(v) => ((addRole = null), (d!.staff_role_ids = [...d!.staff_role_ids, v]), patch('staff_role_ids'))}
					/>
				</div>
				<p class="hint">They see and handle these tickets and are pinged when one opens. Open tickets keep their current access.</p>
			</div>
		</div>

		<div class="dsec sec stack">
			<h3>Opening</h3>
			<div class="field">
				<span>How staff take a ticket</span>
				<Segmented label="Claim mode" bind:value={d.claim_mode} options={CLAIM} onchange={() => patch('claim_mode')} />
			</div>
			<div class="two">
				<label class="field">
					<span>Open per member</span>
					<input class="inp" type="number" min="1" max="10" bind:value={d.open_limit} onchange={() => patch('open_limit')} />
				</label>
				<label class="field">
					<span>Unclaim after (hours)</span>
					<input class="inp" type="number" min="0" max="168" placeholder="never" bind:value={d.auto_unclaim_hours} onchange={() => patch('auto_unclaim_hours')} />
				</label>
			</div>
			<label class="field">
				<span>Support hours</span>
				<input class="inp mono" placeholder="the server’s hours" maxlength="200" bind:value={d.hours} onchange={() => patch('hours')} />
				<span class="hint">Own hours, like <span class="mono">mon-fri 09:00-17:00; sat 10:00-14:00</span>, in the server’s timezone. Blank uses the server’s.</span>
			</label>
			<div class="rowline">
				<span>Can open outside support hours</span>
				<Toggle label="Members can open {cur.name} tickets outside support hours" bind:checked={d.open_off_hours} onchange={() => patch('open_off_hours')} />
			</div>
			<label class="field">
				<span>Auto-close after (support hours without the opener replying)</span>
				<input class="inp" type="number" min="0" max="720" placeholder="never" bind:value={d.auto_close_hours} onchange={() => patch('auto_close_hours')} />
			</label>
			<label class="field">
				<span>Channel name</span>
				<input class="inp mono" maxlength="90" bind:value={d.name_template} onchange={() => patch('name_template')} />
				<span class="hint">
					{'{type} {number} {user} {field:key}'} · e.g. <span class="mono" style="color:var(--fg)">#{example || '…'}</span>
				</span>
			</label>
		</div>

		<div class="dsec sec stack">
			<h3>
				Form <span class="cnt">{fields.length}/{MAX_FIELDS} · {picked}/{MAX_PICKED} picked</span>
			</h3>
			{#each fields as f, i (i)}
				<div class="frow">
					<div class="two">
						<label class="field"><span>Question</span><input class="inp" maxlength="45" bind:value={f.label} /></label>
						<label class="field">
							<span>Answer</span>
							<select class="inp" bind:value={f.type}>
								{#each KINDS as k (k.value)}<option value={k.value}>{k.label}</option>{/each}
							</select>
						</label>
					</div>
					{#if f.type === 'select'}
						<label class="field"><span>Options, by commas (up to 25)</span><input class="inp" bind:value={f.options} /></label>
					{:else if f.type === 'pattern'}
						<label class="field"><span>Regular expression the whole answer must match</span><input class="inp mono" maxlength="200" bind:value={f.pattern} /></label>
					{/if}
					<div class="flags">
						<span class="rowline"><span>Required</span><Toggle label="{f.label || 'Field'} is required" bind:checked={f.required} disabled={f.staff_only} onblocked={() => toast('Staff-only fields are never asked, so they’re optional')} /></span>
						<span class="rowline"><span>Links tickets</span><Toggle label="Link tickets with the same answer to {f.label || 'this field'}" bind:checked={f.identity} /></span>
						<span class="rowline"><span>Staff only</span><Toggle label="{f.label || 'Field'} is filled in by staff" bind:checked={f.staff_only} /></span>
						<span class="sp"></span>
						<button class="btn sm ghost" aria-label="Move {f.label} up" disabled={i === 0} onclick={() => move(fields, i, -1)}>↑</button>
						<button class="btn sm ghost" aria-label="Move {f.label} down" disabled={i === fields.length - 1} onclick={() => move(fields, i, 1)}>↓</button>
						<button class="btn sm ghost" aria-label="Remove {f.label}" onclick={() => fields.splice(i, 1)}><Icon name="x" /></button>
					</div>
				</div>
			{:else}
				<p class="hint">No questions: members open the ticket straight away.</p>
			{/each}
			<div class="rowline">
				<button
					class="btn sm"
					disabled={fields.length >= MAX_FIELDS}
					title={fields.length >= MAX_FIELDS ? `A form holds at most ${MAX_FIELDS} questions` : undefined}
					onclick={() => fields.push({ label: '', type: 'short', required: true, options: '', identity: false, staff_only: false, pattern: '' })}><Icon name="plus" />Add question</button
				>
				{#if fieldsDirty}
					<span class="rowline">
						<button class="btn sm ghost" onclick={() => (fields = cur!.fields.map(toDraft))}>Discard</button>
						<button class="btn sm pri" onclick={saveFields}><Icon name="check" />Save form</button>
					</span>
				{/if}
			</div>
		</div>

		<div class="dsec sec stack">
			<h3>Workflow <span class="cnt">{stages.length}/{MAX_STAGES}</span></h3>
			{#each stages as s, i (i)}
				<div class="srow">
					<input
						type="color"
						class="swatch"
						aria-label="Colour of {s.name || 'stage'}"
						value={s.color ?? '#80848e'}
						oninput={(e) => (s.color = e.currentTarget.value)}
					/>
					<input class="inp" aria-label="Stage {i + 1} name" placeholder="Stage name" maxlength="50" bind:value={s.name} />
					<span class="rowline"><span class="hint">Ends</span><Toggle label="{s.name || 'Stage'} offers to close the ticket" bind:checked={s.terminal} /></span>
					<span class="rowline"><span class="hint">Auto-close</span><Toggle label="Quiet tickets at {s.name || 'this stage'} may close automatically" bind:checked={s.auto_close} /></span>
					<span class="rowline" style="gap:2px">
						<button class="btn sm ghost" aria-label="Move {s.name} up" disabled={i === 0} onclick={() => move(stages, i, -1)}>↑</button>
						<button class="btn sm ghost" aria-label="Move {s.name} down" disabled={i === stages.length - 1} onclick={() => move(stages, i, 1)}>↓</button>
						<button class="btn sm ghost" aria-label="Remove {s.name}" onclick={() => stages.splice(i, 1)}><Icon name="x" /></button>
					</span>
				</div>
			{:else}
				<p class="hint">No stages: tickets are just open or closed.</p>
			{/each}
			<p class="hint">New tickets start at the first stage. Tickets at a renamed or removed stage keep it until they’re moved.</p>
			<div class="rowline">
				<button
					class="btn sm"
					disabled={stages.length >= MAX_STAGES}
					onclick={() => stages.push({ name: '', color: null, terminal: false, auto_close: true })}><Icon name="plus" />Add stage</button
				>
				{#if stagesDirty}
					<span class="rowline">
						<button class="btn sm ghost" onclick={() => (stages = cur!.stages.map(draftStage))}>Discard</button>
						<button class="btn sm pri" onclick={saveStages}><Icon name="check" />Save workflow</button>
					</span>
				{/if}
			</div>
		</div>

		<div class="dsec sec stack">
			<h3>After closing</h3>
			<div class="rowline">
				<span>Keep transcripts</span>
				<Toggle label="Keep transcripts of {cur.name} tickets" bind:checked={d.transcripts} onchange={() => patch('transcripts')} />
			</div>
			<label class="field">
				<span>Keep transcripts for (days)</span>
				<input class="inp" type="number" min="0" max="365" placeholder="server default" disabled={!d.transcripts} bind:value={d.retention_days} onchange={() => patch('retention_days')} />
				<span class="hint">7–365, or 0 to keep them until deleted. Blank uses the server’s.</span>
			</label>
			<div class="rowline">
				<span>Ask openers for a rating</span>
				<Toggle label="Ask {cur.name} openers to rate closed tickets" bind:checked={d.ratings} onchange={() => patch('ratings')} />
			</div>
			<p class="hint">Escalation tiers are edited with the tier ladder in <a href="/dashboard/{guildId}/tickets/settings">Settings</a>.</p>
		</div>

		<div class="dsec sec rowline">
			<span class="hint">Only types that no panel shows and that never had a ticket can be deleted.</span>
			<button class="btn sm danger" onclick={() => (confirmDelete = true)}>Delete type</button>
		</div>
	{/if}
</Drawer>

<Modal bind:open={confirmDelete} title="Delete {cur?.name}?">
	<p>This can’t be undone.</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (confirmDelete = false)}>Cancel</button>
		<button class="btn danger" onclick={remove}>Delete</button>
	{/snippet}
</Modal>

<style>
	.sec {
		margin: 0 -14px;
	}
	.sec:first-child {
		margin-top: -14px;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		align-items: center;
	}
	.frow {
		display: grid;
		gap: 10px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: var(--r);
		background: var(--ink);
	}
	.flags {
		display: flex;
		gap: 6px 14px;
		flex-wrap: wrap;
		align-items: center;
		font-size: 12px;
		color: var(--fg2);
	}
	.flags .sp {
		flex: 1;
	}
	.srow {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 10px;
		align-items: center;
		padding-bottom: 8px;
		border-bottom: 1px solid var(--line);
	}
	.srow .inp {
		flex: 1 1 200px;
	}
	.swatch {
		width: 28px;
		height: 28px;
		padding: 0;
		border: 1px solid var(--line2);
		border-radius: var(--r);
		background: none;
	}
</style>
