<script lang="ts">
	import { api, errorMessage, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import Avatar from '../../components/Avatar.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Modal from '../../components/Modal.svelte';
	import Segmented from '../../components/Segmented.svelte';
	import Select from '../../components/Select.svelte';
	import { palette } from '../../palette.svelte';
	import { session } from '../../session.svelte';
	import { toast } from '../../toast.svelte';
	import { ago, isTyping } from '../../util';
	import {
		PRIORITIES,
		channelUrl,
		eventText,
		stageColor,
		typeLabel,
		who,
		type Detail,
		type Message,
		type Priority,
		type TicketType,
		type TicketUser
	} from './queue';

	// The split view's right pane (a bottom sheet on phones). Every action goes through the bot,
	// answers with the ticket's fresh detail, and is passed up with onchange so lists can reload.
	// Keys while it's open: c claim/unclaim, n next stage, e escalate, a mod action.
	let {
		guildId,
		number,
		types,
		sheet = false,
		onclose,
		onchange,
		onopen
	}: {
		guildId: string;
		number: number | null;
		types: TicketType[];
		sheet?: boolean;
		onclose: () => void;
		onchange: (d: Detail) => void;
		onopen: (n: number) => void;
	} = $props();

	let detail = $state<Detail | null>(null);
	let messages = $state<Message[] | null>(null);
	let messagesError = $state('');
	let busy = $state(false);
	let dialog = $state<'escalate' | 'deescalate' | 'close' | 'mod' | null>(null);
	let reason = $state('');
	let tier = $state<string | null>(null);
	let modUser = $state<string | null>(null);
	let modAction = $state('warn');
	let duration = $state('1h');
	let note = $state('');
	let editing = $state(false);
	let answers = $state<Record<string, string>>({});
	let assignees = $state<TicketUser[]>([]);

	const isOpen = $derived(detail?.status === 'open');
	const tiers = $derived(detail?.type.tiers ?? []);
	const atBottom = $derived(!detail?.tier_id || detail.tier_id === tiers[0]?.id);
	const stages = $derived(detail?.type.stages ?? []);
	const typeName = (id: number) => types.find((t) => t.id === id)?.name ?? 'Ticket';

	let detailFor = 0; // the newest load wins if answers arrive out of order
	$effect(() => {
		const n = number;
		const id = guildId;
		const req = ++detailFor;
		detail = null;
		messages = null;
		editing = false;
		dialog = null;
		if (n === null) return;
		api<Detail>(`/guilds/${id}/tickets/${n}`).then(
			(d) => req === detailFor && (detail = d),
			(e) => req === detailFor && (toast(errorMessage(e), { error: true }), onclose())
		);
		loadMessages(id, n);
	});

	let messagesFor = 0;
	async function loadMessages(id: string, n: number) {
		const req = ++messagesFor;
		try {
			const m = await api<Message[]>(`/guilds/${id}/tickets/${n}/messages`);
			if (req === messagesFor) (messages = m), (messagesError = '');
		} catch (e) {
			if (req === messagesFor) (messages = []), (messagesError = errorMessage(e));
		}
	}

	async function searchAssignees(q = '') {
		if (!detail) return;
		const n = detail.number;
		try {
			const r = await api<TicketUser[]>(`/guilds/${guildId}/tickets/${n}/assignees?query=${encodeURIComponent(q)}`);
			if (detail?.number === n) assignees = r;
		} catch {
			assignees = [];
		}
	}
	$effect(() => {
		if (detail?.can.reassign && isOpen) searchAssignees();
	});

	/** Run one action on the shown ticket; resolves to its new detail, or null if it failed (toasted). */
	async function act(path: string, body: unknown = {}, method = 'POST'): Promise<Detail | null> {
		if (!detail || busy) return null;
		const n = detail.number;
		busy = true;
		const r = await save(() => send<Detail>(method, `/guilds/${guildId}/tickets/${n}/${path}`, body));
		busy = false;
		if (!r) return null;
		onchange(r);
		if (detail?.number === n) {
			detail = r;
			loadMessages(guildId, n);
		}
		return r;
	}

	function openDialog(kind: NonNullable<typeof dialog>) {
		if (!detail) return;
		reason = '';
		if (kind === 'escalate') {
			const i = tiers.findIndex((t) => t.id === detail!.tier_id);
			tier = tiers[Math.max(0, i) + 1]?.name ?? null;
		}
		if (kind === 'mod') {
			modUser = detail.opener.id;
			modAction = detail.can.mod[0];
		}
		dialog = kind;
	}

	async function claimToggle() {
		if (!detail) return;
		if (detail.assignee?.id === session.me?.id) {
			if (await act('unclaim')) toast(`Unclaimed #${detail.number}`);
		} else if (await act('claim')) toast(`Claimed #${detail.number}`);
	}

	async function moveStage(name: string) {
		const r = await act('stage', { stage: name });
		if (!r) return;
		toast(`#${r.number} moved to ${name}`);
		if (r.type.stages.find((s) => s.name === name)?.terminal) {
			reason = name; // as the "Close as …" button in Discord
			dialog = 'close';
		}
	}

	function nextStage() {
		const i = stages.findIndex((s) => s.name === detail?.stage);
		if (stages[i + 1]) moveStage(stages[i + 1].name);
	}

	async function setPriority(p: Priority, previous: Priority) {
		const n = detail?.number;
		const r = await act('priority', { priority: p });
		if (!r) {
			if (detail?.number === n) detail!.priority = previous; // not another ticket opened meanwhile
		} else if (r.renaming) toast('Priority saved; Discord renames the channel within 10 minutes.');
	}

	async function submit(e: Event) {
		e.preventDefault();
		const kind = dialog;
		const r =
			kind === 'escalate'
				? await act('escalate', { tier, reason })
				: kind === 'deescalate'
					? await act('deescalate', { reason })
					: kind === 'close'
						? await act('close', { reason })
						: await act('mod', { user_id: modUser, action: modAction, reason, duration });
		if (!r) return;
		dialog = null;
		if (kind === 'mod') toast(`Case #${r.case}: ${modAction}`);
		else if (kind === 'close') toast(`Closed #${r.number}`);
	}

	async function addNote(e: Event) {
		e.preventDefault();
		if (note.trim() && (await act('notes', { text: note }))) note = '';
	}

	function editFields() {
		if (!detail) return;
		answers = Object.fromEntries(detail.type.fields.map((f) => [f.key, detail!.fields[f.key] ?? '']));
		editing = true;
	}

	async function saveFields(e: Event) {
		e.preventDefault();
		if (await act('fields', { fields: answers }, 'PATCH')) editing = false;
	}

	function onkeydown(e: KeyboardEvent) {
		if (!detail || !isOpen || busy || dialog || isTyping(e) || palette.open || e.metaKey || e.ctrlKey || e.altKey) return;
		if (e.key === 'c') claimToggle();
		else if (e.key === 'n') nextStage();
		else if (e.key === 'e' && tiers.length) openDialog('escalate');
		else if (e.key === 'a' && detail.can.mod.length) openDialog('mod');
		else return;
		e.preventDefault();
	}

	const when = (ts: number) => new Date(ts * 1000).toLocaleString();
</script>

<svelte:window {onkeydown} />

<aside class="pane detail" class:open={sheet && !!detail} aria-label="Ticket detail" aria-busy={busy}>
	{#if detail}
		{@const d = detail}
		{@const url = channelUrl(guildId, d)}
		<div class="dhead">
			<span class="prio" data-p={d.priority}>{d.priority}</span>
			<h2>#{d.number}</h2>
			<span class="dim" style="font-size:13px">{typeLabel(d.type)}</span>
			{#if d.off_hours}<span class="tag">off-hours</span>{/if}
			<span style="flex:1"></span>
			{#if url}<a class="btn sm ghost" href={url} target="_blank" rel="noopener" aria-label="Open in Discord"><Icon name="ext" /></a>{/if}
			<button class="btn sm ghost" aria-label="Close ticket detail" onclick={onclose}><Icon name="x" /></button>
		</div>

		{#if isOpen}
			<div class="dsec acts">
				<button class="btn sm" disabled={busy} onclick={claimToggle}>
					{d.assignee?.id === session.me?.id ? 'Unclaim' : 'Claim'} <kbd>c</kbd>
				</button>
				{#if tiers.length}<button class="btn sm" disabled={busy} onclick={() => openDialog('escalate')}>Escalate <kbd>e</kbd></button>{/if}
				{#if !atBottom}<button class="btn sm" disabled={busy} onclick={() => openDialog('deescalate')}>Hand back</button>{/if}
				{#if d.can.mod.length}<button class="btn sm" disabled={busy} onclick={() => openDialog('mod')}>Mod action <kbd>a</kbd></button>{/if}
				<span style="flex:1"></span>
				<button class="btn sm danger" disabled={busy} onclick={() => openDialog('close')}>Close</button>
			</div>
		{:else}
			<div class="dsec"><p class="hint">Closed {d.closed_at ? ago(d.closed_at * 1000) : ''} ago · {d.close_reason ?? 'no reason given'}</p></div>
		{/if}

		<dl class="dgrid">
			<div><dt>Opener</dt><dd class="who"><Avatar name={who(d.opener)} src={d.opener.avatar_url} size="xs" />{who(d.opener)}</dd></div>
			<div><dt>Status</dt><dd>waiting on {d.waiting_on === 'staff' ? 'staff' : 'member'}</dd></div>
			<div>
				<dt>Claimed by</dt>
				<dd>
					{#if d.can.reassign && isOpen}
						<Select
							label="Reassign ticket"
							value={d.assignee?.id ?? null}
							placeholder="unclaimed"
							options={[...(d.assignee ? [d.assignee] : []), ...assignees.filter((u) => u.id !== d.assignee?.id)].map((u) => ({ value: u.id, label: who(u) }))}
							onsearch={searchAssignees}
							onchange={(id) => act('assign', { user_id: id })}
						/>
					{:else}{d.assignee ? who(d.assignee) : 'unclaimed'}{/if}
				</dd>
			</div>
			<div>
				<dt>Tier</dt>
				<dd>
					{#if tiers.length}
						<span class="ladder">{#each tiers as t, i (t.id)}{#if i}<span class="dim">›</span>{/if}<span class:cur={t.id === d.tier_id}>{t.name}</span>{/each}</span>
					{:else}-{/if}
				</dd>
			</div>
			<div><dt>Opened</dt><dd title={when(d.opened_at)}>{ago(d.opened_at * 1000)} ago</dd></div>
			<div><dt>Last activity</dt><dd title={when(d.last_activity_at)}>{ago(d.last_activity_at * 1000)} ago</dd></div>
			{#if stages.length}
				<div>
					<dt>Stage <kbd>n</kbd></dt>
					<dd>
						{#if isOpen}
							<Select label="Stage" value={d.stage} options={stages.map((s) => ({ value: s.name, label: s.name, hint: s.terminal ? 'closes' : undefined }))} onchange={moveStage} />
						{:else}<span style:color={stageColor(stages.find((s) => s.name === d.stage))}>{d.stage ?? '-'}</span>{/if}
					</dd>
				</div>
			{/if}
			{#if isOpen}
				<div style="grid-column:1/-1">
					<dt>Priority</dt>
					<dd><Segmented label="Priority" bind:value={detail.priority} options={PRIORITIES.map((p) => ({ value: p, label: p }))} onchange={setPriority} /></dd>
				</div>
			{/if}
		</dl>

		{#if d.type.fields.length}
			<div class="dsec">
				<h3>
					Answers
					{#if isOpen && !editing}<button class="btn sm ghost" style="margin-left:auto" onclick={editFields}><Icon name="edit" />Edit</button>{/if}
				</h3>
				{#if editing}
					<form class="stack" style="gap:10px" onsubmit={saveFields}>
						{#each d.type.fields as f (f.key)}
							<label class="field">
								<span>{f.label}{f.staff_only ? ' (staff only)' : ''}{f.required ? '' : ' · optional'}</span>
								{#if f.type === 'select'}
									<select class="inp" bind:value={answers[f.key]}>
										{#if !f.required}<option value="">-</option>{/if}
										{#each f.options as o (o)}<option value={o}>{o}</option>{/each}
									</select>
								{:else if f.type === 'paragraph'}
									<textarea class="inp" rows="3" maxlength="1000" bind:value={answers[f.key]}></textarea>
								{:else}
									<input class="inp" maxlength="100" bind:value={answers[f.key]} placeholder={f.type === 'user' ? 'Member id' : f.type === 'date' ? '2026-01-31' : ''} />
								{/if}
							</label>
						{/each}
						<div class="rowline" style="justify-content:flex-end;gap:6px">
							<button type="button" class="btn sm" onclick={() => (editing = false)}>Cancel</button>
							<button class="btn sm pri" type="submit" disabled={busy}>Save answers</button>
						</div>
					</form>
				{:else}
					<dl class="answers">
						{#each d.type.fields as f (f.key)}
							<div><dt>{f.label}{f.staff_only ? ' · staff' : ''}</dt><dd>{d.fields[f.key] || '-'}</dd></div>
						{/each}
					</dl>
				{/if}
			</div>
		{/if}

		<div class="dsec">
			<h3>Conversation <span class="cnt">latest {messages?.length ?? ''}</span></h3>
			{#if messages === null}
				<p class="hint">Loading…</p>
			{:else if messagesError}
				<p class="hint">{messagesError}</p>
			{:else if !messages.length}
				<p class="hint">{isOpen ? 'No messages yet.' : 'The channel is gone; its messages are in the transcript.'}</p>
			{:else}
				<ol class="msgs">
					{#each messages as m (m.id)}
						<li>
							<Avatar name={m.author.name} src={m.author.avatar} size="xs" />
							<div>
								<div class="mh"><b>{m.author.name}</b>{#if m.author.bot}<span class="tag">bot</span>{/if}<span class="dim mono" title={when(m.timestamp)}>{ago(m.timestamp * 1000)}</span></div>
								{#if m.content}<p>{m.content}</p>{/if}
								{#each m.embeds as em, i (i)}<p class="embed">{em.title ?? ''}{em.title && em.description ? ' · ' : ''}{em.description ?? ''}</p>{/each}
								{#each m.attachments as a (a.url)}<a class="linkish" href={a.url} target="_blank" rel="noopener">📎 {a.filename}</a>{/each}
							</div>
						</li>
					{/each}
				</ol>
			{/if}
		</div>

		<div class="dsec">
			<h3>Handlers</h3>
			{#if d.handlers.length}
				<ul class="mini">
					{#each d.handlers as h (h.user.id)}
						<li><Avatar name={who(h.user)} src={h.user.avatar_url} size="xs" />{who(h.user)}<span class="dim">{h.role}</span><span class="mono dim" style="margin-left:auto">{h.messages} msg</span></li>
					{/each}
				</ul>
			{:else}<p class="hint">Nobody yet.</p>{/if}
		</div>

		{#if d.transcripts}
			<div class="dsec">
				<h3>Notes <span class="cnt">{d.notes.length}</span></h3>
				<ul class="mini">
					{#each d.notes as n, i (i)}
						<li style="align-items:flex-start"><b style="flex:none">{who(n.author)}</b><span style="white-space:pre-wrap">{n.body}</span><span class="mono dim" style="margin-left:auto">{ago(n.created_at * 1000)}</span></li>
					{/each}
				</ul>
				{#if isOpen}
					<form class="rowline" style="margin-top:8px" onsubmit={addNote}>
						<label class="sr" for="noteIn">Add a staff note</label>
						<input class="inp" id="noteIn" maxlength="2000" placeholder="Private note, kept in the transcript" bind:value={note} />
						<button class="btn sm" type="submit" disabled={busy || !note.trim()}>Add</button>
					</form>
				{/if}
			</div>
		{/if}

		{#if d.previous.length || d.linked.length}
			<div class="dsec">
				<h3>Context</h3>
				<ul class="hist">
					{#each d.linked as t (t.number)}
						<li><button onclick={() => onopen(t.number)}><span class="mono">#{t.number}</span><span class="dim">linked</span><span class="fg2">{typeName(t.type_id)} · {who(t.opener)} · same {t.linked_by?.join(', ')}</span><span class="mono dim">{t.status}</span></button></li>
					{/each}
					{#each d.previous as t (t.number)}
						<li><button onclick={() => onopen(t.number)}><span class="mono">#{t.number}</span><span class="dim">earlier</span><span class="fg2">{typeName(t.type_id)}</span><span class="mono dim">{t.status} {ago((t.closed_at ?? t.opened_at) * 1000)}</span></button></li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if d.moderation}
			<div class="dsec">
				<h3>Moderation record</h3>
				{#each d.moderation as r (r.user.id)}
					<div class="rec">
						<div class="rowline"><span>{who(r.user)}</span><span class="mono dim">{r.warnings} active warning{r.warnings === 1 ? '' : 's'}</span></div>
						{#each r.cases as c (c.number)}
							<div class="mini-case"><span class="mono">#{c.number}</span><span class="act" data-a={c.action}>{c.action}</span><span class="fg2">{c.reason ?? 'No reason'}</span><span class="mono dim">{ago(c.created_at * 1000)}</span></div>
						{/each}
					</div>
				{/each}
			</div>
		{/if}

		<div class="dsec">
			<h3>History</h3>
			<ul class="mini">
				{#each d.events as e, i (i)}
					<li><span class="mono dim" style="width:34px;flex:none">{ago(e.created_at * 1000)}</span><span>{e.actor ? who(e.actor) : 'Serenity'} {eventText(e, d.type)}</span></li>
				{/each}
			</ul>
		</div>
	{:else if number !== null}
		<EmptyState>Loading…</EmptyState>
	{:else}
		<EmptyState>Select a ticket to see it here.</EmptyState>
	{/if}
</aside>

{#if sheet && detail}<div class="scrim sheet-scrim" onclick={onclose} aria-hidden="true"></div>{/if}

{#if detail}
	{@const d = detail}
	<Modal
		bind:open={() => dialog !== null, (v) => !v && (dialog = null)}
		title={dialog === 'escalate' ? `Escalate #${d.number}` : dialog === 'deescalate' ? `Hand #${d.number} back` : dialog === 'close' ? `Close #${d.number}?` : `Mod action from #${d.number}`}
	>
		<form id="ticketAct" class="stack" style="gap:12px" onsubmit={submit}>
			{#if dialog === 'escalate'}
				<div class="field"><span>Tier</span>
					<Select label="Tier" value={tier} options={tiers.filter((t) => t.id !== d.tier_id).map((t) => ({ value: t.name, label: t.name }))} onchange={(v) => (tier = v)} />
				</div>
			{:else if dialog === 'mod'}
				<div class="field"><span>Member</span>
					<Select label="Member" value={modUser} options={(d.moderation ?? [{ user: d.opener }]).map((r) => ({ value: r.user.id, label: who(r.user) }))} onchange={(v) => (modUser = v)} />
				</div>
				<div class="field"><span>Action</span><Segmented label="Action" bind:value={modAction} options={d.can.mod.map((a) => ({ value: a, label: a }))} /></div>
				{#if modAction === 'timeout'}
					<label class="field"><span>Duration</span><input class="inp" maxlength="10" placeholder="30m, 2h or 7d" bind:value={duration} /></label>
				{/if}
			{/if}
			<label class="field">
				<span>{dialog === 'deescalate' ? 'Reason (posted for the tier taking it back)' : dialog === 'escalate' ? 'Reason (shown to the tier)' : 'Reason (optional)'}</span>
				<textarea class="inp" rows="2" maxlength="500" bind:value={reason}></textarea>
			</label>
			{#if dialog === 'close'}<p class="hint">The channel is deleted and the opener gets the transcript by DM.</p>{/if}
			{#if dialog === 'mod'}<p class="hint">The case links to this ticket and keeps its transcript as evidence.</p>{/if}
		</form>
		{#snippet actions()}
			<button class="btn" onclick={() => (dialog = null)}>Cancel</button>
			<button class="btn {dialog === 'close' || modAction === 'ban' ? 'danger' : 'pri'}" type="submit" form="ticketAct" disabled={busy}>
				{dialog === 'escalate' ? 'Escalate' : dialog === 'deescalate' ? 'Hand back' : dialog === 'close' ? 'Close ticket' : modAction[0].toUpperCase() + modAction.slice(1)}
			</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.acts {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		align-items: center;
	}
	.who {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.ladder {
		display: inline-flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.ladder .cur {
		color: var(--acc);
	}
	.answers {
		display: grid;
		gap: 8px;
		margin: 0;
	}
	.answers dt {
		font-size: 11.5px;
		color: var(--fg3);
	}
	.answers dd {
		margin: 0;
		font-size: 13px;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.msgs {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 10px;
		max-height: 340px;
		overflow: auto;
		overscroll-behavior: contain;
	}
	.msgs li {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 8px;
		font-size: 13px;
	}
	.msgs p {
		margin: 2px 0 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.mh {
		display: flex;
		gap: 6px;
		align-items: baseline;
	}
	.embed {
		color: var(--fg2);
		border-left: 2px solid var(--line3);
		padding-left: 8px;
	}
	.mini {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 6px;
		font-size: 12.5px;
	}
	.mini li {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.rec + .rec {
		margin-top: 10px;
	}
	.rec .rowline {
		font-size: 13px;
	}
	.mini-case {
		display: grid;
		grid-template-columns: 40px 70px 1fr auto;
		gap: 8px;
		align-items: center;
		font-size: 12.5px;
		margin-top: 4px;
	}
	.mini-case .fg2 {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.tag {
		font-family: var(--mono);
		font-size: 10.5px;
		color: var(--lilac);
		border: 1px solid currentColor;
		border-radius: 4px;
		padding: 0 4px;
	}
	.prio {
		font-family: var(--mono);
		font-size: 11px;
		line-height: 18px;
		padding: 0 6px;
		border-radius: 4px;
		border: 1px solid currentColor;
		color: var(--fg3);
	}
	.prio[data-p='high'] {
		color: var(--warn);
	}
	.prio[data-p='urgent'] {
		color: var(--red);
	}
	form.rowline .inp {
		flex: 1;
	}
</style>
